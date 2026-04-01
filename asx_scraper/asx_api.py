"""ASX Markit Digital API client — official ASX data source.

Endpoints discovered at asx.api.markitdigital.com:
- /companies/{ticker}/header — name, sector, market cap, current price
- /companies/{ticker}/key-statistics — EPS, PE, dividends, 4yr income statements
- /companies/{ticker}/announcements — recent ASX announcements with document keys

No API key required. Free. Official ASX data.
"""

from __future__ import annotations

import asyncio
import json
import logging
import ssl
import urllib.request
from datetime import date, datetime, timedelta
from typing import Optional

from db.schema import get_pool

logger = logging.getLogger(__name__)

_BASE = "https://asx.api.markitdigital.com/asx-research/1.0/companies"


async def _fetch(url: str) -> dict:
    """GET request to ASX Markit API."""
    ctx = ssl.create_default_context()
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "application/json",
    })

    def do_fetch():
        with urllib.request.urlopen(req, context=ctx, timeout=15) as resp:
            return json.loads(resp.read().decode("utf-8"))

    return await asyncio.get_event_loop().run_in_executor(None, do_fetch)


def _excel_date_to_date(serial: int) -> Optional[date]:
    """Convert Excel serial date to Python date. Excel epoch = 1899-12-30."""
    try:
        return date(1899, 12, 30) + timedelta(days=serial)
    except (ValueError, OverflowError):
        return None


class ASXMarketData:
    """Fetches company data from the official ASX Markit Digital API."""

    async def get_header(self, ticker: str) -> dict:
        """Company header: name, sector, industry, market cap, current price."""
        ticker = ticker.upper()
        try:
            resp = await _fetch(f"{_BASE}/{ticker}/header")
            data = resp.get("data", {})
            return {
                "ticker": ticker,
                "company_name": data.get("displayName"),
                "sector": data.get("sector"),
                "industry": data.get("industryGroup"),
                "market_cap_aud": data.get("marketCap"),
                "price_last": data.get("priceLast"),
                "price_change_pct": data.get("priceChangePercent"),
                "volume": data.get("volume"),
            }
        except Exception as e:
            logger.warning(f"[asx_api] Header failed for {ticker}: {e}")
            return {"ticker": ticker, "error": str(e)}

    async def get_key_statistics(self, ticker: str) -> dict:
        """Key stats: EPS, PE, dividends, income statements, shares on issue."""
        ticker = ticker.upper()
        try:
            resp = await _fetch(f"{_BASE}/{ticker}/key-statistics")
            data = resp.get("data", {})

            # Parse income statements
            income_statements = []
            for inc in data.get("incomeStatement", []):
                period_end = _excel_date_to_date(inc.get("fPeriodEndDate", 0))
                income_statements.append({
                    "period": inc.get("period"),
                    "period_end_date": period_end.isoformat() if period_end else None,
                    "revenue": inc.get("revenue"),
                    "net_income": inc.get("netIncome"),
                    "currency": inc.get("curCode"),
                })

            return {
                "ticker": ticker,
                "eps": data.get("earningsPerShare"),
                "pe_ratio": data.get("priceEarningsRatio"),
                "dividend": data.get("dividend"),
                "dividend_currency": data.get("dividendCurrency"),
                "franking_pct": data.get("frankingPercent"),
                "shares_on_issue": data.get("numOfShares"),
                "yield_annual_pct": data.get("yieldAnnual"),
                "price_52w_high": data.get("priceFiftyTwoWeekHigh"),
                "price_52w_low": data.get("priceFiftyTwoWeekLow"),
                "cash_flow": data.get("cashFlow"),
                "free_cash_flow_yield": data.get("freeCashFlowYield"),
                "income_statements": income_statements,
            }
        except Exception as e:
            logger.warning(f"[asx_api] Key statistics failed for {ticker}: {e}")
            return {"ticker": ticker, "error": str(e)}

    async def get_announcements(self, ticker: str, count: int = 50) -> list[dict]:
        """Recent ASX announcements with document keys."""
        ticker = ticker.upper()
        try:
            resp = await _fetch(f"{_BASE}/{ticker}/announcements?count={count}")
            items = resp.get("data", {}).get("items", [])
            return [
                {
                    "ticker": ticker,
                    "headline": item.get("headline"),
                    "date": item.get("date", "")[:10],
                    "type": item.get("announcementType"),
                    "document_key": item.get("documentKey"),
                    "is_price_sensitive": item.get("isPriceSensitive", False),
                    "file_size": item.get("fileSize"),
                }
                for item in items
            ]
        except Exception as e:
            logger.warning(f"[asx_api] Announcements failed for {ticker}: {e}")
            return []

    async def scrape_and_store(self, ticker: str) -> dict:
        """Fetch all ASX data for a ticker and store in Neon.

        Upserts:
        - asx_companies: from header + key-statistics
        - asx_earnings: from income statements (4 years of annual data)
        """
        ticker = ticker.upper()
        header = await self.get_header(ticker)
        stats = await self.get_key_statistics(ticker)

        if "error" in header and "error" in stats:
            return {"ticker": ticker, "error": "both endpoints failed"}

        try:
            pool = await get_pool()
            async with pool.acquire() as conn:
                # Upsert company
                await conn.execute("""
                    INSERT INTO asx_companies
                        (ticker, company_name, sector, industry, market_cap_aud,
                         shares_on_issue, last_updated)
                    VALUES ($1, $2, $3, $4, $5, $6, NOW())
                    ON CONFLICT (ticker) DO UPDATE SET
                        company_name = COALESCE(EXCLUDED.company_name, asx_companies.company_name),
                        sector = COALESCE(EXCLUDED.sector, asx_companies.sector),
                        industry = COALESCE(EXCLUDED.industry, asx_companies.industry),
                        market_cap_aud = COALESCE(EXCLUDED.market_cap_aud, asx_companies.market_cap_aud),
                        shares_on_issue = COALESCE(EXCLUDED.shares_on_issue, asx_companies.shares_on_issue),
                        last_updated = NOW()
                """,
                    ticker,
                    header.get("company_name"),
                    header.get("sector"),
                    header.get("industry"),
                    header.get("market_cap_aud"),
                    stats.get("shares_on_issue"),
                )

                # Upsert income statements as earnings records
                earnings_stored = 0
                for inc in stats.get("income_statements", []):
                    period_end_str = inc.get("period_end_date")
                    if not period_end_str:
                        continue

                    period_end = date.fromisoformat(period_end_str)
                    # Use period_end + ~45 days as approximate reporting_date
                    # (companies report ~6-8 weeks after period end)
                    approx_reporting = period_end + timedelta(days=45)

                    revenue_m = inc["revenue"] / 1e6 if inc.get("revenue") else None
                    npat_m = inc["net_income"] / 1e6 if inc.get("net_income") else None

                    await conn.execute("""
                        INSERT INTO asx_earnings
                            (ticker, period, reporting_date, period_end_date, result_type,
                             revenue_aud_m, npat_aud_m, data_source, data_confidence)
                        VALUES ($1, $2, $3, $4, 'FULL_YEAR', $5, $6, 'asx_api', 'MED')
                        ON CONFLICT (ticker, reporting_date) DO UPDATE SET
                            period = COALESCE(EXCLUDED.period, asx_earnings.period),
                            period_end_date = COALESCE(EXCLUDED.period_end_date, asx_earnings.period_end_date),
                            revenue_aud_m = COALESCE(EXCLUDED.revenue_aud_m, asx_earnings.revenue_aud_m),
                            npat_aud_m = COALESCE(EXCLUDED.npat_aud_m, asx_earnings.npat_aud_m)
                    """,
                        ticker,
                        inc.get("period"),
                        approx_reporting,
                        period_end,
                        revenue_m,
                        npat_m,
                    )
                    earnings_stored += 1

            result = {
                "ticker": ticker,
                "company_name": header.get("company_name"),
                "sector": header.get("sector"),
                "eps": stats.get("eps"),
                "pe_ratio": stats.get("pe_ratio"),
                "income_statements": len(stats.get("income_statements", [])),
                "earnings_stored": earnings_stored,
                "source": "asx_api",
            }
            logger.info(
                f"[asx_api] {ticker}: {result['company_name']}, "
                f"EPS={stats.get('eps')}, PE={stats.get('pe_ratio')}, "
                f"{earnings_stored} earnings stored"
            )
            return result

        except Exception as e:
            logger.error(f"[asx_api] Store failed for {ticker}: {e}")
            return {"ticker": ticker, "error": str(e)}
