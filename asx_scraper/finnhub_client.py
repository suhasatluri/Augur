"""Finnhub consensus data — fetches actual vs estimate EPS for ASX tickers.

Works for dual-listed tickers (BHP, CSL, RIO, WES, WOW, TLS, etc.)
via their US exchange listings. Returns empty for ASX-only tickers.
Falls back to price proxy in that case (handled by orchestrator).

Free tier: 60 calls/min, US equities + some international.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import ssl
import urllib.request
from datetime import date as date_type, timedelta
from typing import Optional

from db.schema import get_pool

logger = logging.getLogger(__name__)

FINNHUB_BASE = "https://finnhub.io/api/v1"

# Finnhub uses US-style quarterly periods. ASX companies report half-yearly.
# Map Finnhub period_end → closest ASX reporting_date (within 90 days).
_MATCH_WINDOW_DAYS = 90

# Beat/miss thresholds on surprisePercent
_BEAT_THRESHOLD = 1.0    # > +1% surprise → BEAT
_MISS_THRESHOLD = -1.0   # < -1% surprise → MISS


def _classify(surprise_pct: float) -> str:
    if surprise_pct > _BEAT_THRESHOLD:
        return "BEAT"
    elif surprise_pct < _MISS_THRESHOLD:
        return "MISS"
    return "INLINE"


class FinnhubClient:
    """Fetches consensus EPS data from Finnhub for ASX tickers."""

    def __init__(self, api_key: Optional[str] = None) -> None:
        self.api_key = api_key or os.getenv("FINNHUB_API_KEY", "")
        if not self.api_key:
            logger.warning("[finnhub] No FINNHUB_API_KEY set")

    async def _fetch(self, endpoint: str, params: dict) -> list | dict:
        """Make a Finnhub API request."""
        params["token"] = self.api_key
        qs = "&".join(f"{k}={v}" for k, v in params.items())
        url = f"{FINNHUB_BASE}/{endpoint}?{qs}"

        try:
            ctx = ssl.create_default_context()
            req = urllib.request.Request(url, headers={
                "User-Agent": "Augur/1.0",
            })

            def do_fetch():
                with urllib.request.urlopen(req, context=ctx, timeout=15) as resp:
                    return json.loads(resp.read().decode("utf-8"))

            return await asyncio.get_event_loop().run_in_executor(None, do_fetch)
        except Exception as e:
            logger.debug(f"[finnhub] API error: {e}")
            return []

    async def get_earnings(self, ticker: str) -> list[dict]:
        """Fetch earnings history for a ticker.

        Tries bare symbol first (catches dual-listed), then .AX suffix.
        Returns list of {period, actual, estimate, surprisePercent, symbol}.
        """
        ticker = ticker.upper()

        # Try bare symbol (works for BHP, CSL, RIO on NYSE)
        data = await self._fetch("stock/earnings", {"symbol": ticker})
        if isinstance(data, list) and data:
            logger.info(f"[finnhub] {ticker}: {len(data)} quarters from bare symbol")
            return data

        # Try .AX suffix
        data = await self._fetch("stock/earnings", {"symbol": f"{ticker}.AX"})
        if isinstance(data, list) and data:
            logger.info(f"[finnhub] {ticker}: {len(data)} quarters from .AX")
            return data

        logger.info(f"[finnhub] {ticker}: no earnings data available")
        return []

    async def update_consensus(self, ticker: str) -> int:
        """Fetch Finnhub earnings and update asx_earnings with consensus data.

        Matches Finnhub quarterly periods to ASX reporting dates within 90 days.
        Updates eps_consensus_cents, surprise_pct, and beat_miss.
        Returns number of rows updated.
        """
        ticker = ticker.upper()
        earnings = await self.get_earnings(ticker)
        if not earnings:
            return 0

        try:
            pool = await get_pool()
            updated = 0

            async with pool.acquire() as conn:
                # Get existing earnings rows
                rows = await conn.fetch("""
                    SELECT id, reporting_date, period_end_date, eps_basic_cents
                    FROM asx_earnings
                    WHERE ticker = $1
                    ORDER BY reporting_date DESC
                """, ticker)

                if not rows:
                    return 0

                for fh in earnings:
                    fh_period_end = fh.get("period")
                    fh_actual = fh.get("actual")
                    fh_estimate = fh.get("estimate")
                    fh_surprise_pct = fh.get("surprisePercent")

                    if not fh_period_end or fh_surprise_pct is None:
                        continue

                    try:
                        fh_date = date_type.fromisoformat(fh_period_end)
                    except (ValueError, TypeError):
                        continue

                    # Find matching ASX earnings row by period_end_date or reporting_date proximity
                    best_match = None
                    best_delta = timedelta(days=_MATCH_WINDOW_DAYS + 1)

                    for row in rows:
                        # Try period_end_date first
                        row_date = row.get("period_end_date") or row["reporting_date"]
                        delta = abs(fh_date - row_date)
                        if delta < best_delta:
                            best_delta = delta
                            best_match = row

                    if not best_match or best_delta.days > _MATCH_WINDOW_DAYS:
                        continue

                    # Convert Finnhub EPS (dollars) to cents for consensus
                    consensus_cents = round(fh_estimate * 100, 2) if fh_estimate else None

                    beat_miss = _classify(fh_surprise_pct)

                    await conn.execute("""
                        UPDATE asx_earnings SET
                            eps_consensus_cents = COALESCE($1, eps_consensus_cents),
                            surprise_pct = $2,
                            beat_miss = $3,
                            data_source = CASE
                                WHEN data_source = 'pdf' THEN 'pdf+finnhub'
                                ELSE 'finnhub'
                            END
                        WHERE id = $4
                    """,
                        consensus_cents,
                        round(fh_surprise_pct, 2),
                        beat_miss,
                        best_match["id"],
                    )
                    updated += 1
                    logger.info(
                        f"[finnhub] {ticker} @ {best_match['reporting_date']}: "
                        f"est={fh_estimate}, actual={fh_actual}, "
                        f"surprise={fh_surprise_pct:+.1f}% → {beat_miss}"
                    )

            return updated

        except Exception as e:
            logger.error(f"[finnhub] Update failed for {ticker}: {e}")
            return 0

    async def update_all(self, tickers: list[str]) -> dict:
        """Update consensus data for multiple tickers. Returns summary."""
        results = {"updated": {}, "no_data": [], "errors": []}
        for ticker in tickers:
            try:
                count = await self.update_consensus(ticker)
                if count > 0:
                    results["updated"][ticker] = count
                else:
                    results["no_data"].append(ticker)
            except Exception as e:
                results["errors"].append(f"{ticker}: {e}")
            await asyncio.sleep(1)  # Rate limit
        return results
