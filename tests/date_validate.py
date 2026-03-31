"""Date-anchoring validation — verify reporting_date flows through and differentiates simulations."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import time
import uuid
from datetime import date as date_type

from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))

from db.schema import ensure_schema, get_pool
from seed_harvester.harvester import SeedHarvester
from persona_forge.forge import PersonaForge
from persona_forge.models import ForgeRequest, Archetype
from negotiation_runner.runner import NegotiationRunner
from prediction_synthesiser.synthesiser import PredictionSynthesiser

TICKERS = [
    ("WBC", "2025-08-12"),
    ("CBA", "2025-08-14"),
]
DELAY = 15


async def run_one(ticker: str, reporting_date: str, pool) -> dict:
    """Run full pipeline with date anchoring, capture agent reasoning."""
    sim_id = f"date-{ticker.lower()}-{uuid.uuid4().hex[:6]}"
    start = time.monotonic()

    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO simulations (id, ticker, reporting_date, status) VALUES ($1, $2, $3, 'pending')",
            sim_id, ticker, date_type.fromisoformat(reporting_date),
        )

    # Stage 1: Harvest
    print(f"\n  [1/4] Harvesting {ticker} (reporting: {reporting_date})...")
    harvester = SeedHarvester()
    harvest = await harvester.harvest(
        ticker=ticker, force_refresh=True, reporting_period=reporting_date,
    )
    bias = harvest.ticker_bias_score
    seed_q = harvest.quality.overall_score if harvest.quality else 0.0
    seed_summaries = [f"[{s.seed_type.value.upper()}] {s.content}" for s in harvest.seeds]

    async with pool.acquire() as conn:
        await conn.execute("UPDATE simulations SET seed_quality = $1 WHERE id = $2", seed_q, sim_id)

    print(f"         {len(harvest.seeds)} seeds, quality={seed_q:.2f}, bias={bias:.3f}")

    # Capture macro seeds for validation
    macro_seeds = [s for s in harvest.seeds if s.seed_type.value == "macro"]
    print(f"\n  Macro seeds ({len(macro_seeds)}):")
    for s in macro_seeds:
        print(f"    - {s.content[:120]}")

    # Stage 2: Forge
    print(f"\n  [2/4] Forging 50 personas...")
    forge = PersonaForge()
    forge_req = ForgeRequest(
        simulation_id=sim_id, ticker=ticker,
        seed_summaries=seed_summaries, agents_per_archetype=10,
        ticker_bias_score=bias, reporting_date=reporting_date,
    )
    forge_result = await forge.forge(forge_req)
    print(f"         {forge_result.total_count} personas forged")

    # Capture sample reasoning from bull and bear
    sample_bull = next((p for p in forge_result.personas if p.archetype == Archetype.BULL_ANALYST), None)
    sample_bear = next((p for p in forge_result.personas if p.archetype == Archetype.BEAR_ANALYST), None)

    print(f"\n  Sample Bull ({sample_bull.name if sample_bull else 'N/A'}):")
    if sample_bull:
        print(f"    Reasoning: {sample_bull.initial_reasoning[:200]}")
    print(f"  Sample Bear ({sample_bear.name if sample_bear else 'N/A'}):")
    if sample_bear:
        print(f"    Reasoning: {sample_bear.initial_reasoning[:200]}")

    # Stage 3: Negotiate
    print(f"\n  [3/4] Negotiating (3 rounds)...")
    runner = NegotiationRunner()
    neg = await runner.run(
        simulation_id=sim_id, ticker=ticker,
        seed_summaries=seed_summaries, reporting_date=reporting_date,
    )

    print(f"  Round-by-round:")
    for rs in neg.round_summaries:
        print(f"    R{rs.round_number}: mean={rs.mean_probability:.3f} bulls={rs.bull_count} bears={rs.bear_count}")

    # Capture sample round reasoning
    async with pool.acquire() as conn:
        sample_rr = await conn.fetch(
            """SELECT rr.reasoning, a.archetype, a.persona->>'name' as name
               FROM round_results rr JOIN agents a ON a.id = rr.agent_id
               WHERE rr.simulation_id = $1 AND rr.round_number = 1
               ORDER BY a.archetype LIMIT 2""",
            sim_id,
        )
    if sample_rr:
        print(f"\n  Sample Round 1 reasoning:")
        for r in sample_rr:
            print(f"    [{r['archetype']}] {r['name']}: {r['reasoning'][:150]}")

    # Stage 4: Synthesise
    print(f"\n  [4/4] Synthesising...")
    synth = PredictionSynthesiser()
    report = await synth.synthesise(sim_id)

    dur = time.monotonic() - start

    # Check date references in all reasoning
    all_reasoning = " ".join(s.content for s in harvest.seeds)
    if sample_bull:
        all_reasoning += " " + sample_bull.initial_reasoning
    if sample_bear:
        all_reasoning += " " + sample_bear.initial_reasoning
    for r in (sample_rr or []):
        all_reasoning += " " + r["reasoning"]
    all_reasoning_lower = all_reasoning.lower()

    has_date_ref = any(
        term in all_reasoning_lower
        for term in ["august 2025", "aug 2025", "2025-08", "h2 2025", "second half 2025", reporting_date]
    )
    has_rba_ref = any(
        term in all_reasoning_lower
        for term in ["rba", "reserve bank", "interest rate", "rate cut", "rate hold", "monetary policy"]
    )

    return {
        "ticker": ticker,
        "reporting_date": reporting_date,
        "verdict": report.verdict,
        "mean_prob": report.distribution.mean_probability,
        "p_beat": report.distribution.p_beat,
        "p_miss": report.distribution.p_miss,
        "convergence": report.convergence_score,
        "seed_quality": seed_q,
        "bias_score": bias,
        "duration_s": round(dur, 1),
        "has_date_ref": has_date_ref,
        "has_rba_ref": has_rba_ref,
        "macro_seeds": [s.content[:100] for s in macro_seeds],
    }


async def main():
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    for name in ["httpx", "httpcore", "urllib3", "yfinance"]:
        logging.getLogger(name).setLevel(logging.WARNING)

    await ensure_schema()
    pool = await get_pool()

    print(f"\n{'='*70}")
    print(f"  DATE-ANCHORING VALIDATION — WBC (2025-08-12), CBA (2025-08-14)")
    print(f"{'='*70}")

    results = []
    for i, (ticker, rd) in enumerate(TICKERS):
        print(f"\n{'─'*70}")
        print(f"  [{i+1}/{len(TICKERS)}] {ticker} — reporting {rd}")
        print(f"{'─'*70}")

        r = await run_one(ticker, rd, pool)
        results.append(r)

        print(f"\n  RESULT: {r['verdict']}  mean={r['mean_prob']:.3f}  "
              f"P(beat)={r['p_beat']:.0%}  P(miss)={r['p_miss']:.0%}  "
              f"{r['duration_s']}s")

        if i < len(TICKERS) - 1:
            print(f"\n  Waiting {DELAY}s...")
            await asyncio.sleep(DELAY)

    # Summary
    wbc = results[0]
    cba = results[1]

    print(f"\n{'='*70}")
    print(f"  SUMMARY")
    print(f"{'='*70}")
    print(f"{'Ticker':<6} {'Date':<12} {'Bias':>6} {'Verdict':<14} {'Mean':>6} {'P(beat)':>8} {'P(miss)':>8} {'Time':>6}")
    print("─" * 70)
    for r in results:
        print(f"{r['ticker']:<6} {r['reporting_date']:<12} {r['bias_score']:>6.3f} {r['verdict']:<14} "
              f"{r['mean_prob']:>6.3f} {r['p_beat']:>7.0%} {r['p_miss']:>7.0%} {r['duration_s']:>5.1f}s")

    # Criteria
    print(f"\n{'─'*70}")
    print("  CRITERIA:")

    checks = [
        (wbc["has_rba_ref"],
         f"WBC macro references RBA/rates: {wbc['has_rba_ref']}"),
        (wbc["has_date_ref"],
         f"WBC references August 2025 context: {wbc['has_date_ref']}"),
        (cba["has_date_ref"],
         f"CBA references August 2025 context: {cba['has_date_ref']}"),
        (wbc["verdict"] != cba["verdict"],
         f"Different verdicts: WBC={wbc['verdict']}, CBA={cba['verdict']}"),
        (len(results) == 2 and all(r["verdict"] is not None for r in results),
         f"Zero crashes: {len([r for r in results if r['verdict']])} / {len(results)}"),
    ]

    all_pass = True
    for passed, desc in checks:
        status = " OK " if passed else "FAIL"
        print(f"  [{status}] {desc}")
        if not passed:
            all_pass = False

    print(f"\n{'='*70}")
    if all_pass:
        print("  ALL CRITERIA PASSED — DATE ANCHORING COMPLETE")
    else:
        print("  SOME CRITERIA FAILED")
    print(f"{'='*70}\n")

    return all_pass


if __name__ == "__main__":
    passed = asyncio.run(main())
    sys.exit(0 if passed else 1)
