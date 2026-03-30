"""Quick 3-ticker validation after calibration + speed fixes."""

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
from pipeline import run_full_pipeline

TICKERS = ["BHP", "CSL", "XRO"]


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    await ensure_schema()
    pool = await get_pool()

    print(f"\n{'='*70}")
    print(f"  QUICK VALIDATION — 3 tickers, calibration + speed fixes")
    print(f"  Rounds: {os.environ.get('SIMULATION_ROUNDS', '3')}")
    print(f"{'='*70}\n")

    results = []

    for i, ticker in enumerate(TICKERS, 1):
        sim_id = f"val-{ticker.lower()}-{uuid.uuid4().hex[:6]}"
        async with pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO simulations (id, ticker, status) VALUES ($1, $2, 'pending')",
                sim_id, ticker,
            )

        print(f"[{i}/3] {ticker}...", flush=True)
        start = time.monotonic()

        try:
            report = await run_full_pipeline(sim_id, ticker)
            dur = time.monotonic() - start

            # Get seed quality
            async with pool.acquire() as conn:
                sq = await conn.fetchval(
                    "SELECT seed_quality FROM simulations WHERE id = $1", sim_id
                )

            r = {
                "ticker": ticker,
                "verdict": report.verdict,
                "mean_prob": report.distribution.mean_probability,
                "p_beat": report.distribution.p_beat,
                "p_miss": report.distribution.p_miss,
                "convergence": report.convergence_score,
                "seed_quality": round(sq, 2) if sq else None,
                "duration_s": round(dur, 1),
            }
            results.append(r)
            print(
                f"  -> {r['verdict']:14s} mean={r['mean_prob']:.3f} "
                f"P(beat)={r['p_beat']:.0%} P(miss)={r['p_miss']:.0%} "
                f"conv={r['convergence']:.3f} seed_q={r['seed_quality']} "
                f"{r['duration_s']}s"
            )
        except Exception as e:
            dur = time.monotonic() - start
            print(f"  -> FAILED: {e} ({dur:.0f}s)")
            results.append({"ticker": ticker, "verdict": "FAILED", "duration_s": round(dur, 1)})

        if i < len(TICKERS):
            print("  Waiting 15s...")
            await asyncio.sleep(15)

    # Summary
    print(f"\n{'='*70}")
    print(f"  VALIDATION RESULTS")
    print(f"{'='*70}")
    print(f"{'Ticker':<8} {'Verdict':<14} {'Mean':>6} {'P(beat)':>8} {'P(miss)':>8} {'Conv':>6} {'SeedQ':>6} {'Time':>6}")
    print("─" * 70)
    for r in results:
        if r["verdict"] != "FAILED":
            print(
                f"{r['ticker']:<8} {r['verdict']:<14} {r['mean_prob']:>6.3f} "
                f"{r['p_beat']:>7.0%} {r['p_miss']:>7.0%} "
                f"{r['convergence']:>6.3f} {r.get('seed_quality', 0):>5.2f} "
                f"{r['duration_s']:>5.1f}s"
            )
        else:
            print(f"{r['ticker']:<8} {'FAILED':<14} {'—':>6} {'—':>8} {'—':>8} {'—':>6} {'—':>6} {r['duration_s']:>5.1f}s")

    # Checks
    completed = [r for r in results if r["verdict"] != "FAILED"]
    verdicts = [r["verdict"] for r in completed]
    mean_probs = [r["mean_prob"] for r in completed]
    durations = [r["duration_s"] for r in completed]

    print(f"\n{'─'*70}")
    print("  CHECKS:")
    has_beat = any("BEAT" in v for v in verdicts)
    print(f"  [{'OK' if has_beat else 'FAIL'}] At least 1 BEAT or LEAN BEAT verdict: {verdicts}")
    spread_ok = max(mean_probs) - min(mean_probs) > 0.10 if len(mean_probs) > 1 else False
    print(f"  [{'OK' if spread_ok else 'FAIL'}] Mean probability spread > 0.10: {[f'{p:.3f}' for p in mean_probs]}")
    under_150 = all(d <= 150 for d in durations)
    print(f"  [{'OK' if under_150 else 'FAIL'}] All durations under 150s: {[f'{d:.0f}s' for d in durations]}")
    no_fails = len(completed) == len(results)
    print(f"  [{'OK' if no_fails else 'FAIL'}] No crashes: {len(completed)}/{len(results)}")
    print(f"{'='*70}\n")


if __name__ == "__main__":
    asyncio.run(main())
