"""Structural moderator for the Augur debate engine.

Runs between rounds to:
  1. Extract strongest bull/bear arguments from agent reasoning
  2. Challenge low-conviction outliers
  3. Flag high-conviction dissent for explicit consideration
  4. Track swing factors across rounds

Uses Claude Haiku — ~$0.01-0.02 per round moderation.
"""

from __future__ import annotations

import json
import logging
import re
import statistics
from dataclasses import dataclass, field
from typing import Optional

import anthropic

from negotiation_runner.models import AgentState, RoundResult

logger = logging.getLogger(__name__)


@dataclass
class ModeratorOutput:
    """Output from one round of moderation. Injected into next round's debate prompt."""

    round_number: int
    bull_arguments: list[str] = field(default_factory=list)
    bear_arguments: list[str] = field(default_factory=list)
    outlier_agent_ids: list[str] = field(default_factory=list)
    outlier_challenge: Optional[str] = None
    dissent_agent_ids: list[str] = field(default_factory=list)
    dissent_summary: Optional[str] = None
    swing_factors: list[str] = field(default_factory=list)
    moderator_brief: str = ""


ARGUMENT_EXTRACTION_PROMPT = """You are a debate moderator for an ASX earnings prediction simulation.

Ticker: {ticker}
Round just completed: {round_number}
Distribution: Mean={mean:.3f} Std={std:.3f}
Bulls={bulls} Neutral={neutral} Bears={bears}

Below are the reasoning statements from {agent_count} analyst agents this round.
Each is prefixed with their probability and conviction score.

{agent_reasoning}

Your tasks:

1. EXTRACT TOP ARGUMENTS
   Find the 3 strongest bull arguments and 3 strongest bear arguments.
   "Strongest" means: specific, evidence-grounded, cited by multiple high-conviction agents.
   Generic arguments ("macro uncertainty", "management is good") do not count.

2. IDENTIFY SWING FACTORS
   What are the top 3 data points that agents disagree most about?
   These are variables where bulls and bears interpret the same data differently. Be specific.

3. FLAG OUTLIER AGENTS
   List agent IDs that are >2 std devs from the mean AND have conviction < 0.4.
   These are weak-conviction extremists who may be anchored rather than reasoning.

4. FLAG HIGH-CONVICTION DISSENTERS
   List agent IDs whose conviction > 0.7 AND whose position differs from the mean by > 0.25.
   These are genuine contrarians whose reasoning deserves explicit attention.

Return ONLY a raw JSON object. No markdown code fences. No preamble. No explanation.
Start your response with {{ and end with }}.
The JSON must be complete and valid:
{{
  "bull_arguments": ["string", "string", "string"],
  "bear_arguments": ["string", "string", "string"],
  "swing_factors": ["string", "string", "string"],
  "outlier_agent_ids": ["uuid", ...],
  "outlier_challenge": "1-2 sentence challenge for outlier agents",
  "dissent_agent_ids": ["uuid", ...],
  "dissent_summary": "1 sentence summarising the high-conviction minority view"
}}"""


MODERATOR_BRIEF_TEMPLATE = """
=== MODERATOR BRIEF (Round {round_number} Analysis) ===

STRONGEST BULL CASE:
{bull_args}

STRONGEST BEAR CASE:
{bear_args}

KEY SWING FACTORS (what this debate hinges on):
{swing_factors}
{dissent_section}
In this round: Consider these arguments carefully.
Move only if you encounter a specific, evidence-grounded argument that challenges your current thesis.
Do not move simply because the group mean shifted."""

DISSENT_SECTION_TEMPLATE = """
HIGH-CONVICTION MINORITY VIEW:
{dissent_summary}
(Held by {count} agent(s) with strong conviction. Explicitly consider whether their evidence changes your view.)
"""


def _extract_partial_json(text: str) -> dict | None:
    """Attempts to salvage a truncated JSON response.

    Haiku sometimes truncates mid-JSON when it hits the token limit.
    Tries closing brace permutations, then regex-extracts complete fields.
    """
    # Strategy 1: try closing the JSON object
    for closing in [']}', '"}]}', '"]}', '"}', '}']:
        try:
            return json.loads(text + closing)
        except json.JSONDecodeError:
            pass

    # Strategy 2: extract individual complete fields via regex
    result: dict = {}

    # Extract string arrays like "key": ["a", "b"]
    array_pattern = re.compile(r'"(\w+)"\s*:\s*\[((?:[^[\]]*"[^"]*"[^[\]]*)*)\]')
    for match in array_pattern.finditer(text):
        key = match.group(1)
        try:
            val = json.loads(f"[{match.group(2)}]")
            result[key] = val
        except json.JSONDecodeError:
            pass

    # Extract string values like "key": "value"
    str_pattern = re.compile(r'"(\w+)"\s*:\s*"([^"]*)"')
    for match in str_pattern.finditer(text):
        key = match.group(1)
        if key not in result:
            result[key] = match.group(2)

    return result if result else None


class ModeratorAgent:
    """Structural moderator between debate rounds. Uses Haiku for cost efficiency."""

    def __init__(self, client: anthropic.AsyncAnthropic):
        self.client = client
        self._swing_factor_counts: dict[str, int] = {}

    async def moderate(
        self,
        ticker: str,
        round_number: int,
        agents: list[AgentState],
        round_results: list[RoundResult],
    ) -> ModeratorOutput:
        """Run moderation after a completed round. Returns ModeratorOutput."""
        if not agents or not round_results:
            return ModeratorOutput(round_number=round_number)

        agent_map = {a.id: a for a in agents}

        probs = [r.probability for r in round_results]
        mean = statistics.mean(probs)
        std = statistics.stdev(probs) if len(probs) > 1 else 0.0

        bull_count = sum(1 for p in probs if p > 0.6)
        bear_count = sum(1 for p in probs if p < 0.4)
        neutral_count = len(probs) - bull_count - bear_count

        # Build reasoning block — cap each at 150 chars to prevent prompt bloat
        reasoning_lines = []
        for r in sorted(round_results, key=lambda x: x.probability, reverse=True):
            agent = agent_map.get(r.agent_id)
            conv = agent.conviction if agent else 0.5
            arch = agent.archetype if agent else "?"
            reasoning_lines.append(
                f"[id={r.agent_id} P={r.probability:.3f} conv={conv:.2f} arch={arch}] {r.reasoning[:150]}"
            )

        prompt = ARGUMENT_EXTRACTION_PROMPT.format(
            ticker=ticker,
            round_number=round_number,
            mean=mean,
            std=std,
            bulls=bull_count,
            neutral=neutral_count,
            bears=bear_count,
            agent_count=len(round_results),
            agent_reasoning="\n".join(reasoning_lines),
        )

        try:
            response = await self.client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=2000,
                messages=[{"role": "user", "content": prompt}],
                timeout=60.0,
                stop_sequences=["```"],
            )
            text = response.content[0].text.strip()

            # Strip markdown fences if present
            if text.startswith("```"):
                lines = text.split("\n")
                text = "\n".join(lines[1:])
                if text.rstrip().endswith("```"):
                    text = text.rstrip()[:-3]
                text = text.strip()

            # Try direct parse
            try:
                data = json.loads(text)
            except json.JSONDecodeError:
                # Response may be truncated — try partial recovery
                data = _extract_partial_json(text)
                if data is None:
                    logger.warning(
                        f"[moderator] Round {round_number} JSON parse failed for {ticker} "
                        f"— response truncated at {len(text)} chars"
                    )
                    return ModeratorOutput(round_number=round_number)
                logger.info(
                    f"[moderator] Round {round_number} partial JSON recovery: "
                    f"extracted {len(data)} fields"
                )

        except Exception as e:
            logger.warning(f"[moderator] Round {round_number} extraction failed: {e}")
            return ModeratorOutput(round_number=round_number)

        # Update cumulative swing factor counts
        for sf in data.get("swing_factors", []):
            key = sf[:80].lower()
            self._swing_factor_counts[key] = self._swing_factor_counts.get(key, 0) + 1

        top_swings = sorted(self._swing_factor_counts.items(), key=lambda x: x[1], reverse=True)[:3]
        swing_factors = [k for k, _ in top_swings] or data.get("swing_factors", [])

        # Build dissent section
        dissent_ids = data.get("dissent_agent_ids", [])
        dissent_summary = data.get("dissent_summary", "")
        dissent_section = ""
        if dissent_ids and dissent_summary:
            dissent_section = DISSENT_SECTION_TEMPLATE.format(
                dissent_summary=dissent_summary, count=len(dissent_ids)
            )

        def _fmt(args: list[str]) -> str:
            return "\n".join(f"  {i+1}. {a}" for i, a in enumerate(args[:3])) or "  (none identified)"

        brief = MODERATOR_BRIEF_TEMPLATE.format(
            round_number=round_number,
            bull_args=_fmt(data.get("bull_arguments", [])),
            bear_args=_fmt(data.get("bear_arguments", [])),
            swing_factors=_fmt(swing_factors),
            dissent_section=dissent_section,
        )

        output = ModeratorOutput(
            round_number=round_number,
            bull_arguments=data.get("bull_arguments", []),
            bear_arguments=data.get("bear_arguments", []),
            outlier_agent_ids=data.get("outlier_agent_ids", []),
            outlier_challenge=data.get("outlier_challenge"),
            dissent_agent_ids=dissent_ids,
            dissent_summary=dissent_summary,
            swing_factors=swing_factors,
            moderator_brief=brief,
        )

        logger.info(
            f"[moderator] Round {round_number}: "
            f"bulls={len(output.bull_arguments)} bears={len(output.bear_arguments)} "
            f"outliers={len(output.outlier_agent_ids)} dissenters={len(output.dissent_agent_ids)} "
            f"swings={len(output.swing_factors)}"
        )
        return output

    def get_final_swing_factors(self) -> list[str]:
        """Top 5 swing factors across all rounds. Called after all rounds complete."""
        return [k for k, _ in sorted(self._swing_factor_counts.items(), key=lambda x: x[1], reverse=True)[:5]]
