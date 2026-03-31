"""Final validation — BHP, CSL, XRO with full bias-anchored pipeline."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import time
import uuid

from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))

from db.schema import ensure_schema, get_pool
from seed_harvester.harvester import SeedHarvester
from persona_forge.forge import PersonaForge, get_starting_probability
from persona_forge.models import ForgeRequest, Archetype
from negotiation_runner.runner import NegotiationRunner
from prediction_synthesiser.synthesiser import PredictionSynthesiser

TICKERS = ["BHP", "CSL", "XRO"]
DELAY = 15


async def run_one(ticker: str, pool) -> dict:
    """Run full pipeline with detailed logging."""
    sim_id = f"final-{ticker.lower()}-{uuid.uuid4().hex[:6]}"
    start = time.monotonic()

    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO simulations (id, ticker, status) VALUES ($1, $2, 'pending')",
            sim_id, ticker,
        )

    # Stage 1: Harvest
    print(f"\n  [1/4] Harvesting {ticker}...")
    harvester = SeedHarvester()
    harvest = await harvester.harvest(ticker=ticker, force_refresh=True)
    bias = harvest.ticker_bias_score
    seed_q = harvest.quality.overall_score if harvest.quality else 0.0
    seeds = len(harvest.seeds)

    async with pool.acquire() as conn:
        await conn.execute("UPDATE simulations SET seed_quality = $1 WHERE id = $2", seed_q, sim_id)

    seed_summaries = [f"[{s.seed_type.value.upper()}] {s.content}" for s in harvest.seeds]
    print(f"         {seeds} seeds, quality={seed_q:.2f}, bias_score={bias}")

    # Show starting distribution
    print(f"\n  Starting probability distribution (bias={bias:.3f}):")
    print(f"  {'Archetype':<20} {'Avg':>6}")
    print(f"  {'─'*28}")
    for arch in Archetype:
        probs = [get_starting_probability(arch, bias, i) for i in range(10)]
        print(f"  {arch.value:<20} {sum(probs)/len(probs):>6.3f}")
    all_start = [get_starting_probability(arch, bias, i) for arch in Archetype for i in range(10)]
    print(f"  {'ALL 50':<20} {sum(all_start)/len(all_start):>6.3f}")

    # Stage 2: Forge
    print(f"\n  [2/4] Forging 50 personas...")
    forge = PersonaForge()
    forge_req = ForgeRequest(
        simulation_id=sim_id, ticker=ticker,
        seed_summaries=seed_summaries, agents_per_archetype=10,
        ticker_bias_score=bias,
    )
    forge_result = await forge.forge(forge_req)
    print(f"         {forge_result.total_count} personas forged")

    # Stage 3: Negotiate
    print(f"\n  [3/4] Running negotiation (3 rounds)...")
    runner = NegotiationRunner()
    neg = await runner.run(simulation_id=sim_id, ticker=ticker, seed_summaries=seed_summaries)

    print(f"\n  Round-by-round:")
    for rs in neg.round_summaries:
        print(f"    R{rs.round_number}: mean={rs.mean_probability:.3f} std={rs.std_dev:.3f} "
              f"bulls={rs.bull_count} neutral={rs.neutral_count} bears={rs.bear_count}")

    # Stage 4: Synthesise
    print(f"\n  [4/4] Synthesising...")
    synth = PredictionSynthesiser()
    report = await synth.synthesise(sim_id)

    dur = time.monotonic() - start

    return {
        "ticker": ticker,
        "bias_score": bias,
        "seed_quality": seed_q,
        "verdict": report.verdict,
        "mean_prob": report.distribution.mean_probability,
        "p_beat": report.distribution.p_beat,
        "p_miss": report.distribution.p_miss,
        "convergence": report.convergence_score,
        "duration_s": round(dur, 1),
    }


async def main():
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    # Suppress noisy loggers
    for name in ["httpx", "httpcore", "urllib3", "yfinance"]:
        logging.getLogger(name).setLevel(logging.WARNING)

    await ensure_schema()
    pool = await get_pool()

    print(f"\n{'='*70}")
    print(f"  FINAL VALIDATION — BHP, CSL, XRO")
    print(f"  Pipeline: harvest → forge (bias-anchored) → negotiate → synthesise")
    print(f"  Rounds: {os.environ.get('SIMULATION_ROUNDS', '3')}")
    print(f"{'='*70}")

    results = []
    for i, ticker in enumerate(TICKERS):
        print(f"\n{'─'*70}")
        print(f"  [{i+1}/{len(TICKERS)}] {ticker}")
        print(f"{'─'*70}")

        r = await run_one(ticker, pool)
        results.append(r)

        print(f"\n  RESULT: {r['verdict']}  mean={r['mean_prob']:.3f}  "
              f"P(beat)={r['p_beat']:.0%}  P(miss)={r['p_miss']:.0%}  "
              f"conv={r['convergence']:.3f}  {r['duration_s']}s")

        if i < len(TICKERS) - 1:
            print(f"\n  Waiting {DELAY}s...")
            await asyncio.sleep(DELAY)

    # Summary table
    print(f"\n{'='*70}")
    print(f"  SUMMARY")
    print(f"{'='*70}")
    print(f"{'Ticker':<8} {'Bias':>6} {'Verdict':<14} {'Mean':>6} {'P(beat)':>8} {'P(miss)':>8} {'Conv':>6} {'SeedQ':>6} {'Time':>6}")
    print("─" * 70)
    for r in results:
        print(f"{r['ticker']:<8} {r['bias_score']:>6.3f} {r['verdict']:<14} {r['mean_prob']:>6.3f} "
              f"{r['p_beat']:>7.0%} {r['p_miss']:>7.0%} {r['convergence']:>6.3f} "
              f"{r['seed_quality']:>5.2f} {r['duration_s']:>5.1f}s")

    # Criteria checks
    bhp = next(r for r in results if r["ticker"] == "BHP")
    xro = next(r for r in results if r["ticker"] == "XRO")
    spread = xro["mean_prob"] - bhp["mean_prob"]
    verdicts = [r["verdict"] for r in results]
    has_beat = any("BEAT" in v for v in verdicts)
    all_under_150 = all(r["duration_s"] <= 150 for r in results)
    no_crashes = len(results) == len(TICKERS)
    unique_verdicts = len(set(verdicts)) > 1

    print(f"\n{'─'*70}")
    print("  CRITERIA:")
    checks = [
        (xro["mean_prob"] > 0.60,  f"XRO mean > 0.60: {xro['mean_prob']:.3f}"),
        (bhp["mean_prob"] < 0.55,  f"BHP mean < 0.55: {bhp['mean_prob']:.3f}"),
        (spread > 0.10,            f"XRO-BHP spread > 0.10: {spread:.3f}"),
        (has_beat,                 f"At least 1 BEAT/LEAN BEAT: {verdicts}"),
        (all_under_150,            f"All under 150s: {[str(round(r['duration_s'])) + 's' for r in results]}"),
        (no_crashes,               f"Zero crashes: {len(results)}/{len(TICKERS)}"),
        (unique_verdicts,          f"No identical verdicts: {verdicts}"),
    ]

    all_pass = True
    for passed, desc in checks:
        status = " OK " if passed else "FAIL"
        print(f"  [{status}] {desc}")
        if not passed:
            all_pass = False

    print(f"\n{'='*70}")
    if all_pass:
        print("  ALL CRITERIA PASSED — READY TO COMMIT")
    else:
        print("  SOME CRITERIA FAILED — DO NOT COMMIT")
    print(f"{'='*70}\n")

    return all_pass


if __name__ == "__main__":
    passed = asyncio.run(main())
    sys.exit(0 if passed else 1)
