"""Batch test — run full pipeline for 20 ASX 100 companies across 5 sectors."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import time
import uuid
from datetime import datetime

from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))

from db.schema import ensure_schema, get_pool
from pipeline import run_full_pipeline

SECTORS = {
    "Mining":           ["BHP", "RIO", "FMG", "S32"],
    "Banking":          ["CBA", "WBC", "ANZ", "NAB"],
    "Retail/Consumer":  ["WES", "WOW", "COL", "JBH"],
    "Healthcare":       ["CSL", "RHC", "SHL", "COH"],
    "Technology":       ["WTC", "XRO", "TYR", "ALU"],
}

DELAY_BETWEEN = 5  # seconds
TICKER_TIMEOUT = 300  # 5 minutes max per ticker
RESULTS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "batch_results.json")


async def run_single(ticker: str, sector: str) -> dict:
    """Run full pipeline for one ticker with a hard timeout. Returns result dict."""
    simulation_id = f"batch-{ticker.lower()}-{uuid.uuid4().hex[:6]}"
    pool = await get_pool()

    # Create simulation row
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO simulations (id, ticker, status) VALUES ($1, $2, 'pending')",
            simulation_id, ticker,
        )

    start = time.monotonic()
    try:
        report = await asyncio.wait_for(
            run_full_pipeline(simulation_id, ticker),
            timeout=TICKER_TIMEOUT,
        )
        duration = time.monotonic() - start

        return {
            "ticker": ticker,
            "sector": sector,
            "simulation_id": simulation_id,
            "status": "complete",
            "verdict": report.verdict,
            "p_beat": report.distribution.p_beat,
            "p_miss": report.distribution.p_miss,
            "p_inline": report.distribution.p_inline,
            "mean_probability": report.distribution.mean_probability,
            "convergence_score": report.convergence_score,
            "high_uncertainty": report.high_uncertainty,
            "seed_quality": None,  # filled below
            "duration_s": round(duration, 1),
            "error": None,
        }
    except asyncio.TimeoutError:
        duration = time.monotonic() - start
        logging.error(f"[batch] {ticker} TIMED OUT after {TICKER_TIMEOUT}s")
        return {
            "ticker": ticker,
            "sector": sector,
            "simulation_id": simulation_id,
            "status": "failed",
            "verdict": None,
            "p_beat": None,
            "p_miss": None,
            "p_inline": None,
            "mean_probability": None,
            "convergence_score": None,
            "high_uncertainty": None,
            "seed_quality": None,
            "duration_s": round(duration, 1),
            "error": f"Timed out after {TICKER_TIMEOUT}s",
        }
    except Exception as e:
        duration = time.monotonic() - start
        logging.error(f"[batch] {ticker} FAILED: {e}")
        return {
            "ticker": ticker,
            "sector": sector,
            "simulation_id": simulation_id,
            "status": "failed",
            "verdict": None,
            "p_beat": None,
            "p_miss": None,
            "p_inline": None,
            "mean_probability": None,
            "convergence_score": None,
            "high_uncertainty": None,
            "seed_quality": None,
            "duration_s": round(duration, 1),
            "error": str(e),
        }


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    await ensure_schema()

    # Support --tickers flag for subset runs
    custom_tickers: list[str] | None = None
    if "--tickers" in sys.argv:
        idx = sys.argv.index("--tickers")
        custom_tickers = [t.upper() for t in sys.argv[idx + 1:]]

    if custom_tickers:
        # Map custom tickers to their sectors
        sector_lookup = {t: s for s, tickers in SECTORS.items() for t in tickers}
        all_tickers = [(t, sector_lookup.get(t, "Unknown")) for t in custom_tickers]
    else:
        all_tickers = [(ticker, sector) for sector, tickers in SECTORS.items() for ticker, sector in zip(tickers, [sector] * len(tickers))]
    total = len(all_tickers)

    print(f"\n{'='*80}")
    print(f"  AUGUR BATCH TEST — {total} tickers across {len(SECTORS)} sectors")
    print(f"  Started: {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"  Delay between runs: {DELAY_BETWEEN}s")
    print(f"{'='*80}\n")

    results: list[dict] = []
    pool = await get_pool()

    for i, (ticker, sector) in enumerate(all_tickers, 1):
        print(f"[{i:2d}/{total}] {ticker} ({sector})", end=" ", flush=True)

        result = await run_single(ticker, sector)

        # Fetch seed_quality from DB (guard against asyncpg cleanup issues after timeout)
        try:
            async with pool.acquire() as conn:
                sq = await conn.fetchval(
                    "SELECT seed_quality FROM simulations WHERE id = $1",
                    result["simulation_id"],
                )
                result["seed_quality"] = round(sq, 2) if sq is not None else None
        except Exception as e:
            logging.debug(f"[batch] seed_quality fetch failed for {result['ticker']}: {e}")
            result["seed_quality"] = None

        results.append(result)

        # Print inline result
        if result["status"] == "complete":
            print(
                f"-> {result['verdict']:12s}  "
                f"P(beat)={result['p_beat']:.0%}  "
                f"P(miss)={result['p_miss']:.0%}  "
                f"conv={result['convergence_score']:.3f}  "
                f"seed_q={result['seed_quality']}  "
                f"{result['duration_s']}s"
            )
        else:
            print(f"-> FAILED ({result['error'][:60]}...)  {result['duration_s']}s")

        # Save intermediate results
        with open(RESULTS_PATH, "w") as f:
            json.dump({"generated_at": datetime.utcnow().isoformat(), "results": results}, f, indent=2)

        # Delay between runs (skip after last)
        if i < total:
            print(f"     Waiting {DELAY_BETWEEN}s before next run...")
            await asyncio.sleep(DELAY_BETWEEN)

    # --- Summary (always prints, even after timeout errors) ---
    # Save final results JSON before printing summary
    with open(RESULTS_PATH, "w") as f:
        json.dump({"generated_at": datetime.utcnow().isoformat(), "results": results}, f, indent=2)

    print(f"\n{'='*80}")
    print(f"  BATCH COMPLETE — {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"{'='*80}\n")

    completed = [r for r in results if r["status"] == "complete"]
    failed = [r for r in results if r["status"] == "failed"]

    # Summary table
    print(f"{'Ticker':<8} {'Sector':<18} {'Verdict':<14} {'P(beat)':>8} {'P(miss)':>8} {'P(inl)':>8} {'Conv':>6} {'SeedQ':>6} {'Time':>6}")
    print("─" * 92)
    for r in results:
        if r["status"] == "complete":
            print(
                f"{r['ticker']:<8} {r['sector']:<18} {r['verdict']:<14} "
                f"{r['p_beat']:>7.0%} {r['p_miss']:>7.0%} {r['p_inline']:>7.0%} "
                f"{r['convergence_score']:>6.3f} {r['seed_quality'] or 0:>5.2f} "
                f"{r['duration_s']:>5.1f}s"
            )
        else:
            print(f"{r['ticker']:<8} {r['sector']:<18} {'FAILED':<14} {'—':>8} {'—':>8} {'—':>8} {'—':>6} {'—':>6} {r['duration_s']:>5.1f}s")

    # Stats
    print(f"\n{'─'*92}")
    print(f"  Completed: {len(completed)}/{total}")
    print(f"  Failed:    {len(failed)}/{total}")

    if completed:
        verdicts = {}
        for r in completed:
            verdicts[r["verdict"]] = verdicts.get(r["verdict"], 0) + 1
        print(f"\n  Verdict distribution:")
        for v, count in sorted(verdicts.items(), key=lambda x: -x[1]):
            bar = "█" * count
            print(f"    {v:<14} {bar} ({count})")

        durations = [r["duration_s"] for r in completed]
        seed_quals = [r["seed_quality"] for r in completed if r["seed_quality"] is not None]
        convergences = [r["convergence_score"] for r in completed]

        print(f"\n  Duration:     min={min(durations):.0f}s  max={max(durations):.0f}s  avg={sum(durations)/len(durations):.0f}s")
        over_3min = sum(1 for d in durations if d > 180)
        print(f"  Over 3 min:   {over_3min}/{len(completed)}")

        if seed_quals:
            print(f"  Seed quality: min={min(seed_quals):.2f}  max={max(seed_quals):.2f}  avg={sum(seed_quals)/len(seed_quals):.2f}")
            below_05 = sum(1 for q in seed_quals if q < 0.5)
            print(f"  Below 0.50:   {below_05}/{len(seed_quals)}")

        print(f"  Convergence:  min={min(convergences):.3f}  max={max(convergences):.3f}  avg={sum(convergences)/len(convergences):.3f}")

    # Check success criteria
    print(f"\n{'─'*92}")
    print("  SUCCESS CRITERIA:")
    all_same = len(set(r["verdict"] for r in completed)) <= 1 if completed else True
    print(f"  [{'FAIL' if all_same else ' OK '}] Verdict diversity (not all same)")
    seed_ok = all(r["seed_quality"] >= 0.5 for r in completed if r["seed_quality"] is not None) if completed else False
    print(f"  [{'FAIL' if not seed_ok else ' OK '}] Seed quality >= 0.50 for all")
    no_crashes = len(failed) == 0
    print(f"  [{'FAIL' if not no_crashes else ' OK '}] No crashes or failures")
    under_3min = all(r["duration_s"] <= 180 for r in completed) if completed else False
    print(f"  [{'FAIL' if not under_3min else ' OK '}] All simulations under 3 minutes")

    print(f"\n  Results saved to: {RESULTS_PATH}")
    print(f"{'='*80}\n")


if __name__ == "__main__":
    asyncio.run(main())
