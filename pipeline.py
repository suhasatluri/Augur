"""Full simulation pipeline — orchestrates all 4 modules sequentially."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Optional

from db.schema import get_pool
from seed_harvester.harvester import SeedHarvester
from persona_forge.forge import PersonaForge
from persona_forge.models import ForgeRequest
from negotiation_runner.runner import NegotiationRunner
from prediction_synthesiser.synthesiser import PredictionSynthesiser
from prediction_synthesiser.models import PredictionReport

try:
    import sentry_sdk
except ImportError:
    sentry_sdk = None  # type: ignore

logger = logging.getLogger(__name__)

PIPELINE_TIMEOUT = 300  # 5 minutes max per simulation


def _build_market_signal_seeds(structured_data: dict) -> list[str]:
    """Convert structured market signals into seed summary strings for agent prompts."""
    seeds = []

    short = structured_data.get("source_asic_short", {})
    if short:
        pct = short.get("pct_shorted", 0)
        sig = short.get("signal", "UNKNOWN")
        context = {
            "HIGH": "significant institutional conviction this stock will underperform",
            "ELEVATED": "meaningful institutional skepticism",
            "NORMAL": "short interest within normal range",
            "LOW": "shorts not positioned against this stock",
        }.get(sig, "")
        seeds.append(f"[MARKET] ASIC short interest: {pct:.2f}% [{sig}] — {context}")

    director = structured_data.get("source_director", {})
    if director:
        sig = director.get("signal", "NEUTRAL")
        net = director.get("net_buy_value", 0)
        buys = director.get("buy_count", 0)
        sells = director.get("sell_count", 0)
        context = {
            "STRONG_BUY": "directors buying heavily — strong insider conviction",
            "BUY": "net insider buying — directors more bullish than bearish",
            "NEUTRAL": "mixed or minimal insider activity",
            "SELL": "net insider selling — directors reducing exposure",
            "STRONG_SELL": "significant insider selling",
        }.get(sig, "")
        net_str = f"+${net:,.0f}" if net > 0 else f"-${abs(net):,.0f}"
        seeds.append(
            f"[MARKET] Director trading: {buys} buys, {sells} sells, "
            f"net {net_str} [{sig}] — {context}"
        )

    yf = structured_data.get("source_yfinance", {})
    spread = yf.get("analyst_spread_pct")
    if spread and spread > 0:
        hi = yf.get("targetHighPrice")
        lo = yf.get("targetLowPrice")
        quality = yf.get("analyst_consensus_quality", "N/A")
        context = {
            "HIGH_DISAGREEMENT": "analysts widely split — wide range of outcomes possible",
            "MODERATE_DISAGREEMENT": "moderate analyst disagreement on fair value",
            "CONSENSUS": "analysts broadly aligned on valuation",
        }.get(quality, "")
        range_str = f"${lo:.2f}–${hi:.2f} " if lo and hi else ""
        seeds.append(
            f"[MARKET] Analyst target range: {range_str}(spread {spread:.0f}%) "
            f"[{quality}] — {context}"
        )

    mi_fin = structured_data.get("source_mi_financials", {})
    if mi_fin and mi_fin.get("npat_m") is not None:
        npat = mi_fin["npat_m"]
        npat_prior = mi_fin.get("npat_prior_m")
        growth_str = ""
        if npat_prior and npat_prior != 0:
            g = ((npat - npat_prior) / abs(npat_prior)) * 100
            growth_str = f" ({g:+.1f}% YoY)"
        seeds.append(f"[MARKET] NPAT: ${npat:,.0f}M{growth_str} (Market Index 10yr history)")

    return seeds
SEED_CACHE_TTL_HOURS = 6
SEED_CACHE_MIN_QUALITY = 0.6


async def _check_seed_cache(pool, ticker: str) -> Optional[dict]:
    """Check for a recent completed simulation with cached seed data for this ticker.

    Returns dict with seed_summaries, ticker_bias_score, seed_quality, age_minutes
    or None if no valid cache entry exists.
    """
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """SELECT seed_data, seed_quality, created_at
               FROM simulations
               WHERE ticker = $1
                 AND status = 'complete'
                 AND seed_data IS NOT NULL
                 AND created_at > NOW() - INTERVAL '6 hours'
               ORDER BY created_at DESC
               LIMIT 1""",
            ticker,
        )

    if not row or not row["seed_data"]:
        return None

    quality = row["seed_quality"] or 0.0
    if quality < SEED_CACHE_MIN_QUALITY:
        logger.info(f"[pipeline] Seed cache skip for {ticker} — quality {quality:.2f} < {SEED_CACHE_MIN_QUALITY}")
        return None

    try:
        data = json.loads(row["seed_data"]) if isinstance(row["seed_data"], str) else row["seed_data"]
    except (json.JSONDecodeError, TypeError):
        return None

    # Compute age in minutes
    from datetime import datetime, timezone
    created = row["created_at"]
    if created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    age_seconds = (datetime.now(timezone.utc) - created).total_seconds()
    age_minutes = round(age_seconds / 60)

    data["age_minutes"] = age_minutes
    return data


async def run_full_pipeline(
    simulation_id: str,
    ticker: str,
    reporting_date: str = "",
) -> PredictionReport:
    """Execute the full Augur pipeline: harvest → forge → negotiate → synthesise.

    Updates simulation status in Neon at each stage.
    Raises on failure (caller handles status update to 'failed').
    """
    return await asyncio.wait_for(
        _run_pipeline_inner(simulation_id, ticker, reporting_date),
        timeout=PIPELINE_TIMEOUT,
    )


async def _run_pipeline_inner(
    simulation_id: str,
    ticker: str,
    reporting_date: str = "",
) -> PredictionReport:
    start = time.monotonic()
    pool = await get_pool()

    if sentry_sdk:
        sentry_sdk.set_tag("ticker", ticker)
        sentry_sdk.set_tag("simulation_id", simulation_id)

    logger.info(f"[pipeline] Starting full pipeline for {ticker} ({simulation_id})")

    # --- Stage 1: Seed Harvester (with 6-hour cache) ---
    logger.info("[pipeline] Stage 1/4: Seed Harvester")

    cached = await _check_seed_cache(pool, ticker)
    if cached:
        seed_quality = cached["seed_quality"]
        seed_summaries = cached["seed_summaries"]
        ticker_bias = cached["ticker_bias_score"]
        age_min = cached["age_minutes"]
        logger.info(f"Seed cache HIT for {ticker} — age {age_min}m")
        logger.info(f"[pipeline] Using cached seeds ({len(seed_summaries)} seeds, quality={seed_quality:.2f})")

        # Propagate seed_data to new simulation so it can serve as cache for future runs
        seed_data = {
            "seed_summaries": seed_summaries,
            "ticker_bias_score": ticker_bias,
            "seed_quality": seed_quality,
        }
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE simulations SET seed_quality = $1, seed_data = $2 WHERE id = $3",
                seed_quality, json.dumps(seed_data), simulation_id,
            )
    else:
        logger.info(f"Seed cache MISS for {ticker} — fetching fresh")
        harvester = SeedHarvester()
        harvest = await harvester.harvest(
            ticker=ticker,
            force_refresh=True,
            reporting_period=reporting_date or "next scheduled report",
        )
        seed_quality = harvest.quality.overall_score if harvest.quality else 0.0

        seed_summaries = [
            f"[{s.seed_type.value.upper()}] {s.content}"
            for s in harvest.seeds
        ]

        # Inject market signal seeds from structured data
        if harvest.structured_data:
            signal_seeds = _build_market_signal_seeds(harvest.structured_data)
            seed_summaries.extend(signal_seeds)

        ticker_bias = harvest.ticker_bias_score

        # Store seed_quality + seed_data for future cache hits
        seed_data = {
            "seed_summaries": seed_summaries,
            "ticker_bias_score": ticker_bias,
            "seed_quality": seed_quality,
        }
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE simulations SET seed_quality = $1, seed_data = $2 WHERE id = $3",
                seed_quality, json.dumps(seed_data), simulation_id,
            )

        logger.info(f"[pipeline] Harvested {len(harvest.seeds)} seeds (quality={seed_quality:.2f})")

    # --- Stage 2: Persona Forge ---
    logger.info("[pipeline] Stage 2/4: Persona Forge")
    logger.info(f"[pipeline] ticker_bias_score={ticker_bias}")
    forge = PersonaForge()
    forge_request = ForgeRequest(
        simulation_id=simulation_id,
        ticker=ticker,
        seed_summaries=seed_summaries,
        agents_per_archetype=10,
        ticker_bias_score=ticker_bias,
        reporting_date=reporting_date or None,
    )
    forge_result = await forge.forge(forge_request)
    logger.info(f"[pipeline] Forged {forge_result.total_count} personas")

    # --- Stage 3: Negotiation Runner ---
    logger.info("[pipeline] Stage 3/4: Negotiation Runner")
    runner = NegotiationRunner()
    neg_result = await runner.run(
        simulation_id=simulation_id, ticker=ticker,
        seed_summaries=seed_summaries, reporting_date=reporting_date or None,
    )
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
