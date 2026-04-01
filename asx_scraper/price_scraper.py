"""Price scraper — fetches ASX price history and computes earnings-day reactions."""

from __future__ import annotations

import asyncio
import logging
from datetime import date, timedelta
from typing import Optional

import yfinance as yf

from db.schema import get_pool

logger = logging.getLogger(__name__)

# Price reaction thresholds
BEAT_THRESHOLD = 3.0   # > +3% → market says BEAT
MISS_THRESHOLD = -3.0  # < -3% → market says MISS


class PriceScraper:
    """Fetches ASX price history. Computes price reactions on earnings dates."""

    async def get_price_history(self, ticker: str, days: int = 500) -> list[dict]:
        """Returns daily OHLCV history via yfinance."""
        try:
            def do_fetch():
                stock = yf.Ticker(f"{ticker.upper()}.AX")
                hist = stock.history(period=f"{days}d")
                return hist

            hist = await asyncio.get_event_loop().run_in_executor(None, do_fetch)

            if hist is None or hist.empty:
                logger.warning(f"[price] No history for {ticker}")
                return []

            rows = []
            for dt, row in hist.iterrows():
                rows.append({
                    "date": dt.strftime("%Y-%m-%d"),
                    "open": float(row.get("Open", 0)),
                    "high": float(row.get("High", 0)),
                    "low": float(row.get("Low", 0)),
                    "close": float(row.get("Close", 0)),
                    "volume": int(row.get("Volume", 0)),
                })

            logger.info(f"[price] Fetched {len(rows)} days of history for {ticker}")
            return rows

        except Exception as e:
            logger.error(f"[price] Failed to get history for {ticker}: {e}")
            return []

    async def get_price_reaction(
        self, ticker: str, reporting_date: str
    ) -> dict:
        """Get price reaction around an earnings date.

        Returns price_before, price_after, reaction_pct, implied_result.
        """
        try:
            rd = date.fromisoformat(reporting_date)
        except ValueError:
            return {"error": f"Invalid date: {reporting_date}"}

        # Fetch enough history around the reporting date
        start = rd - timedelta(days=10)
        end = rd + timedelta(days=10)

        try:
            def do_fetch():
                stock = yf.Ticker(f"{ticker.upper()}.AX")
                return stock.history(start=start.isoformat(), end=end.isoformat())

            hist = await asyncio.get_event_loop().run_in_executor(None, do_fetch)

            if hist is None or len(hist) < 2:
                return {"error": "Insufficient price data around reporting date"}

            # Find closest trading day before and after
            dates = [d.date() for d in hist.index]
            before_dates = [d for d in dates if d < rd]
            after_dates = [d for d in dates if d >= rd]

            if not before_dates or not after_dates:
                return {"error": "No trading days found around reporting date"}

            # Day before = last trading day before reporting date
            day_before = max(before_dates)
            # Day after = first trading day on or after reporting date
            day_after = min(after_dates)

            price_before = float(hist.loc[hist.index.date == day_before, "Close"].iloc[0])
            price_after = float(hist.loc[hist.index.date == day_after, "Close"].iloc[0])

            if price_before == 0:
                return {"error": "Zero price before earnings"}

            reaction_pct = round(((price_after - price_before) / price_before) * 100, 2)

            if reaction_pct > BEAT_THRESHOLD:
                implied = "BEAT"
            elif reaction_pct < MISS_THRESHOLD:
                implied = "MISS"
            else:
                implied = "INLINE"

            return {
                "ticker": ticker,
                "reporting_date": reporting_date,
                "price_day_before": round(price_before, 2),
                "price_day_after": round(price_after, 2),
                "price_reaction_pct": reaction_pct,
                "price_implied_result": implied,
            }

        except Exception as e:
            logger.error(f"[price] Reaction calc failed for {ticker} @ {reporting_date}: {e}")
            return {"error": str(e)}

    async def update_earnings_reactions(self, ticker: str) -> int:
        """Update price reactions for all earnings rows missing price data."""
        ticker = ticker.upper()
        updated = 0

        try:
            pool = await get_pool()
            async with pool.acquire() as conn:
                rows = await conn.fetch("""
                    SELECT id, reporting_date FROM asx_earnings
                    WHERE ticker = $1 AND price_reaction_pct IS NULL
                    ORDER BY reporting_date
                """, ticker)

                for row in rows:
                    rd = row["reporting_date"].isoformat()
                    reaction = await self.get_price_reaction(ticker, rd)

                    if "error" in reaction:
                        logger.warning(
                            f"[price] Skipping {ticker} @ {rd}: {reaction['error']}"
                        )
                        continue

                    await conn.execute("""
                        UPDATE asx_earnings SET
                            price_day_before = $1,
                            price_day_after = $2,
                            price_reaction_pct = $3,
                            price_implied_result = $4
                        WHERE id = $5
                    """,
                        reaction["price_day_before"],
                        reaction["price_day_after"],
                        reaction["price_reaction_pct"],
                        reaction["price_implied_result"],
                        row["id"],
                    )
                    updated += 1
                    await asyncio.sleep(1)

            logger.info(f"[price] Updated {updated} price reactions for {ticker}")
        except Exception as e:
            logger.error(f"[price] Failed to update reactions for {ticker}: {e}")

        return updated
