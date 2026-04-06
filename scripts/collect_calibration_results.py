"""Nightly calibration result collector.

Checks yfinance for actual EPS results for any pending calibration rows
where the report_date has passed but actual_beat is still NULL.
Computes Brier score per scored prediction and writes summary stats.
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import date
from typing import Optional

import asyncpg
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)


def _fetch_yfinance_result(ticker: str) -> Optional[dict]:
    """Fetch actual vs estimated EPS from yfinance.

    Returns dict with actual_eps, consensus_eps, beat (bool), surprise_pct
    or None if unavailable.
    """
    try:
        import yfinance as yf
        t = yf.Ticker(f"{ticker}.AX")

        earnings = t.earnings_dates
        if earnings is None or earnings.empty:
            return None

        today = date.today()
        # Filter to past quarters only — most recent first
        past = earnings[earnings.index.date < today]
        if past.empty:
            return None

        latest = past.iloc[0]
        actual = latest.get("Reported EPS")
        estimate = latest.get("EPS Estimate")

        if actual is None or estimate is None:
            return None

        try:
            actual = float(actual)
            estimate = float(estimate)
        except (TypeError, ValueError):
            return None

        # NaN check
        import math
        if math.isnan(actual) or math.isnan(estimate):
            return None

        # Beat = actual > estimate (handles negative EPS correctly:
        # a loss of -0.05 beats an estimate of -0.10)
        beat = actual > estimate
        surprise_pct = (
            (actual - estimate) / abs(estimate) * 100
            if estimate != 0 else None
        )

        return {
            "actual_eps": round(actual, 4),
            "consensus_eps": round(estimate, 4),
            "beat": beat,
            "surprise_pct": (
                round(surprise_pct, 4) if surprise_pct is not None else None
            ),
        }

    except Exception as e:
        logger.debug(f"[calibration] yfinance {ticker}: {e}")
        return None


async def collect_results() -> dict:
    """Run one collection pass. Returns {collected, not_found, total_pending}."""
    conn = await asyncpg.connect(os.environ["DATABASE_URL"])

    pending = await conn.fetch("""
        SELECT id, ticker, report_date, augur_probability, augur_verdict
        FROM calibration
        WHERE actual_beat IS NULL
          AND report_date < CURRENT_DATE
          AND report_date > CURRENT_DATE - 90
        ORDER BY report_date ASC
    """)

    logger.info(f"[calibration] {len(pending)} pending results to check")
    print(f"Checking {len(pending)} pending calibration rows...")

    collected = 0
    not_found: list[str] = []

    for row in pending:
        ticker = row["ticker"]
        result = _fetch_yfinance_result(ticker)

        if result is None:
            not_found.append(ticker)
            logger.info(f"[calibration] {ticker}: no result yet")
            continue

        p = float(row["augur_probability"])
        outcome = 1.0 if result["beat"] else 0.0
        brier = round((p - outcome) ** 2, 6)

        await conn.execute("""
            UPDATE calibration SET
                actual_beat        = $1,
                actual_eps         = $2,
                consensus_eps      = $3,
                eps_surprise_pct   = $4,
                result_source      = 'yfinance',
                result_verified_at = NOW(),
                brier_score        = $5
            WHERE id = $6
        """,
            result["beat"], result["actual_eps"], result["consensus_eps"],
            result["surprise_pct"], brier, row["id"],
        )

        direction = "BEAT" if result["beat"] else "MISS"
        correct = (result["beat"] and p >= 0.5) or (not result["beat"] and p < 0.5)

        logger.info(
            f"[calibration] {ticker}: actual={direction} P={p:.3f} "
            f"brier={brier:.4f} {'CORRECT' if correct else 'WRONG'}"
        )
        print(
            f"  {ticker:6} {direction:4} P={p:.3f} brier={brier:.4f} "
            f"{'CORRECT' if correct else 'WRONG'}"
        )
        collected += 1
        await asyncio.sleep(0.3)

    print(f"\nCollected: {collected} | Not yet available: {len(not_found)}")
    if not_found:
        print(f"No result yet: {', '.join(not_found[:10])}")

    # Overall summary
    summary = await conn.fetchrow("""
        SELECT
            COUNT(*) FILTER (WHERE actual_beat IS NOT NULL) AS with_outcome,
            COUNT(*) FILTER (
                WHERE (actual_beat = TRUE AND augur_probability >= 0.5)
                OR (actual_beat = FALSE AND augur_probability < 0.5)
            ) AS correct,
            AVG(brier_score) AS avg_brier
        FROM calibration
    """)
    if summary and summary["with_outcome"]:
        total = summary["with_outcome"]
        correct_n = summary["correct"] or 0
        accuracy = correct_n / total * 100
        avg_brier = float(summary["avg_brier"]) if summary["avg_brier"] is not None else 0.0
        print(
            f"\nOverall: {correct_n}/{total} correct ({accuracy:.1f}%) | "
            f"Avg Brier: {avg_brier:.4f}"
        )

    await conn.close()
    return {
        "collected": collected,
        "not_found": len(not_found),
        "total_pending": len(pending),
    }


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    asyncio.run(collect_results())
