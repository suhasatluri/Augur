"""Test script — harvest seeds for a ticker and print raw output."""

from __future__ import annotations

import asyncio
import logging
import os
import sys

from dotenv import load_dotenv

# Add parent to path so seed_harvester is importable
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))

from seed_harvester.harvester import SeedHarvester


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    ticker = sys.argv[1] if len(sys.argv) > 1 else "BHP"
    force = "--force" in sys.argv or "force_refresh=True" in " ".join(sys.argv)

    print(f"\n{'='*60}")
    print(f"  AUGUR Seed Harvester — {ticker}")
    print(f"  force_refresh={force}")
    print(f"{'='*60}\n")

    harvester = SeedHarvester()
    result = await harvester.harvest(ticker=ticker, force_refresh=force)

    # --- Structured Data & Bias Score ---
    if result.ticker_bias_score is not None:
        print(f"\n{'─'*60}")
        print(f"  STRUCTURED DATA (yfinance)")
        print(f"{'─'*60}")
        yf = (result.structured_data or {}).get("source_yfinance", {})
        print(f"  Company:     {yf.get('longName', 'N/A')}")
        print(f"  Sector:      {yf.get('sector', 'N/A')} / {yf.get('industry', 'N/A')}")
        print(f"  Price:       A${yf.get('currentPrice', 'N/A')}  →  Target: A${yf.get('targetMeanPrice', 'N/A')}")
        print(f"  Rec:         {yf.get('recommendationMean', 'N/A')} ({yf.get('recommendationKey', 'N/A')})")
        print(f"  EGrowth:     {yf.get('earningsGrowth', 'N/A')}   RevGrowth: {yf.get('revenueGrowth', 'N/A')}")
        print(f"  ROE:         {yf.get('returnOnEquity', 'N/A')}   D/E: {yf.get('debtToEquity', 'N/A')}")
        print(f"  fwdPE:       {yf.get('forwardPE', 'N/A')}   trPE: {yf.get('trailingPE', 'N/A')}")
        print(f"  Earnings:    {yf.get('nextEarningsDate', 'N/A')}")
        bias_bar = "▓" * int(result.ticker_bias_score * 20) + "░" * (20 - int(result.ticker_bias_score * 20))
        print(f"\n  TICKER BIAS SCORE: {result.ticker_bias_score:.3f} [{bias_bar}]")

    # --- Quality Report ---
    q = result.quality
    print(f"\n{'─'*60}")
    print(f"  QUALITY REPORT")
    print(f"{'─'*60}")
    if q:
        bar_len = int(q.overall_score * 20)
        bar = "█" * bar_len + "░" * (20 - bar_len)
        print(f"  Overall Score:    {q.overall_score:.2f} [{bar}]")
        print(f"  Avg Confidence:   {q.avg_confidence:.3f}")
        print(f"  Category Coverage:")
        for cat, count in sorted(q.category_coverage.items()):
            print(f"    {cat.upper():12s} {'●' * count} ({count})")
        print(f"  Earnings History: {'YES' if q.has_earnings_history else 'NO — missing historical baseline'}")
        print(f"  Analyst Consensus:{'YES' if q.has_consensus else 'NO — missing market expectations anchor'}")

        if q.warnings:
            print(f"\n  ⚠ WARNINGS ({len(q.warnings)}):")
            for w in q.warnings:
                print(f"    • {w}")
    else:
        print("  (no quality report available)")

    # --- Harvest Summary ---
    print(f"\n{'─'*60}")
    print(f"  HARVEST SUMMARY")
    print(f"{'─'*60}")
    print(f"  Ticker:           {result.ticker}")
    print(f"  Total Seeds:      {len(result.seeds)}")
    print(f"  Slow Layer:       {'CACHED' if result.slow_layer_cached else 'FRESH (API call)'}")
    print(f"  Fast Layer:       {'CACHED' if result.fast_layer_cached else 'FRESH (API call)'}")
    print(f"  Duration:         {result.harvest_duration_ms:.0f}ms")

    # --- Seeds by Layer ---
    slow_seeds = [s for s in result.seeds if s.source == "general knowledge" or s.seed_type.value in ("financial", "guidance", "sector", "macro") and s.confidence >= 0.4]
    fast_seeds = [s for s in result.seeds if s not in slow_seeds] if len(result.seeds) > 8 else []

    print(f"\n{'─'*60}")
    print(f"  SEEDS ({len(result.seeds)} total)")
    print(f"{'─'*60}")

    for i, seed in enumerate(result.seeds, 1):
        conf_bar = "▓" * int(seed.confidence * 10) + "░" * (10 - int(seed.confidence * 10))
        print(f"\n  [{i:2d}] {seed.seed_type.value.upper()}")
        print(f"       Confidence: {seed.confidence:.2f} [{conf_bar}]")
        print(f"       Content:    {seed.content}")
        print(f"       Source:     {seed.source}")
        print(f"       Reasoning:  {seed.reasoning}")

    print(f"\n{'='*60}")
    print(f"  HARVEST COMPLETE")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    asyncio.run(main())
