"""Market Index scraper — director transactions and 10-year financials.

Requires MARKETINDEX_COOKIE in .env for authenticated access.
Without cookie, requests may return 403.
"""

from __future__ import annotations

import logging
import os
import re
import time
from datetime import datetime
from typing import Optional

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

BASE = "https://www.marketindex.com.au"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-AU,en;q=0.9",
    "Referer": "https://www.marketindex.com.au/",
    "Connection": "keep-alive",
}


def get_session() -> requests.Session:
    """Create a session with optional cookie auth."""
    s = requests.Session()
    s.headers.update(HEADERS)
    cookie = os.getenv("MARKETINDEX_COOKIE")
    if cookie:
        s.cookies.set("mi_session", cookie, domain="www.marketindex.com.au")
        uid = os.getenv("MARKETINDEX_USER_ID", "142704")
        s.cookies.set("mi_auth_user", uid, domain="www.marketindex.com.au")
    else:
        logger.warning("MARKETINDEX_COOKIE not set — pages may be restricted")
    return s


def fetch_page(url: str, session: Optional[requests.Session] = None) -> BeautifulSoup:
    """Fetch and parse a page with rate limiting."""
    if session is None:
        session = get_session()
    time.sleep(1.5)
    r = session.get(url, timeout=25)
    r.raise_for_status()
    return BeautifulSoup(r.text, "lxml")


def _parse_num(s: str) -> float:
    """Parse a number string, stripping currency/commas."""
    try:
        return float(re.sub(r"[^0-9.\-]", "", s))
    except (ValueError, TypeError):
        return 0.0


def get_director_transactions(
    ticker: str, session: Optional[requests.Session] = None
) -> list[dict]:
    """Scrape 12 months of director transactions from /asx/{ticker}."""
    url = f"{BASE}/asx/{ticker.lower()}"
    try:
        soup = fetch_page(url, session)
    except Exception as e:
        logger.error(f"{ticker} director fetch failed: {e}")
        return []

    # Find director transactions table
    target = None
    for t in soup.find_all("table"):
        headers = [th.get_text(strip=True) for th in t.find_all("th")]
        text = t.get_text()
        if ("Director" in headers or "Director" in text) and ("Buy" in text or "Sell" in text):
            target = t
            break

    if not target:
        logger.warning(f"{ticker}: no director table found")
        return []

    results = []
    for row in target.find_all("tr")[1:]:
        cells = [td.get_text(strip=True) for td in row.find_all("td")]
        if len(cells) < 6:
            continue
        results.append({
            "date": cells[0],
            "director": cells[1],
            "type": cells[2],
            "amount": int(_parse_num(cells[3])),
            "price": _parse_num(cells[4]),
            "value": _parse_num(cells[5]),
            "notes": cells[6] if len(cells) > 6 else "",
            "ticker": ticker.upper(),
            "scraped_at": datetime.now().isoformat(),
        })

    logger.info(f"{ticker}: {len(results)} director transactions")
    return results


def compute_director_signal(transactions: list[dict]) -> dict:
    """Net buy value signal from on-market director trades (excludes option grants)."""
    buys = [t for t in transactions if t["type"] == "Buy"]
    sells = [t for t in transactions if t["type"] == "Sell"]
    buy_val = sum(t["value"] for t in buys)
    sell_val = sum(t["value"] for t in sells)
    net = buy_val - sell_val

    if net > 1_000_000:
        sig, score = "STRONG_BUY", 0.75
    elif net > 100_000:
        sig, score = "BUY", 0.65
    elif net > -100_000:
        sig, score = "NEUTRAL", 0.50
    elif net > -1_000_000:
        sig, score = "SELL", 0.35
    else:
        sig, score = "STRONG_SELL", 0.20

    return {
        "net_buy_value": round(net, 2),
        "buy_count": len(buys),
        "sell_count": len(sells),
        "signal": sig,
        "signal_score": score,
    }


def get_financials(ticker: str, session: Optional[requests.Session] = None) -> dict:
    """Scrape 10-year financials from /asx/{ticker}/financials."""
    url = f"{BASE}/asx/{ticker.lower()}/financials"
    try:
        soup = fetch_page(url, session)
    except Exception as e:
        logger.error(f"{ticker} financials fetch failed: {e}")
        return {}

    tables = soup.find_all("table")
    if not tables:
        return {}

    main = tables[0]
    rows = main.find_all("tr")
    if not rows:
        return {}

    years = [cell.get_text(strip=True) for cell in rows[0].find_all(["th", "td"])][1:]

    def parse_val(text: str):
        if not text or text == "-":
            return None
        neg = text.startswith("(")
        clean = re.sub(r"[^0-9.]", "", text)
        try:
            v = float(clean)
            return -v if neg else v
        except ValueError:
            return None

    metrics = {}
    for row in rows[1:]:
        cells = row.find_all("td")
        if not cells:
            continue
        name = cells[0].get_text(strip=True)
        vals = [parse_val(c.get_text(strip=True)) for c in cells[1:]]
        metrics[name] = vals

    return {
        "ticker": ticker.upper(),
        "years": years,
        "npat": metrics.get("NPAT ($M)", []),
        "npat_before_abnormals": metrics.get("NPAT before Abnormals ($M)", []),
        "revenue": metrics.get("Revenue ($M)", []),
        "eps_cents": metrics.get("EPS (¢)", []),
        "dps_cents": metrics.get("DPS (¢)", []),
        "scraped_at": datetime.now().isoformat(),
    }


def compute_beat_rate(financials: dict) -> Optional[float]:
    """Proxy beat rate from year-on-year NPAT growth. Positive growth = beat proxy."""
    npat = [v for v in financials.get("npat", []) if v is not None]
    if len(npat) < 3:
        return None
    # npat[0] is most recent year, npat[1] is prior year, etc.
    beats = sum(1 for i in range(len(npat) - 1) if npat[i] > npat[i + 1])
    total = len(npat) - 1
    return round(beats / total, 3) if total else None
