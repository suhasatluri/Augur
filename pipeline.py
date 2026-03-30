"""Full simulation pipeline — orchestrates all 4 modules sequentially."""

from __future__ import annotations

import logging
import time

from db.schema import get_pool
from seed_harvester.harvester import SeedHarvester
from persona_forge.forge import PersonaForge
from persona_forge.models import ForgeRequest
from negotiation_runner.runner import NegotiationRunner
from prediction_synthesiser.synthesiser import PredictionSynthesiser
from prediction_synthesiser.models import PredictionReport

logger = logging.getLogger(__name__)


async def run_full_pipeline(
    simulation_id: str,
    ticker: str,
    reporting_date: str = "",
) -> PredictionReport:
    """Execute the full Augur pipeline: harvest → forge → negotiate → synthesise.

    Updates simulation status in Neon at each stage.
    Raises on failure (caller handles status update to 'failed').
    """
    start = time.monotonic()
    pool = await get_pool()

    logger.info(f"[pipeline] Starting full pipeline for {ticker} ({simulation_id})")

    # --- Stage 1: Seed Harvester ---
    logger.info("[pipeline] Stage 1/4: Seed Harvester")
    harvester = SeedHarvester()
    harvest = await harvester.harvest(
        ticker=ticker,
        force_refresh=True,
        reporting_period=reporting_date or "next scheduled report",
    )
    seed_quality = harvest.quality.overall_score if harvest.quality else 0.0

    # Update seed_quality on simulation
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE simulations SET seed_quality = $1 WHERE id = $2",
            seed_quality, simulation_id,
        )

    seed_summaries = [
        f"[{s.seed_type.value.upper()}] {s.content}"
        for s in harvest.seeds
    ]
    logger.info(f"[pipeline] Harvested {len(harvest.seeds)} seeds (quality={seed_quality:.2f})")

    # --- Stage 2: Persona Forge ---
    logger.info("[pipeline] Stage 2/4: Persona Forge")
    forge = PersonaForge()
    forge_request = ForgeRequest(
        simulation_id=simulation_id,
        ticker=ticker,
        seed_summaries=seed_summaries,
        agents_per_archetype=10,
    )
    forge_result = await forge.forge(forge_request)
    logger.info(f"[pipeline] Forged {forge_result.total_count} personas")

    # --- Stage 3: Negotiation Runner ---
    logger.info("[pipeline] Stage 3/4: Negotiation Runner")
    runner = NegotiationRunner()
    neg_result = await runner.run(simulation_id=simulation_id, ticker=ticker, seed_summaries=seed_summaries)
    logger.info(
        f"[pipeline] Negotiation complete: mean={neg_result.final_mean_probability:.3f} "
        f"convergence={neg_result.convergence_score:.3f}"
    )

    # --- Stage 4: Prediction Synthesiser ---
    logger.info("[pipeline] Stage 4/4: Prediction Synthesiser")
    synth = PredictionSynthesiser()
    report = await synth.synthesise(simulation_id)

    elapsed = (time.monotonic() - start) * 1000
    logger.info(f"[pipeline] Full pipeline complete in {elapsed/1000:.1f}s — verdict: {report.verdict}")

    return report
