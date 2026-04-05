"""Market Index scraper using Playwright — bypasses Cloudflare bot protection.

Uses stealth mode to appear as a real Chrome browser.
Requires: pip install playwright playwright-stealth && playwright install chromium

Data sources:
  /asx/{ticker}/financials  → 10-year financials (NPAT, revenue, EPS, DPS)
  /asx/{ticker}             → director transactions table

Auth: Logs in once using MARKETINDEX_EMAIL + MARKETINDEX_PASSWORD from .env.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)

BASE = "https://www.marketindex.com.au"


async def _get_browser():
    """Create Playwright browser with stealth mode."""
    from playwright.async_api import async_playwright

    pw = await async_playwright().start()
    browser = await pw.chromium.launch(
        headless=True,
        args=[
            "--no-sandbox",
            "--disable-blink-features=AutomationControlled",
            "--disable-dev-shm-usage",
        ],
    )
    context = await browser.new_context(
        user_agent=(
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
        ),
        viewport={"width": 1280, "height": 800},
    )
    # Apply stealth patches
    try:
        from playwright_stealth import Stealth
        stealth = Stealth()
        await stealth.apply_stealth_async(context)
    except Exception:
        pass  # Proceed without stealth if API changed
    page = await context.new_page()
    return pw, browser, page


async def _login(page) -> bool:
    """Login to Market Index using credentials from .env."""
    email = os.getenv("MARKETINDEX_EMAIL")
    password = os.getenv("MARKETINDEX_PASSWORD")
    if not email or not password:
        logger.warning("MARKETINDEX_EMAIL/PASSWORD not set — scraping without auth")
        return False

    try:
        await page.goto(f"{BASE}/login", wait_until="networkidle")
        await page.fill('input[type="email"], input[name="email"]', email)
        await page.fill('input[type="password"], input[name="password"]', password)
        await page.click('button[type="submit"], input[type="submit"]')
        await page.wait_for_load_state("networkidle")
        logger.info("Market Index login attempted")
        return True
    except Exception as e:
        logger.error(f"Market Index login failed: {e}")
        return False


def _parse_val(text: str) -> Optional[float]:
    """Parse a financial value string."""
    if not text or text.strip() == "-":
        return None
    neg = "(" in text
    clean = re.sub(r"[^0-9.]", "", text)
    try:
        v = float(clean)
        return -v if neg else v
    except ValueError:
        return None


async def get_financials(ticker: str) -> dict:
    """Scrape 10-year financial history via Playwright. Returns NPAT, revenue, EPS, beat rate."""
    pw = browser = page = None
    try:
        pw, browser, page = await _get_browser()
        await _login(page)

        url = f"{BASE}/asx/{ticker.lower()}/financials"
        await page.goto(url, wait_until="networkidle")
        await asyncio.sleep(1.5)

        html = await page.content()

        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "lxml")
        tables = soup.find_all("table")
        if not tables:
            logger.warning(f"{ticker}: no financials tables found")
            return {}

        main = tables[0]
        rows = main.find_all("tr")
        if not rows:
            return {}

        years = [cell.get_text(strip=True) for cell in rows[0].find_all(["th", "td"])][1:]

        metrics = {}
        for row in rows[1:]:
            cells = row.find_all("td")
            if not cells:
                continue
            name = cells[0].get_text(strip=True)
            vals = [_parse_val(c.get_text(strip=True)) for c in cells[1:]]
            metrics[name] = vals

        npat = metrics.get("NPAT ($M)", [])
        npat_clean = [v for v in npat if v is not None]
        beat_rate = None
        if len(npat_clean) >= 3:
            beats = sum(1 for i in range(len(npat_clean) - 1) if npat_clean[i] > npat_clean[i + 1])
            beat_rate = round(beats / (len(npat_clean) - 1), 3)

        result = {
            "ticker": ticker.upper(),
            "years": years,
            "npat": npat,
            "revenue": metrics.get("Revenue ($M)", []),
            "eps_cents": metrics.get("EPS (¢)", []),
            "dps_cents": metrics.get("DPS (¢)", []),
            "beat_rate": beat_rate,
            "npat_m": npat_clean[0] if npat_clean else None,
            "npat_prior_m": npat_clean[1] if len(npat_clean) > 1 else None,
            "revenue_m": metrics.get("Revenue ($M)", [None])[0],
        }
        logger.info(f"{ticker}: financials scraped — {len(years)} years, beat_rate={beat_rate}")
        return result

    except Exception as e:
        logger.error(f"{ticker} Playwright financials failed: {e}")
        return {}
    finally:
        if browser:
            await browser.close()
        if pw:
            await pw.stop()


async def get_director_transactions(ticker: str) -> list[dict]:
    """Scrape director transactions via Playwright."""
    pw = browser = page = None
    try:
        pw, browser, page = await _get_browser()
        await _login(page)

        url = f"{BASE}/asx/{ticker.lower()}"
        await page.goto(url, wait_until="networkidle")
        await asyncio.sleep(1.5)

        html = await page.content()

        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "lxml")

        target = None
        for t in soup.find_all("table"):
            headers = [th.get_text(strip=True) for th in t.find_all("th")]
            text = t.get_text()
            if ("Director" in headers or "Director" in text) and ("Buy" in text or "Sell" in text):
                target = t
                break

        if not target:
            logger.warning(f"{ticker}: no director table found via Playwright")
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
                "amount": int(float(re.sub(r"[^0-9.]", "", cells[3]) or "0")),
                "price": float(re.sub(r"[^0-9.]", "", cells[4]) or "0"),
                "value": float(re.sub(r"[^0-9.]", "", cells[5]) or "0"),
                "notes": cells[6] if len(cells) > 6 else "",
                "ticker": ticker.upper(),
                "scraped_at": datetime.now().isoformat(),
            })

        logger.info(f"{ticker}: {len(results)} director transactions via Playwright")
        return results

    except Exception as e:
        logger.error(f"{ticker} Playwright director scrape failed: {e}")
        return []
    finally:
        if browser:
            await browser.close()
        if pw:
            await pw.stop()
