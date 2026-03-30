"""Layered cache — in-memory fallback (R2 integration TODO)."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

from seed_harvester.models import CacheEntry, Seed
from seed_harvester.slow_layer import SLOW_TTL_SECONDS
from seed_harvester.fast_layer import FAST_TTL_SECONDS

logger = logging.getLogger(__name__)


class LayeredCache:
    """Two-tier cache: slow (7-day) and fast (2-hour) with announcement invalidation.

    Current implementation: in-memory dict.
    Future: Cloudflare R2 for persistence, Neon PG for metadata.
    """

    def __init__(self) -> None:
        self._store: dict[str, CacheEntry] = {}

    def _key(self, ticker: str, layer: str) -> str:
        return f"{ticker.upper()}:{layer}"

    def get(self, ticker: str, layer: str) -> Optional[CacheEntry]:
        key = self._key(ticker, layer)
        entry = self._store.get(key)
        if entry is None:
            return None
        if entry.is_expired:
            logger.info(f"[cache] {key} expired, removing")
            del self._store[key]
            return None
        return entry

    def put(self, ticker: str, layer: str, seeds: list[Seed]) -> CacheEntry:
        ttl = SLOW_TTL_SECONDS if layer == "slow" else FAST_TTL_SECONDS
        entry = CacheEntry(
            ticker=ticker.upper(),
            seeds=seeds,
            layer=layer,
            created_at=datetime.utcnow(),
            ttl_seconds=ttl,
        )
        key = self._key(ticker, layer)
        self._store[key] = entry
        logger.info(f"[cache] Stored {len(seeds)} seeds at {key} (TTL={ttl}s)")
        return entry

    def invalidate(self, ticker: str, layer: str = "fast") -> bool:
        """Invalidate cache entry. Used when ASX announcements arrive."""
        key = self._key(ticker, layer)
        entry = self._store.get(key)
        if entry:
            entry.invalidated = True
            logger.info(f"[cache] Invalidated {key}")
            return True
        return False

    def invalidate_all_fast(self, ticker: str) -> bool:
        """Invalidate fast layer for a ticker (triggered by ASX announcements)."""
        return self.invalidate(ticker, "fast")

    def clear(self, ticker: Optional[str] = None) -> None:
        if ticker:
            keys = [k for k in self._store if k.startswith(ticker.upper() + ":")]
            for k in keys:
                del self._store[k]
        else:
            self._store.clear()

    def stats(self) -> dict:
        return {
            "entries": len(self._store),
            "tickers": list({e.ticker for e in self._store.values()}),
        }
