from datetime import datetime

from seed_harvester.cache import LayeredCache
from seed_harvester.models import Seed, SeedType


def _make_seed(ticker="BHP"):
    return Seed(
        ticker=ticker,
        seed_type=SeedType.FINANCIAL,
        content="Test seed content for unit tests",
        confidence=0.7,
        source="test",
    )


class TestLayeredCache:

    def setup_method(self):
        self.cache = LayeredCache()

    def test_put_and_get_slow(self):
        """Slow layer round-trip works"""
        seeds = [_make_seed()]
        self.cache.put("BHP", "slow", seeds)
        entry = self.cache.get("BHP", "slow")
        assert entry is not None
        assert len(entry.seeds) == 1
        assert entry.layer == "slow"

    def test_put_and_get_fast(self):
        """Fast layer round-trip works"""
        seeds = [_make_seed()]
        self.cache.put("CBA", "fast", seeds)
        entry = self.cache.get("CBA", "fast")
        assert entry is not None
        assert entry.layer == "fast"

    def test_cache_miss_returns_none(self):
        """Missing key returns None"""
        entry = self.cache.get("XRO", "slow")
        assert entry is None

    def test_invalidate_fast(self):
        """Invalidated entry returns None on next get"""
        self.cache.put("BHP", "fast", [_make_seed()])
        assert self.cache.invalidate_all_fast("BHP") is True
        entry = self.cache.get("BHP", "fast")
        assert entry is None

    def test_clear_ticker(self):
        """Clear removes only that ticker's entries"""
        self.cache.put("BHP", "slow", [_make_seed("BHP")])
        self.cache.put("CBA", "slow", [_make_seed("CBA")])
        self.cache.clear("BHP")
        assert self.cache.get("BHP", "slow") is None
        assert self.cache.get("CBA", "slow") is not None

    def test_clear_all(self):
        """Clear with no ticker empties everything"""
        self.cache.put("BHP", "slow", [_make_seed()])
        self.cache.put("CBA", "fast", [_make_seed()])
        self.cache.clear()
        assert self.cache.stats()["entries"] == 0

    def test_ticker_uppercased(self):
        """Keys are uppercased internally"""
        self.cache.put("bhp", "slow", [_make_seed()])
        entry = self.cache.get("BHP", "slow")
        assert entry is not None
