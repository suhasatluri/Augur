"""SeedHarvester — orchestrates layered cache and agent harvesting."""

from __future__ import annotations

import logging
import time
from typing import Optional

import anthropic

from seed_harvester.cache import LayeredCache
from seed_harvester.fast_layer import check_asx_announcements, harvest_fast
from seed_harvester.slow_layer import harvest_slow
from seed_harvester.models import Seed, HarvestResponse
from seed_harvester.quality import score_harvest

logger = logging.getLogger(__name__)


class SeedHarvester:
    """Main entry point. Checks cache layers, dispatches to agents on miss."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        cache: Optional[LayeredCache] = None,
    ) -> None:
        self.client = anthropic.AsyncAnthropic(api_key=api_key)
        self.cache = cache or LayeredCache()

    async def harvest(
        self,
        ticker: str,
        force_refresh: bool = False,
        company_name: str = "",
        reporting_period: str = "",
    ) -> HarvestResponse:
        """Harvest seeds for a ticker through the layered cache."""
        start = time.monotonic()
        ticker = ticker.upper()
        all_seeds: list[Seed] = []
        slow_seeds: list[Seed] = []
        slow_cached = False
        fast_cached = False

        # --- Slow layer (7-day TTL) ---
        if not force_refresh:
            entry = self.cache.get(ticker, "slow")
            if entry:
                slow_seeds = entry.seeds
                all_seeds.extend(slow_seeds)
                slow_cached = True
                logger.info(f"[harvester] Slow layer cache hit for {ticker}")

        ticker_bias_score = None
        structured_data = None

        if not slow_cached:
            slow_seeds, structured_data, ticker_bias_score = await harvest_slow(
                self.client, ticker, company_name, reporting_period
            )
            if slow_seeds:
                self.cache.put(ticker, "slow", slow_seeds)
                all_seeds.extend(slow_seeds)

        # --- Fast layer (2-hour TTL + announcement invalidation) ---
        # Check for ASX announcements that should invalidate fast cache
        if await check_asx_announcements(ticker):
            self.cache.invalidate_all_fast(ticker)
            logger.info(f"[harvester] Fast cache invalidated by ASX announcement for {ticker}")

        if not force_refresh:
            entry = self.cache.get(ticker, "fast")
            if entry:
                all_seeds.extend(entry.seeds)
                fast_cached = True
                logger.info(f"[harvester] Fast layer cache hit for {ticker}")

        if not fast_cached:
            seeds = await harvest_fast(
                self.client, ticker, slow_seeds, reporting_period
            )
            if seeds:
                self.cache.put(ticker, "fast", seeds)
                all_seeds.extend(seeds)

        elapsed = (time.monotonic() - start) * 1000

        response = HarvestResponse(
            ticker=ticker,
            seeds=all_seeds,
            slow_layer_cached=slow_cached,
            fast_layer_cached=fast_cached,
            harvest_duration_ms=round(elapsed, 1),
            ticker_bias_score=ticker_bias_score,
            structured_data=structured_data,
        )

        response.quality = score_harvest(response)
        return response
