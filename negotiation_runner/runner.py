"""NegotiationRunner — the core debate engine."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import statistics
import time
from collections import defaultdict
from typing import Optional

import anthropic

from db.schema import get_pool
from negotiation_runner.db import (
    load_agents,
    store_round_results,
    update_agent_state,
    update_simulation_status,
)
from negotiation_runner.models import (
    AgentState,
    RoundResult,
    RoundSummary,
    SimulationResult,
)
from negotiation_runner.prompts import (
    DEBATE_BATCH_PROMPT,
    ROUND_SUMMARY_PROMPT,
    build_agent_block,
)

logger = logging.getLogger(__name__)

NUM_ROUNDS = int(os.environ.get("SIMULATION_ROUNDS", "3"))
HIGH_UNCERTAINTY_THRESHOLD = 0.25


def _parse_json(raw: str) -> list[dict]:
    text = raw.strip()
    # Strip markdown fences (```json ... ``` or ``` ... ```)
    if text.startswith("```"):
        text = text.split("\n", 1)[1]
        if "```" in text:
            text = text[: text.rfind("```")]
        text = text.strip()
    # Try direct parse first
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # Fallback: find the JSON array within the text
    start = text.find("[")
    end = text.rfind("]")
    if start != -1 and end != -1 and end > start:
        return json.loads(text[start : end + 1])
    raise json.JSONDecodeError("No JSON array found", text, 0)


def _compute_summary_stats(agents: list[AgentState], round_number: int) -> RoundSummary:
    """Compute distribution statistics from current agent probabilities."""
    probs = [a.current_probability for a in agents]
    mean = statistics.mean(probs)
    median = statistics.median(probs)
    std = statistics.stdev(probs) if len(probs) > 1 else 0.0

    bull = sum(1 for p in probs if p > 0.6)
    bear = sum(1 for p in probs if p < 0.4)
    neutral = len(probs) - bull - bear

    return RoundSummary(
        round_number=round_number,
        mean_probability=round(mean, 4),
        median_probability=round(median, 4),
        std_dev=round(std, 4),
        min_probability=round(min(probs), 4),
        max_probability=round(max(probs), 4),
        bull_count=bull,
        bear_count=bear,
        neutral_count=neutral,
    )


class NegotiationRunner:
    """Orchestrates multi-round debate across 50 agents."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        database_url: Optional[str] = None,
        num_rounds: int = NUM_ROUNDS,
    ) -> None:
        self.client = anthropic.AsyncAnthropic(api_key=api_key)
        self._db_url = database_url
        self.num_rounds = num_rounds

    async def run(self, simulation_id: str, ticker: str, seed_summaries: list[str] | None = None) -> SimulationResult:
        """Execute a full negotiation simulation."""
        start = time.monotonic()
        pool = await get_pool(self._db_url)
        self._seed_context = "\n".join(f"- {s}" for s in seed_summaries) if seed_summaries else "(no seed data)"

        # Load agents
        agents = await load_agents(pool, simulation_id)
        if not agents:
            raise ValueError(f"No agents found for simulation {simulation_id}")

        logger.info(f"[runner] Starting negotiation: {len(agents)} agents, {self.num_rounds} rounds")
        await update_simulation_status(pool, simulation_id, "negotiating")

        agent_map = {a.id: a for a in agents}
        round_summaries: list[RoundSummary] = []

        for round_num in range(1, self.num_rounds + 1):
            round_start = time.monotonic()
            logger.info(f"[runner] === ROUND {round_num}/{self.num_rounds} ===")

            # 1. Compute distribution stats
            summary = _compute_summary_stats(agents, round_num)

            # 2. Generate round narrative via Haiku
            summary.narrative = await self._generate_narrative(
                ticker, round_num, summary, agents
            )
            logger.info(f"[runner] Round {round_num} narrative generated")

            # 3. Debate: batch by archetype, run in parallel
            archetype_groups: dict[str, list[AgentState]] = defaultdict(list)
            for a in agents:
                archetype_groups[a.archetype].append(a)

            debate_tasks = [
                self._debate_archetype_batch(
                    ticker, round_num, summary, archetype, group
                )
                for archetype, group in archetype_groups.items()
            ]
            batch_results = await asyncio.gather(*debate_tasks, return_exceptions=True)

            # 4. Collect results, update agent state
            all_round_results: list[RoundResult] = []
            for archetype, result in zip(archetype_groups.keys(), batch_results):
                if isinstance(result, Exception):
                    logger.error(f"[runner] Round {round_num} {archetype} failed: {result}")
                    continue
                for rr in result:
                    all_round_results.append(rr)
                    agent = agent_map.get(rr.agent_id)
                    if agent:
                        prev_prob = agent.current_probability
                        agent.current_probability = rr.probability
                        agent.conviction = max(0, min(1, agent.conviction + rr.conviction_delta))
                        agent.round_history.append({
                            "round": round_num,
                            "probability": rr.probability,
                            "reasoning": rr.reasoning,
                            "conviction_delta": rr.conviction_delta,
                            "prev_probability": prev_prob,
                        })

            # 5. Find biggest mover
            if all_round_results:
                for rr in all_round_results:
                    agent = agent_map.get(rr.agent_id)
                    if agent and agent.round_history:
                        prev = agent.round_history[-1].get("prev_probability", agent.initial_probability)
                        delta = abs(rr.probability - prev)
                        if delta > summary.biggest_move_delta:
                            summary.biggest_move_delta = round(delta, 4)
                            summary.biggest_mover = agent.name

            round_summaries.append(summary)

            # 6. Persist to Neon (parallel: round_results + agent updates)
            persist_tasks = [store_round_results(pool, simulation_id, all_round_results)]
            for a in agents:
                persist_tasks.append(update_agent_state(pool, a))
            await asyncio.gather(*persist_tasks)

            round_elapsed = (time.monotonic() - round_start) * 1000
            logger.info(
                f"[runner] Round {round_num} complete: mean={summary.mean_probability:.3f} "
                f"std={summary.std_dev:.3f} ({round_elapsed:.0f}ms)"
            )

        # Final stats
        final_summary = _compute_summary_stats(agents, self.num_rounds + 1)
        convergence = round(1.0 - final_summary.std_dev, 4)
        high_uncertainty = final_summary.std_dev > HIGH_UNCERTAINTY_THRESHOLD

        await update_simulation_status(pool, simulation_id, "complete")

        elapsed = (time.monotonic() - start) * 1000

        return SimulationResult(
            simulation_id=simulation_id,
            ticker=ticker,
            rounds_completed=self.num_rounds,
            final_mean_probability=final_summary.mean_probability,
            final_median_probability=final_summary.median_probability,
            final_std_dev=final_summary.std_dev,
            convergence_score=convergence,
            high_uncertainty=high_uncertainty,
            round_summaries=round_summaries,
            duration_ms=round(elapsed, 1),
        )

    async def _generate_narrative(
        self,
        ticker: str,
        round_number: int,
        summary: RoundSummary,
        agents: list[AgentState],
    ) -> str:
        """Generate a round narrative via Haiku."""
        # Compute movement note
        if round_number == 1:
            movement_note = "This is the opening round. Agents are stating initial positions."
        else:
            movers = []
            for a in agents:
                if len(a.round_history) >= 1:
                    prev = a.round_history[-1].get("prev_probability", a.current_probability)
                    delta = a.current_probability - prev
                    if abs(delta) > 0.02:
                        movers.append((a.name, a.archetype, delta))
            if movers:
                movers.sort(key=lambda x: abs(x[2]), reverse=True)
                top3 = movers[:3]
                movement_note = "Notable movements from last round:\n" + "\n".join(
                    f"  - {name} ({arch}): {delta:+.3f}" for name, arch, delta in top3
                )
            else:
                movement_note = "No significant movements from last round — positions are hardening."

        prompt = ROUND_SUMMARY_PROMPT.format(
            ticker=ticker,
            round_number=round_number,
            total_rounds=self.num_rounds,
            agent_count=len(agents),
            mean_prob=summary.mean_probability,
            median_prob=summary.median_probability,
            std_dev=summary.std_dev,
            min_prob=summary.min_probability,
            max_prob=summary.max_probability,
            bull_count=summary.bull_count,
            neutral_count=summary.neutral_count,
            bear_count=summary.bear_count,
            movement_note=movement_note,
        )

        message = await self.client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=256,
            messages=[{"role": "user", "content": prompt}],
        )
        return message.content[0].text.strip()

    async def _debate_archetype_batch(
        self,
        ticker: str,
        round_number: int,
        summary: RoundSummary,
        archetype: str,
        agents: list[AgentState],
    ) -> list[RoundResult]:
        """Run one Sonnet call for all agents of an archetype."""
        agent_blocks = "\n\n".join(build_agent_block(a) for a in agents)

        prompt = DEBATE_BATCH_PROMPT.format(
            ticker=ticker,
            round_number=round_number,
            total_rounds=self.num_rounds,
            seed_context=self._seed_context,
            round_narrative=summary.narrative,
            mean_prob=summary.mean_probability,
            median_prob=summary.median_probability,
            std_dev=summary.std_dev,
            bull_count=summary.bull_count,
            neutral_count=summary.neutral_count,
            bear_count=summary.bear_count,
            archetype=archetype,
            batch_size=len(agents),
            agent_blocks=agent_blocks,
        )

        message = await self.client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=6144,
            messages=[{"role": "user", "content": prompt}],
        )

        raw = message.content[0].text
        try:
            items = _parse_json(raw)
        except json.JSONDecodeError:
            logger.error(f"[runner] JSON parse failed for {archetype} round {round_number}: {raw[:200]}")
            return []

        agent_ids = {a.id for a in agents}
        results: list[RoundResult] = []
        for item in items:
            try:
                aid = item["agent_id"]
                if aid not in agent_ids:
                    logger.warning(f"[runner] Unknown agent_id in response: {aid}")
                    continue
                results.append(RoundResult(
                    agent_id=aid,
                    round_number=round_number,
                    probability=max(0.0, min(1.0, float(item["probability"]))),
                    reasoning=item["reasoning"],
                    conviction_delta=max(-0.2, min(0.2, float(item["conviction_delta"]))),
                ))
            except (KeyError, ValueError) as e:
                logger.warning(f"[runner] Skipping malformed round result: {e}")

        logger.info(f"[runner] {archetype}: {len(results)}/{len(agents)} responses parsed")
        return results
