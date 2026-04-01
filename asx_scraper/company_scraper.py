"""Company master data scraper — ASX API with yfinance fallback."""

from __future__ import annotations

import asyncio
import json
import logging
import ssl
import urllib.request
from typing import Optional

import yfinance as yf

from db.schema import get_pool

logger = logging.getLogger(__name__)

# ASX undocumented API — may return 404/403
ASX_COMPANY_URL = "https://www.asx.com.au/asx/1/company/{ticker}/details"

# Mapping from yfinance fiscal year end month
_MONTH_TO_FYE = {
    1: "JAN", 2: "FEB", 3: "MAR", 4: "APR", 5: "MAY", 6: "JUN",
    7: "JUL", 8: "AUG", 9: "SEP", 10: "OCT", 11: "NOV", 12: "DEC",
}


class CompanyScraper:
    """Scrapes ASX company master data. Runs once per week per ticker."""

    async def _fetch_asx_api(self, ticker: str) -> Optional[dict]:
        """Try the ASX undocumented API. Returns None on failure."""
        url = ASX_COMPANY_URL.format(ticker=ticker.upper())
        try:
            ctx = ssl.create_default_context()
            req = urllib.request.Request(url, headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
                "Accept": "application/json",
            })

            def do_fetch():
                with urllib.request.urlopen(req, context=ctx, timeout=15) as resp:
                    return json.loads(resp.read().decode("utf-8"))

            data = await asyncio.get_event_loop().run_in_executor(None, do_fetch)
            logger.info(f"[company] ASX API OK for {ticker}")
            return data
        except Exception as e:
            logger.debug(f"[company] ASX API failed for {ticker}: {e}")
            return None

    async def _fetch_yfinance(self, ticker: str) -> Optional[dict]:
        """Fallback: use yfinance for company data."""
        try:
            def do_fetch():
                stock = yf.Ticker(f"{ticker}.AX")
                return stock.info or {}

            info = await asyncio.get_event_loop().run_in_executor(None, do_fetch)
            if not info.get("longName"):
                return None

            fye_month = info.get("lastFiscalYearEnd")
            fiscal_year_end = None
            if fye_month:
                from datetime import datetime
                try:
                    dt = datetime.fromtimestamp(fye_month)
                    fiscal_year_end = _MONTH_TO_FYE.get(dt.month)
                except (ValueError, OSError):
                    pass

            return {
                "company_name": info.get("longName"),
                "sector": info.get("sector"),
                "industry": info.get("industry"),
                "market_cap_aud": info.get("marketCap"),
                "shares_on_issue": info.get("sharesOutstanding"),
                "fiscal_year_end": fiscal_year_end,
                "ir_page_url": info.get("website"),
            }
        except Exception as e:
            logger.warning(f"[company] yfinance failed for {ticker}: {e}")
            return None

    async def scrape(self, ticker: str) -> dict:
        """Returns structured company data. Upserts into asx_companies table."""
        ticker = ticker.upper()

        # Try ASX API first, fall back to yfinance
        asx_data = await self._fetch_asx_api(ticker)
        if asx_data:
            record = {
                "ticker": ticker,
                "company_name": asx_data.get("name_full") or asx_data.get("name_short"),
                "sector": asx_data.get("sector"),
                "industry": asx_data.get("industry_group_name"),
                "market_cap_aud": asx_data.get("market_cap"),
                "shares_on_issue": asx_data.get("number_of_shares"),
                "fiscal_year_end": None,
                "ir_page_url": asx_data.get("web_address"),
                "source": "asx_api",
            }
        else:
            yf_data = await self._fetch_yfinance(ticker)
            if not yf_data:
                logger.error(f"[company] No data for {ticker} from any source")
                return {"ticker": ticker, "error": "no data from any source"}
            record = {**yf_data, "ticker": ticker, "source": "yfinance"}

        # Upsert to DB
        try:
            pool = await get_pool()
            async with pool.acquire() as conn:
                await conn.execute("""
                    INSERT INTO asx_companies
                        (ticker, company_name, sector, industry, market_cap_aud,
                         shares_on_issue, fiscal_year_end, ir_page_url, last_updated)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, NOW())
                    ON CONFLICT (ticker) DO UPDATE SET
                        company_name = EXCLUDED.company_name,
                        sector = COALESCE(EXCLUDED.sector, asx_companies.sector),
                        industry = COALESCE(EXCLUDED.industry, asx_companies.industry),
                        market_cap_aud = COALESCE(EXCLUDED.market_cap_aud, asx_companies.market_cap_aud),
                        shares_on_issue = COALESCE(EXCLUDED.shares_on_issue, asx_companies.shares_on_issue),
                        fiscal_year_end = COALESCE(EXCLUDED.fiscal_year_end, asx_companies.fiscal_year_end),
                        ir_page_url = COALESCE(EXCLUDED.ir_page_url, asx_companies.ir_page_url),
                        last_updated = NOW()
                """,
                    record["ticker"],
                    record.get("company_name"),
                    record.get("sector"),
                    record.get("industry"),
                    record.get("market_cap_aud"),
                    record.get("shares_on_issue"),
                    record.get("fiscal_year_end"),
                    record.get("ir_page_url"),
                )
            logger.info(f"[company] Upserted {ticker} (source={record.get('source')})")
        except Exception as e:
            logger.error(f"[company] DB upsert failed for {ticker}: {e}")
            record["db_error"] = str(e)

        return record

    async def scrape_all(self, tickers: list[str]) -> dict:
        """Scrapes all tickers with 2s delay. Returns summary."""
        results = {"success": [], "failed": []}
        for ticker in tickers:
            try:
                record = await self.scrape(ticker)
                if "error" in record:
                    results["failed"].append(ticker)
                else:
                    results["success"].append(ticker)
            except Exception as e:
                logger.error(f"[company] Unexpected error for {ticker}: {e}")
                results["failed"].append(ticker)
            await asyncio.sleep(2)

        logger.info(
            f"[company] Scrape complete: {len(results['success'])} OK, "
            f"{len(results['failed'])} failed"
        )
        return results
