"""Metrics computer — derives beat rates, credibility scores from asx_earnings."""

from __future__ import annotations

import logging
from typing import Optional

from db.schema import get_pool

logger = logging.getLogger(__name__)


class MetricsComputer:
    """Computes derived metrics from asx_earnings. Runs after each new result."""

    async def compute(self, ticker: str) -> dict:
        """Reads asx_earnings, computes and upserts to asx_metrics."""
        ticker = ticker.upper()

        try:
            pool = await get_pool()
            async with pool.acquire() as conn:
                # Fetch all earnings rows, most recent first
                rows = await conn.fetch("""
                    SELECT beat_miss, surprise_pct, data_source, data_confidence
                    FROM asx_earnings
                    WHERE ticker = $1
                    ORDER BY reporting_date DESC
                """, ticker)

                if not rows:
                    logger.info(f"[metrics] No earnings data for {ticker}")
                    return {"ticker": ticker, "error": "no earnings data"}

                total = len(rows)

                # Beat rates
                def calc_beat_rate(subset):
                    known = [r for r in subset if r["beat_miss"] in ("BEAT", "MISS", "INLINE")]
                    if not known:
                        return None
                    return sum(1 for r in known if r["beat_miss"] == "BEAT") / len(known)

                beat_rate_8q = calc_beat_rate(rows[:8])
                beat_rate_4q = calc_beat_rate(rows[:4])

                # Average surprise
                surprises = [r["surprise_pct"] for r in rows if r["surprise_pct"] is not None]
                avg_surprise = sum(surprises) / len(surprises) if surprises else None

                # Guidance delivery rate (placeholder — requires commentary analysis)
                guidance_delivery_rate = None

                # Management credibility score
                mgmt_credibility = None
                if beat_rate_4q is not None and beat_rate_8q is not None:
                    gdr = guidance_delivery_rate if guidance_delivery_rate is not None else 0.5
                    mgmt_credibility = round(
                        beat_rate_4q * 0.5 + beat_rate_8q * 0.3 + gdr * 0.2,
                        3,
                    )

                # Data confidence
                pdf_count = sum(1 for r in rows if r["data_source"] == "pdf")
                if pdf_count >= 6:
                    data_confidence = "HIGH"
                elif total >= 4:
                    data_confidence = "MED"
                else:
                    data_confidence = "LOW"

                # Upsert
                await conn.execute("""
                    INSERT INTO asx_metrics
                        (ticker, beat_rate_8q, beat_rate_4q, avg_surprise_pct,
                         guidance_delivery_rate, mgmt_credibility_score,
                         data_confidence, quarters_available, last_computed)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, NOW())
                    ON CONFLICT (ticker) DO UPDATE SET
                        beat_rate_8q = EXCLUDED.beat_rate_8q,
                        beat_rate_4q = EXCLUDED.beat_rate_4q,
                        avg_surprise_pct = EXCLUDED.avg_surprise_pct,
                        guidance_delivery_rate = EXCLUDED.guidance_delivery_rate,
                        mgmt_credibility_score = EXCLUDED.mgmt_credibility_score,
                        data_confidence = EXCLUDED.data_confidence,
                        quarters_available = EXCLUDED.quarters_available,
                        last_computed = NOW()
                """,
                    ticker,
                    beat_rate_8q,
                    beat_rate_4q,
                    avg_surprise,
                    guidance_delivery_rate,
                    mgmt_credibility,
                    data_confidence,
                    total,
                )

                result = {
                    "ticker": ticker,
                    "beat_rate_8q": beat_rate_8q,
                    "beat_rate_4q": beat_rate_4q,
                    "avg_surprise_pct": avg_surprise,
                    "mgmt_credibility_score": mgmt_credibility,
                    "data_confidence": data_confidence,
                    "quarters_available": total,
                }

                logger.info(
                    f"[metrics] {ticker}: beat_rate_8q={beat_rate_8q}, "
                    f"beat_rate_4q={beat_rate_4q}, confidence={data_confidence}"
                )
                return result

        except Exception as e:
            logger.error(f"[metrics] Failed for {ticker}: {e}")
            return {"ticker": ticker, "error": str(e)}

    async def compute_all(self, tickers: list[str]) -> list[dict]:
        """Recomputes metrics for all tickers."""
        results = []
        for ticker in tickers:
            result = await self.compute(ticker)
            results.append(result)
        return results
