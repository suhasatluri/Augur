"""Test script — run a full 5-round negotiation for an existing simulation."""

from __future__ import annotations

import asyncio
import logging
import os
import sys

from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))

from negotiation_runner.runner import NegotiationRunner


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    if len(sys.argv) < 2:
        # Find the most recent simulation
        from db.schema import get_pool
        pool = await get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT id, ticker FROM simulations ORDER BY created_at DESC LIMIT 1"
            )
        if not row:
            print("No simulations found. Run persona_forge first.")
            return
        sim_id = row["id"]
        ticker = row["ticker"]
        print(f"Using most recent simulation: {sim_id} ({ticker})")
    else:
        sim_id = sys.argv[1]
        ticker = sys.argv[2] if len(sys.argv) > 2 else "BHP"

    print(f"\n{'='*70}")
    print(f"  AUGUR Negotiation Runner")
    print(f"  Simulation: {sim_id}")
    print(f"  Ticker: {ticker}")
    print(f"  Rounds: 5")
    print(f"{'='*70}\n")

    runner = NegotiationRunner()
    result = await runner.run(simulation_id=sim_id, ticker=ticker)

    # --- Round-by-round results ---
    print(f"\n{'─'*70}")
    print(f"  ROUND-BY-ROUND PROGRESSION")
    print(f"{'─'*70}")
    for rs in result.round_summaries:
        bar_pos = int(rs.mean_probability * 40)
        bar = "░" * bar_pos + "█" + "░" * (40 - bar_pos)
        print(f"\n  Round {rs.round_number}:")
        print(f"    Mean P(beat): {rs.mean_probability:.3f}  [{bar}]")
        print(f"    Median: {rs.median_probability:.3f}  StdDev: {rs.std_dev:.3f}")
        print(f"    Bulls: {rs.bull_count}  Neutral: {rs.neutral_count}  Bears: {rs.bear_count}")
        if rs.biggest_mover:
            print(f"    Biggest mover: {rs.biggest_mover} (±{rs.biggest_move_delta:.3f})")
        print(f"    Narrative: {rs.narrative}")

    # --- Final result ---
    print(f"\n{'─'*70}")
    print(f"  FINAL RESULT")
    print(f"{'─'*70}")
    score_bar = "▓" * int(result.convergence_score * 20) + "░" * (20 - int(result.convergence_score * 20))
    print(f"  Final Mean P(beat):   {result.final_mean_probability:.3f}")
    print(f"  Final Median P(beat): {result.final_median_probability:.3f}")
    print(f"  Final Std Dev:        {result.final_std_dev:.3f}")
    print(f"  Convergence Score:    {result.convergence_score:.3f} [{score_bar}]")

    if result.high_uncertainty:
        print(f"\n  ⚠  HIGH UNCERTAINTY — std dev {result.final_std_dev:.3f} > 0.25")
        print(f"     Agents failed to reach consensus. Prediction unreliable.")
    else:
        # Interpret the result
        p = result.final_mean_probability
        if p >= 0.65:
            verdict = "LIKELY BEAT"
        elif p >= 0.55:
            verdict = "LEAN BEAT"
        elif p >= 0.45:
            verdict = "TOSS-UP"
        elif p >= 0.35:
            verdict = "LEAN MISS"
        else:
            verdict = "LIKELY MISS"
        print(f"\n  VERDICT: {verdict}")

    print(f"\n  Duration: {result.duration_ms:.0f}ms ({result.duration_ms/1000:.1f}s)")
    print(f"  Status: {result.status}")

    print(f"\n{'='*70}")
    print(f"  NEGOTIATION COMPLETE")
    print(f"{'='*70}\n")


if __name__ == "__main__":
    asyncio.run(main())
