"""PredictionSynthesiser — transforms negotiation data into final prediction reports."""

from __future__ import annotations

import json
import logging
import time
from typing import Optional

import anthropic

from db.schema import get_pool
from prediction_synthesiser.analysis import (
    compute_distribution,
    compute_sentiment_cascade,
    compute_verdict,
    extract_swing_factors,
)
from prediction_synthesiser.db import (
    load_all_round_results,
    load_final_agent_states,
    load_simulation,
)
from prediction_synthesiser.models import DISCLAIMER, PredictionReport

logger = logging.getLogger(__name__)

SUMMARY_PROMPT = """You are writing a concise earnings prediction summary for an ASX stock.

Ticker: {ticker}
Simulation ID: {simulation_id}

PROBABILITY DISTRIBUTION:
- P(beat): {p_beat:.1%} of agents predict earnings beat
- P(miss): {p_miss:.1%} of agents predict earnings miss
- P(inline): {p_inline:.1%} of agents predict inline
- Mean P(beat consensus): {mean_prob:.3f}
- Confidence band: {conf_low:.3f} – {conf_high:.3f} (±1 std dev)
- Verdict: {verdict}

TOP SWING FACTORS:
{swing_factors_text}

SENTIMENT CASCADE:
- Direction: {cascade_direction}
- Severity: {cascade_severity}
- Retail conviction: {retail_conviction:.2f}
- Assessment: {cascade_reasoning}

CONVERGENCE:
- Score: {convergence:.3f} (1.0 = perfect consensus)
- High uncertainty: {high_uncertainty}

Write a 4-6 sentence human-readable summary covering:
1. The headline verdict and probability
2. The key driver behind the prediction
3. The main risk/uncertainty
4. The expected market reaction severity

Be direct, analytical, and specific to this ticker. No hedging language beyond what the data supports.
End with exactly this line: "{disclaimer}"

Return ONLY the summary text."""


class PredictionSynthesiser:
    """Loads negotiation data from Neon and produces a final PredictionReport."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        database_url: Optional[str] = None,
    ) -> None:
        self.client = anthropic.AsyncAnthropic(api_key=api_key)
        self._db_url = database_url

    async def synthesise(self, simulation_id: str) -> PredictionReport:
        """Run full synthesis pipeline for a completed simulation."""
        start = time.monotonic()
        pool = await get_pool(self._db_url)

        # 1. Load data from Neon
        sim = await load_simulation(pool, simulation_id)
        if not sim:
            raise ValueError(f"Simulation {simulation_id} not found")
        ticker = sim["ticker"]

        agents, round_results = await self._load_data(pool, simulation_id)
        logger.info(f"[synth] Loaded {len(agents)} agents, {len(round_results)} round results")

        # 2. Compute probability distribution
        distribution = compute_distribution(agents)

        # 3. Extract swing factors
        swing_factors = extract_swing_factors(round_results, agents)

        # 4. Compute sentiment cascade
        cascade = compute_sentiment_cascade(agents)

        # 5. Convergence
        convergence = round(1.0 - distribution.std_dev, 4)
        high_uncertainty = distribution.std_dev > 0.25

        # 6. Verdict
        verdict = compute_verdict(distribution.mean_probability)

        # 7. Generate human summary via Haiku
        summary = await self._generate_summary(
            ticker=ticker,
            simulation_id=simulation_id,
            distribution=distribution,
            swing_factors=swing_factors,
            cascade=cascade,
            convergence=convergence,
            high_uncertainty=high_uncertainty,
            verdict=verdict,
        )

        elapsed = (time.monotonic() - start) * 1000
        logger.info(f"[synth] Synthesis complete in {elapsed:.0f}ms")

        return PredictionReport(
            simulation_id=simulation_id,
            ticker=ticker,
            verdict=verdict,
            distribution=distribution,
            swing_factors=swing_factors,
            sentiment_cascade=cascade,
            convergence_score=convergence,
            high_uncertainty=high_uncertainty,
            human_summary=summary,
        )

    async def _load_data(self, pool, simulation_id: str):
        """Load agents and round results in parallel."""
        import asyncio
        agents_task = load_final_agent_states(pool, simulation_id)
        rounds_task = load_all_round_results(pool, simulation_id)
        agents, round_results = await asyncio.gather(agents_task, rounds_task)
        return agents, round_results

    async def _generate_summary(
        self,
        ticker: str,
        simulation_id: str,
        distribution,
        swing_factors,
        cascade,
        convergence: float,
        high_uncertainty: bool,
        verdict: str,
    ) -> str:
        """Generate human-readable summary via Haiku."""
        sf_lines = []
        for i, sf in enumerate(swing_factors, 1):
            sf_lines.append(
                f"{i}. {sf.theme} (mentions={sf.mentions}, disagreement={sf.disagreement_score:.2f})\n"
                f"   Bull view: {sf.bull_view}\n"
                f"   Bear view: {sf.bear_view}"
            )
        swing_text = "\n".join(sf_lines) if sf_lines else "(no swing factors identified)"

        prompt = SUMMARY_PROMPT.format(
            ticker=ticker,
            simulation_id=simulation_id,
            p_beat=distribution.p_beat,
            p_miss=distribution.p_miss,
            p_inline=distribution.p_inline,
            mean_prob=distribution.mean_probability,
            conf_low=distribution.confidence_band_low,
            conf_high=distribution.confidence_band_high,
            verdict=verdict,
            swing_factors_text=swing_text,
            cascade_direction=cascade.direction,
            cascade_severity=cascade.severity,
            retail_conviction=cascade.retail_conviction,
            cascade_reasoning=cascade.reasoning,
            convergence=convergence,
            high_uncertainty=high_uncertainty,
            disclaimer=DISCLAIMER,
        )

        message = await self.client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=512,
            messages=[{"role": "user", "content": prompt}],
        )
        return message.content[0].text.strip()
