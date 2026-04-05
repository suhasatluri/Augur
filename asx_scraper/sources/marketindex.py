"""Market Index scraper using curl_cffi.

Impersonates Chrome TLS fingerprint to bypass Cloudflare bot detection.
No cookies, no auth, no Playwright needed.

Data extracted:
  /asx/{ticker}/financials → 10-year financial history
  /asx/{ticker}            → director transactions
"""

from __future__ import annotations

import logging
import re
import time
from typing import Optional

from bs4 import BeautifulSoup
from curl_cffi import requests as cffi

logger = logging.getLogger(__name__)

BASE = "https://www.marketindex.com.au"
IMPERSONATE = "chrome131"


def _get(url: str, retries: int = 2) -> Optional[str]:
    """Fetch URL using Chrome TLS impersonation. Returns HTML or None."""
    for attempt in range(retries):
        try:
            time.sleep(1.5)
            r = cffi.get(
                url,
                impersonate=IMPERSONATE,
                timeout=25,
                headers={
                    "Accept": "text/html,application/xhtml+xml,*/*",
                    "Accept-Language": "en-AU,en;q=0.9",
                    "Referer": BASE,
                },
            )
            if r.status_code == 200:
                return r.text
            logger.warning(f"GET {url} returned {r.status_code}")
        except Exception as e:
            logger.warning(f"GET {url} attempt {attempt + 1} failed: {e}")
    return None


def _parse_val(text: str) -> Optional[float]:
    """Parse a financial value string. Handles negatives in parens."""
    if not text or text.strip() in ("-", ""):
        return None
    neg = "(" in text
    clean = re.sub(r"[^0-9.]", "", text)
    try:
        v = float(clean)
        return -v if neg else v
    except ValueError:
        return None


def get_financials(ticker: str) -> dict:
    """Scrape 10-year financial history from /asx/{ticker}/financials."""
    url = f"{BASE}/asx/{ticker.lower()}/financials"
    html = _get(url)
    if not html:
        logger.error(f"{ticker}: Market Index financials fetch failed")
        return {}

    soup = BeautifulSoup(html, "lxml")
    tables = soup.find_all("table")
    if not tables:
        logger.warning(f"{ticker}: no tables on financials page")
        return {}

    main = tables[0]
    rows = main.find_all("tr")
    if not rows:
        return {}

    years = [cell.get_text(strip=True) for cell in rows[0].find_all(["th", "td"])][1:]

    metrics: dict[str, list] = {}
    for row in rows[1:]:
        cells = row.find_all("td")
        if not cells:
            continue
        name = cells[0].get_text(strip=True)
        vals = [_parse_val(c.get_text(strip=True)) for c in cells[1:]]
        metrics[name] = vals

    npat = metrics.get("NPAT ($M)", [])
    npat_clean = [v for v in npat if v is not None]
    revenue = metrics.get("Revenue ($M)", [])

    beat_rate = None
    if len(npat_clean) >= 3:
        beats = sum(1 for i in range(len(npat_clean) - 1) if npat_clean[i] > npat_clean[i + 1])
        beat_rate = round(beats / (len(npat_clean) - 1), 3)

    result = {
        "ticker": ticker.upper(),
        "years": years,
        "npat": npat,
        "revenue": revenue,
        "eps_cents": metrics.get("EPS (¢)", []),
        "dps_cents": metrics.get("DPS (¢)", []),
        "beat_rate": beat_rate,
        "npat_m": npat_clean[0] if npat_clean else None,
        "npat_prior_m": npat_clean[1] if len(npat_clean) > 1 else None,
        "revenue_m": next((v for v in revenue if v is not None), None),
    }
    logger.info(f"{ticker}: MI financials OK — {len(years)} years, beat_rate={beat_rate}")
    return result


def get_director_transactions(ticker: str) -> dict:
    """Scrape 12-month director transactions from /asx/{ticker}."""
    url = f"{BASE}/asx/{ticker.lower()}"
    html = _get(url)

    default = {"signal": "NEUTRAL", "signal_score": 0.5, "net_buy_value": 0, "buy_count": 0, "sell_count": 0, "transactions": []}
    if not html:
        logger.error(f"{ticker}: MI director fetch failed")
        return default

    soup = BeautifulSoup(html, "lxml")
    target = None
    for t in soup.find_all("table"):
        text = t.get_text()
        headers = [th.get_text(strip=True) for th in t.find_all("th")]
        if ("Director" in headers or "Director" in text) and ("Buy" in text or "Sell" in text):
            target = t
            break

    if not target:
        logger.info(f"{ticker}: no director table — no insider activity")
        return default

    transactions = []
    for row in target.find_all("tr")[1:]:
        cells = [td.get_text(strip=True) for td in row.find_all("td")]
        if len(cells) < 6:
            continue

        def _num(s):
            try:
                return float(re.sub(r"[^0-9.]", "", s))
            except (ValueError, TypeError):
                return 0.0

        transactions.append({
            "date": cells[0],
            "director": cells[1],
            "type": cells[2],
            "amount": int(_num(cells[3])),
            "price": _num(cells[4]),
            "value": _num(cells[5]),
            "notes": cells[6] if len(cells) > 6 else "",
            "ticker": ticker.upper(),
        })

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

    logger.info(f"{ticker}: MI director signal={sig} buys={len(buys)} sells={len(sells)} net=${net:,.0f}")
    return {
        "signal": sig,
        "signal_score": round(score, 3),
        "net_buy_value": round(net, 2),
        "buy_count": len(buys),
        "sell_count": len(sells),
        "transactions": transactions,
    }


def scrape_ticker(ticker: str) -> dict:
    """Scrape all Market Index data for one ticker."""
    logger.info(f"[MarketIndex] Scraping {ticker}")
    return {
        "ticker": ticker.upper(),
        "financials": get_financials(ticker),
        "director_signal": get_director_transactions(ticker),
    }
