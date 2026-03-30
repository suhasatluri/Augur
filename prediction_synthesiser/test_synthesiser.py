"""Test script — synthesise prediction report for a completed simulation."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys

from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))

from prediction_synthesiser.synthesiser import PredictionSynthesiser


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    if len(sys.argv) < 2:
        from db.schema import get_pool
        pool = await get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT id, ticker FROM simulations WHERE status = 'complete' ORDER BY completed_at DESC LIMIT 1"
            )
        if not row:
            print("No completed simulations found.")
            return
        sim_id = row["id"]
        print(f"Using most recent completed simulation: {sim_id} ({row['ticker']})")
    else:
        sim_id = sys.argv[1]

    synth = PredictionSynthesiser()
    report = await synth.synthesise(sim_id)

    # --- Human-readable output ---
    print(f"\n{'='*70}")
    print(f"  AUGUR PREDICTION REPORT — {report.ticker}")
    print(f"{'='*70}")

    print(f"\n  Simulation: {report.simulation_id}")
    print(f"  Generated:  {report.generated_at.strftime('%Y-%m-%d %H:%M UTC')}")

    # Verdict
    print(f"\n{'─'*70}")
    print(f"  VERDICT: {report.verdict}")
    print(f"{'─'*70}")

    # Distribution
    d = report.distribution
    print(f"\n  Probability Distribution:")
    beat_bar = "█" * int(d.p_beat * 30)
    miss_bar = "█" * int(d.p_miss * 30)
    inline_bar = "█" * int(d.p_inline * 30)
    print(f"    P(beat):   {d.p_beat:5.1%}  {beat_bar}")
    print(f"    P(miss):   {d.p_miss:5.1%}  {miss_bar}")
    print(f"    P(inline): {d.p_inline:5.1%}  {inline_bar}")
    print(f"\n    Mean:  {d.mean_probability:.3f}   Median: {d.median_probability:.3f}")
    print(f"    Band:  {d.confidence_band_low:.3f} — {d.confidence_band_high:.3f}  (±1σ)")

    # Convergence
    conv_bar = "▓" * int(report.convergence_score * 20) + "░" * (20 - int(report.convergence_score * 20))
    print(f"\n  Convergence: {report.convergence_score:.3f} [{conv_bar}]")
    if report.high_uncertainty:
        print(f"  ⚠  HIGH UNCERTAINTY — agents did not converge")

    # Swing factors
    print(f"\n{'─'*70}")
    print(f"  TOP SWING FACTORS")
    print(f"{'─'*70}")
    for i, sf in enumerate(report.swing_factors, 1):
        print(f"\n  {i}. {sf.theme} (mentions: {sf.mentions}, disagreement: {sf.disagreement_score:.2f})")
        print(f"     Bull: {sf.bull_view}")
        print(f"     Bear: {sf.bear_view}")

    # Sentiment cascade
    sc = report.sentiment_cascade
    print(f"\n{'─'*70}")
    print(f"  SENTIMENT CASCADE RISK")
    print(f"{'─'*70}")
    print(f"  Direction: {sc.direction}")
    print(f"  Severity:  {sc.severity}")
    print(f"  Retail conviction: {sc.retail_conviction:.2f}")
    print(f"  Assessment: {sc.reasoning}")

    # Human summary
    print(f"\n{'─'*70}")
    print(f"  SUMMARY")
    print(f"{'─'*70}")
    print(f"\n  {report.human_summary}")

    # JSON export
    print(f"\n{'─'*70}")
    print(f"  JSON EXPORT")
    print(f"{'─'*70}")
    print(json.dumps(report.model_dump(mode="json"), indent=2, default=str))

    print(f"\n{'='*70}")
    print(f"  {report.disclaimer}")
    print(f"{'='*70}\n")


if __name__ == "__main__":
    asyncio.run(main())
