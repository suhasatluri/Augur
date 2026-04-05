"""ASIC daily short position data.

Free, public, no authentication required.
URL: https://download.asic.gov.au/short-selling/RR{YYYYMMDD}-001-SSDailyAggShortPos.csv
T+4 delay — tries last 14 calendar days to find the most recent file.
"""

from __future__ import annotations

import csv
import io
import logging
from datetime import datetime, timedelta
from typing import Optional

import requests

logger = logging.getLogger(__name__)

ASIC_BASE = "https://download.asic.gov.au/short-selling"


def get_latest_asic_url() -> tuple[str, str]:
    """Find the most recent ASIC short-selling CSV (tries last 14 days)."""
    today = datetime.now()
    for i in range(14):
        d = today - timedelta(days=i)
        if d.weekday() >= 5:
            continue
        ds = d.strftime("%Y%m%d")
        url = f"{ASIC_BASE}/RR{ds}-001-SSDailyAggShortPos.csv"
        try:
            r = requests.head(url, timeout=5)
            if r.status_code == 200:
                return url, ds
        except Exception:
            continue
    raise RuntimeError("Could not find recent ASIC CSV")


def download_asic_data() -> dict[str, dict]:
    """Download and parse the latest ASIC short-selling CSV. Returns {ticker: row_dict}."""
    url, ds = get_latest_asic_url()
    logger.info(f"ASIC CSV: {url} (date={ds})")
    r = requests.get(url, timeout=30)
    r.raise_for_status()

    reader = csv.DictReader(io.StringIO(r.text))
    data = {}
    for row in reader:
        code = row.get("Product Code", "").strip()
        if not code:
            continue
        try:
            pct = float(row.get("% of Total Product in Issue Reported as Short Positions", "0") or "0")
            short_pos = int(row.get("Reported Short Positions", 0) or 0)
            total = int(row.get("Total Product in Issue", 0) or 0)
        except (ValueError, TypeError):
            continue
        data[code] = {
            "ticker": code,
            "pct_shorted": round(pct, 6),
            "short_positions": short_pos,
            "total_in_issue": total,
            "as_of_date": ds,
        }
    logger.info(f"ASIC: parsed {len(data)} tickers")
    return data


def get_short_signal(pct: float) -> tuple[str, float]:
    """Returns (signal, score). Score is INVERSE — high shorting = low score.

    Thresholds calibrated for ASX (lower than US market).
    """
    if pct >= 8.0:
        return "HIGH", max(0.05, 0.5 - pct / 20)
    elif pct >= 4.0:
        return "ELEVATED", max(0.3, 0.55 - pct / 25)
    elif pct >= 1.5:
        return "NORMAL", 0.5
    else:
        return "LOW", min(0.65, 0.5 + (1.5 - pct) / 10)


def get_short_interest(ticker: str, data: Optional[dict] = None) -> Optional[dict]:
    """Get short interest data for a single ticker. Downloads CSV if data not provided."""
    if data is None:
        data = download_asic_data()
    row = data.get(ticker.upper())
    if not row:
        return None
    signal, score = get_short_signal(row["pct_shorted"])
    return {**row, "signal": signal, "signal_score": round(score, 3)}
