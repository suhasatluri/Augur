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

    print(f"\n--- Results for {result.ticker} ---")
    print(f"Total seeds: {len(result.seeds)}")
    print(f"Slow layer cached: {result.slow_layer_cached}")
    print(f"Fast layer cached: {result.fast_layer_cached}")
    print(f"Duration: {result.harvest_duration_ms}ms\n")

    for i, seed in enumerate(result.seeds, 1):
        print(f"[{i}] {seed.seed_type.value.upper()} (confidence: {seed.confidence:.2f})")
        print(f"    Content:   {seed.content}")
        print(f"    Source:    {seed.source}")
        print(f"    Reasoning: {seed.reasoning}")
        print()


if __name__ == "__main__":
    asyncio.run(main())
