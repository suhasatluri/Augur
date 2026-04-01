"""Scraper orchestrator — runs the full pipeline for one or many tickers."""

from __future__ import annotations

import asyncio
import logging
import time

from asx_scraper.asx_api import ASXMarketData
from asx_scraper.company_scraper import CompanyScraper
from asx_scraper.announcements_scraper import AnnouncementsScraper
from asx_scraper.ir_harvester import IRHarvester
from asx_scraper.pdf_extractor import PDFExtractor
from asx_scraper.price_scraper import PriceScraper
from asx_scraper.metrics_computer import MetricsComputer

logger = logging.getLogger(__name__)

# ASX 100 tickers (top by market cap)
ASX100_TICKERS = [
    "BHP", "CBA", "CSL", "NAB", "WBC", "ANZ", "WES", "MQG", "FMG", "WDS",
    "TLS", "WOW", "RIO", "ALL", "GMG", "TCL", "COL", "STO", "QBE", "REA",
    "NCM", "AMC", "SHL", "JHX", "SOL", "ORG", "IAG", "MIN", "S32", "SUN",
    "BXB", "APA", "RMD", "CPU", "TWE", "ORI", "AZJ", "BSL", "SVW", "GPT",
    "NST", "MGR", "DXS", "CHC", "SGP", "VCX", "SCG", "ABP", "CWN", "EVN",
    "ILU", "WHC", "ALD", "LYC", "PLS", "IGO", "SFR", "DEG", "GOR", "RED",
    "BPT", "AWC", "NHC", "YAL", "HVN", "TAH", "SGM", "IEL", "ASX", "MPL",
    "NXT", "ALX", "CEN", "BOQ", "BEN", "HUB", "NWS", "SEK", "CAR", "DHG",
    "WTC", "XRO", "TNE", "ALU", "PME", "APX", "TYR", "LNK", "PPT", "CGF",
    "QAN", "FLT", "WEB", "JBH", "PMV", "SUL", "COH", "RHC", "A2M", "GQG",
]


class ScraperOrchestrator:
    """Runs the full scrape pipeline for one or many tickers."""

    def __init__(self) -> None:
        self.asx_api = ASXMarketData()
        self.company = CompanyScraper()
        self.announcements = AnnouncementsScraper()
        self.ir_harvester = IRHarvester()
        self.pdf_extractor = PDFExtractor()
        self.prices = PriceScraper()
        self.metrics = MetricsComputer()

    async def scrape_ticker(self, ticker: str) -> dict:
        """Full pipeline for one ticker."""
        ticker = ticker.upper()
        start = time.monotonic()
        errors: list[str] = []
        summary = {
            "ticker": ticker,
            "announcements_found": 0,
            "quarters_extracted": 0,
            "price_reactions_updated": 0,
            "beat_rate": None,
            "data_confidence": None,
            "errors": errors,
        }

        # 1a. ASX Markit API — official source for company + income statements
        try:
            asx_data = await self.asx_api.scrape_and_store(ticker)
            if "error" not in asx_data:
                summary["company_name"] = asx_data.get("company_name")
                summary["asx_api_earnings"] = asx_data.get("earnings_stored", 0)
            else:
                errors.append(f"asx_api: {asx_data['error']}")
        except Exception as e:
            errors.append(f"asx_api: {e}")

        # 1b. Company data (yfinance fallback for fields ASX API doesn't have)
        try:
            company = await self.company.scrape(ticker)
            if "error" not in company and not summary.get("company_name"):
                summary["company_name"] = company.get("company_name")
        except Exception as e:
            errors.append(f"company: {e}")

        # 2. Earnings extraction (IR harvester → web_search fallback)
        try:
            extracted = await self.ir_harvester.harvest_all(ticker)
            summary["announcements_found"] = len(extracted)

            # Upsert each extracted record to DB
            saved = 0
            for rec in extracted:
                ok = await self.announcements._upsert_earnings(
                    ticker, rec, {"pdf_url": rec.get("_pdf_url", ""), "title": rec.get("period", "")}
                )
                if ok:
                    saved += 1
            summary["quarters_extracted"] = saved
            summary["extracted_records"] = [
                {
                    "ticker": ticker,
                    "period": r.get("period"),
                    "reporting_date": r.get("reporting_date"),
                    "revenue_aud_m": r.get("revenue_aud_m"),
                    "npat_aud_m": r.get("npat_aud_m"),
                    "eps_basic_cents": r.get("eps_basic_cents"),
                    "dividend_cents": r.get("dividend_final_cents") or r.get("dividend_interim_cents"),
                    "data_confidence": r.get("data_confidence"),
                    "saved_to_db": True,
                }
                for r in extracted
            ]
        except Exception as e:
            errors.append(f"earnings: {e}")

        # 3. Finnhub disabled — US-listed consensus diverges from ASX analyst expectations.
        #    Kept in codebase for potential future US market coverage.
        #    See: asx_scraper/finnhub_client.py
        summary["finnhub_matched"] = 0

        # 4. Price reactions + beat_miss proxy (ASX market response = ground truth)
        try:
            updated = await self.prices.update_earnings_reactions(ticker)
            summary["price_reactions_updated"] = updated

            # Fill beat_miss from price proxy where no consensus data exists
            if updated > 0:
                from db.schema import get_pool
                pool = await get_pool()
                async with pool.acquire() as conn:
                    await conn.execute("""
                        UPDATE asx_earnings
                        SET beat_miss = price_implied_result
                        WHERE ticker = $1
                          AND beat_miss IS NULL
                          AND price_implied_result IS NOT NULL
                    """, ticker)
        except Exception as e:
            errors.append(f"prices: {e}")

        # 5. Metrics
        try:
            metrics = await self.metrics.compute(ticker)
            summary["beat_rate"] = metrics.get("beat_rate_8q")
            summary["data_confidence"] = metrics.get("data_confidence")
            summary["metrics"] = metrics
        except Exception as e:
            errors.append(f"metrics: {e}")

        elapsed = time.monotonic() - start
        summary["duration_s"] = round(elapsed, 1)
        logger.info(
            f"[orchestrator] {ticker} done in {elapsed:.1f}s: "
            f"{summary['quarters_extracted']} quarters, "
            f"beat_rate={summary['beat_rate']}, confidence={summary['data_confidence']}"
        )
        return summary

    async def scrape_batch(self, tickers: list[str], delay: float = 5.0) -> dict:
        """Scrapes a specific list of tickers with delay between each."""
        start = time.monotonic()
        results = []

        for i, ticker in enumerate(tickers):
            print(f"  [{i+1}/{len(tickers)}] Scraping {ticker}...")
            summary = await self.scrape_ticker(ticker)
            results.append(summary)
            if i < len(tickers) - 1:
                await asyncio.sleep(delay)

        elapsed = time.monotonic() - start
        success = [r for r in results if not r["errors"]]
        failed = [r for r in results if r["errors"]]

        report = {
            "total": len(tickers),
            "success": len(success),
            "failed": len(failed),
            "duration_s": round(elapsed, 1),
            "results": results,
        }
        return report

    async def scrape_asx100(self) -> dict:
        """Scrapes all ASX 100 tickers."""
        print(f"  Scraping {len(ASX100_TICKERS)} ASX 100 tickers...")
        return await self.scrape_batch(ASX100_TICKERS, delay=5.0)

    async def show_ticker(self, ticker: str) -> dict:
        """Show all stored data for a ticker from the DB."""
        ticker = ticker.upper()
        from db.schema import get_pool

        pool = await get_pool()
        async with pool.acquire() as conn:
            company = await conn.fetchrow(
                "SELECT * FROM asx_companies WHERE ticker = $1", ticker
            )
            earnings = await conn.fetch(
                "SELECT * FROM asx_earnings WHERE ticker = $1 ORDER BY reporting_date DESC", ticker
            )
            metrics = await conn.fetchrow(
                "SELECT * FROM asx_metrics WHERE ticker = $1", ticker
            )
            commentary = await conn.fetch(
                "SELECT * FROM asx_commentary WHERE ticker = $1 ORDER BY reporting_date DESC LIMIT 10",
                ticker,
            )

        return {
            "company": dict(company) if company else None,
            "earnings": [dict(r) for r in earnings],
            "metrics": dict(metrics) if metrics else None,
            "commentary": [dict(r) for r in commentary],
        }
