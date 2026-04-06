"""ASX earnings calendar harvester — dual-source: yfinance + Perplexity Sonar.

Data priority: manual > yfinance+perplexity (high) > yfinance (medium) > perplexity (low)

Cost: ~40 Perplexity calls/week for gap-filling ≈ $0.20/week.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from datetime import date, datetime, timedelta
from typing import Optional

logger = logging.getLogger(__name__)

_LARGE_CAPS = frozenset({
    "BHP", "CBA", "CSL", "NAB", "WBC", "ANZ", "WES", "MQG", "FMG", "WDS",
    "TLS", "WOW", "RIO", "ALL", "GMG",
})

_PERPLEXITY_PROMPT = """You are a financial data assistant.
Find the next upcoming earnings/results reporting date for {ticker} (ASX:{ticker}, {company_name}).

Australian companies typically report:
- Half-year results (H1) in Feb or Aug
- Full-year results (FY) in Aug or Feb
- Some companies report quarterly

Today is {today}.

Return ONLY valid JSON with no other text:
{{
  "report_date": "YYYY-MM-DD or null if unknown",
  "report_type": "H1 FY2026 or FY2026 or null",
  "confidence": "high/medium/low",
  "source_hint": "brief description of where date came from"
}}

Rules:
- report_date must be a future date (after {today})
- If you cannot find a specific upcoming date, return null for report_date
- Do not invent dates — only return dates you found from real sources
- confidence=high only if from ASX announcements or company IR page
- confidence=medium if from financial data aggregator
- confidence=low if estimated from historical reporting pattern"""


def _parse_date(date_str: str) -> Optional[date]:
    """Parse various date formats into a date object."""
    if not date_str or date_str == "null":
        return None
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d %b %Y", "%B %d, %Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(date_str.strip(), fmt).date()
        except ValueError:
            pass
    m = re.search(r"\d{4}-\d{2}-\d{2}", date_str)
    if m:
        try:
            return datetime.strptime(m.group(), "%Y-%m-%d").date()
        except ValueError:
            pass
    return None


async def _try_yfinance(ticker: str) -> Optional[tuple[date, Optional[str]]]:
    """Try to get next earnings date from yfinance. Returns (date, report_type) or None."""
    try:
        import yfinance as yf

        def do_fetch():
            t = yf.Ticker(f"{ticker}.AX")
            cal = t.calendar
            if cal is None:
                return None

            # yfinance calendar can be a dict or DataFrame
            if isinstance(cal, dict):
                earnings_dates = cal.get("Earnings Date", [])
            else:
                return None

            if not earnings_dates:
                return None

            today = date.today()
            future_dates = []
            for d in earnings_dates:
                dt = d.date() if hasattr(d, "date") else d
                if isinstance(dt, date) and dt >= today:
                    future_dates.append(dt)

            if not future_dates:
                return None

            next_date = min(future_dates)
            month = next_date.month
            year = next_date.year
            if month in (1, 2, 3):
                report_type = f"H1 FY{year}"
            elif month in (7, 8, 9):
                report_type = f"FY{year}"
            else:
                report_type = None

            return next_date, report_type

        return await asyncio.wait_for(
            asyncio.get_event_loop().run_in_executor(None, do_fetch),
            timeout=15.0,
        )
    except Exception as e:
        logger.debug(f"[calendar] yfinance {ticker}: {e}")
        return None


async def _try_perplexity(
    ticker: str, company_name: str,
) -> Optional[tuple[date, Optional[str], str, str]]:
    """Try to get earnings date from Perplexity. Returns (date, report_type, confidence, raw_text) or None."""
    api_key = os.getenv("PERPLEXITY_API_KEY")
    if not api_key:
        return None

    try:
        import requests as req_lib

        prompt = _PERPLEXITY_PROMPT.format(
            ticker=ticker,
            company_name=company_name,
            today=date.today().isoformat(),
        )

        def do_request():
            return req_lib.post(
                "https://api.perplexity.ai/chat/completions",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": "sonar",
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": 300,
                    "temperature": 0.1,
                },
                timeout=8,
            )

        resp = await asyncio.get_event_loop().run_in_executor(None, do_request)
        resp.raise_for_status()
        data = resp.json()

        raw = data["choices"][0]["message"]["content"]

        # Strip markdown fences
        text = raw.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1]
            if "```" in text:
                text = text[: text.rfind("```")]
            text = text.strip()

        # Find JSON object
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1 and end > start:
            result = json.loads(text[start : end + 1])
        else:
            result = json.loads(text)

        raw_date = result.get("report_date")
        if not raw_date or raw_date == "null":
            return None

        parsed = _parse_date(str(raw_date))
        if not parsed:
            return None

        # Reject past dates
        if parsed < date.today():
            return None

        # Reject dates more than 18 months out
        if parsed > date.today() + timedelta(days=548):
            return None

        return (
            parsed,
            result.get("report_type"),
            result.get("confidence", "medium"),
            raw,
        )

    except Exception as e:
        logger.debug(f"[calendar] perplexity {ticker}: {e}")
        return None


def merge_sources(
    yf_result: Optional[tuple],
    px_result: Optional[tuple],
    agree_threshold_days: int = 7,
) -> Optional[dict]:
    """Reconcile yfinance + Perplexity results into a single calendar entry.

    Inputs:
      yf_result: (date, report_type) or None
      px_result: (date, report_type, confidence, raw_text) or None

    Returns dict with keys (final_date, final_type, final_confidence, final_source,
    raw_text, bucket) or None if both inputs are None.

    `bucket` is one of: "both_agree", "yfinance", "perplexity" — used by callers
    for stats accounting.

    Rules:
      - Both present and within `agree_threshold_days` → high confidence,
        source="yfinance+perplexity", prefer Perplexity's exact date.
      - Both present but disagree → fall back to yfinance, medium confidence.
      - yfinance only → medium confidence.
      - Perplexity only → confidence capped at "medium" (never "high" without corroboration).
    """
    if yf_result is None and px_result is None:
        return None

    if yf_result and px_result:
        yf_date, yf_type = yf_result
        px_date, px_type, px_conf, px_raw = px_result
        delta = abs((yf_date - px_date).days)
        if delta <= agree_threshold_days:
            return {
                "final_date": px_date,
                "final_type": px_type or yf_type,
                "final_confidence": "high",
                "final_source": "yfinance+perplexity",
                "raw_text": px_raw,
                "bucket": "both_agree",
            }
        return {
            "final_date": yf_date,
            "final_type": yf_type,
            "final_confidence": "medium",
            "final_source": "yfinance",
            "raw_text": None,
            "bucket": "yfinance",
        }

    if yf_result:
        yf_date, yf_type = yf_result
        return {
            "final_date": yf_date,
            "final_type": yf_type,
            "final_confidence": "medium",
            "final_source": "yfinance",
            "raw_text": None,
            "bucket": "yfinance",
        }

    # Perplexity only
    px_date, px_type, px_conf, px_raw = px_result
    final_conf = "medium" if px_conf == "high" else px_conf
    return {
        "final_date": px_date,
        "final_type": px_type,
        "final_confidence": final_conf,
        "final_source": "perplexity",
        "raw_text": px_raw,
        "bucket": "perplexity",
    }


async def refresh_earnings_calendar(
    conn,
    tickers_override: Optional[list[str]] = None,
) -> dict:
    """Iterate tickers, try yfinance first, Perplexity for gaps. Never overwrite manual/confirmed entries."""
    from asx200 import ASX200_TICKERS

    tickers = sorted(tickers_override or ASX200_TICKERS)
    today = date.today()

    # Get tickers with confirmed entries so we skip them
    confirmed_rows = await conn.fetch(
        "SELECT DISTINCT ticker FROM asx_calendar WHERE confirmed = TRUE AND expected_reporting_date >= $1",
        today,
    )
    confirmed_tickers = {r["ticker"] for r in confirmed_rows}

    # Skip tickers that already have a recent future entry (resume support)
    recent_rows = await conn.fetch(
        """SELECT DISTINCT ticker FROM asx_calendar
           WHERE expected_reporting_date >= $1
             AND last_verified > NOW() - INTERVAL '24 hours'""",
        today,
    )
    recent_tickers = {r["ticker"] for r in recent_rows}

    # Get company names from asx_companies
    name_rows = await conn.fetch("SELECT ticker, company_name FROM asx_companies")
    known_names = {r["ticker"]: r["company_name"] for r in name_rows}

    stats = {
        "yfinance_hits": 0,
        "perplexity_hits": 0,
        "both_agree": 0,
        "no_data": 0,
        "skipped_confirmed": 0,
        "skipped_recent": 0,
        "total": len(tickers),
    }

    for ticker in tickers:
        if ticker in confirmed_tickers:
            stats["skipped_confirmed"] += 1
            logger.info(f"[calendar] {ticker}: confirmed entry — skipping")
            continue
        if ticker in recent_tickers:
            stats["skipped_recent"] += 1
            continue

        company_name = known_names.get(ticker, f"{ticker} Ltd")

        async def _do_ticker():
            yf_r = await _try_yfinance(ticker)
            px_r = None
            if yf_r is None or ticker in _LARGE_CAPS:
                px_r = await _try_perplexity(ticker, company_name)
                await asyncio.sleep(1.0)
            return yf_r, px_r

        try:
            yf_result, px_result = await asyncio.wait_for(_do_ticker(), timeout=15.0)
        except asyncio.TimeoutError:
            logger.warning(f"[calendar] {ticker}: timeout — skipping")
            stats["no_data"] += 1
            continue

        merged = merge_sources(yf_result, px_result)
        if merged is None:
            stats["no_data"] += 1
            logger.info(f"[calendar] {ticker}: no data from either source")
            continue

        final_date = merged["final_date"]
        final_type = merged["final_type"]
        final_confidence = merged["final_confidence"]
        final_source = merged["final_source"]
        raw_text = merged["raw_text"]
        stats[{"both_agree": "both_agree",
               "yfinance": "yfinance_hits",
               "perplexity": "perplexity_hits"}[merged["bucket"]]] += 1

        # Upsert — never overwrite confirmed entries. Retry once on connection drop.
        upsert_sql = """INSERT INTO asx_calendar
                (ticker, expected_reporting_date, result_type, source, confidence, raw_date_text, last_verified)
            VALUES ($1, $2, $3, $4, $5, $6, NOW())
            ON CONFLICT (ticker, expected_reporting_date)
            DO UPDATE SET
                result_type = COALESCE(EXCLUDED.result_type, asx_calendar.result_type),
                source = EXCLUDED.source,
                confidence = EXCLUDED.confidence,
                raw_date_text = EXCLUDED.raw_date_text,
                last_verified = NOW()
            WHERE asx_calendar.confirmed = FALSE"""
        try:
            await conn.execute(
                upsert_sql, ticker, final_date, final_type,
                final_source, final_confidence, raw_text,
            )
        except Exception as e:
            logger.warning(f"[calendar] {ticker}: upsert failed ({e}), reconnecting")
            try:
                import asyncpg as _ap
                await conn.close()
            except Exception:
                pass
            conn = await _ap.connect(os.environ["DATABASE_URL"])
            await conn.execute(
                upsert_sql, ticker, final_date, final_type,
                final_source, final_confidence, raw_text,
            )

        logger.info(f"[calendar] {ticker}: {final_date} ({final_source}, {final_confidence})")

    return stats


async def main():
    import asyncpg

    from dotenv import load_dotenv

    load_dotenv()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    conn = await asyncpg.connect(os.environ["DATABASE_URL"])

    import sys

    tickers = sys.argv[1:] if len(sys.argv) > 1 else None
    stats = await refresh_earnings_calendar(conn, tickers_override=tickers)
    await conn.close()

    print()
    print("=== Earnings Calendar Refresh ===")
    for k, v in stats.items():
        print(f"  {k}: {v}")


if __name__ == "__main__":
    asyncio.run(main())
