"""IR Harvester — finds earnings PDFs from company investor relations pages."""

from __future__ import annotations

import asyncio
import json
import logging
import ssl
import urllib.request
from typing import Optional

import anthropic

from asx_scraper.pdf_extractor import PDFExtractor

logger = logging.getLogger(__name__)

# Known IR page URLs for ASX 100 companies
# These are stable — company websites rarely change their IR URL structure
IR_PAGES: dict[str, str] = {
    "CBA": "https://www.commbank.com.au/about-us/investors/results.html",
    "BHP": "https://www.bhp.com/investors/financial-results-operational-reviews",
    "CSL": "https://investors.csl.com/investors/financial-results-and-information",
    "WBC": "https://www.westpac.com.au/about-westpac/investor-centre/events-and-presentations/presentations-agm/",
    "ANZ": "https://www.anz.com/shareholder/centre/reporting/results-announcement/",
    "NAB": "https://www.nab.com.au/about-us/shareholder-centre/financial-disclosures-and-reporting/financial-results",
    "WES": "https://www.wesfarmers.com.au/investor-centre/company-performance-news/results-presentations",
    "WOW": "https://www.woolworthsgroup.com.au/au/en/investors/our-performance/reports-and-presentations.html",
    "RIO": "https://www.riotinto.com/en/invest/financial-news-performance/results",
    "FMG": "https://investors.fortescue.com/en/results-and-operational-performance",
    "MQG": "https://www.macquarie.com/au/en/investors/reports.html",
    "TLS": "https://www.telstra.com.au/aboutus/investors/financial-results",
    "WTC": "https://www.wisetechglobal.com/investors/reports-and-presentations/",
    "XRO": "https://www.xero.com/au/investors/financial-information/",
    "COL": "https://www.colesgroup.com.au/investor-centre/?page=results-reports",
    "JBH": "https://investors.jbhifi.com.au/reports-and-presentations",
    "SHL": "https://investors.sonichealthcare.com/investors/?page=financial-reports",
    "COH": "https://www.cochlear.com/au/en/corporate/investors/results-and-presentations/financial-results",
    "RHC": "https://www.ramsayhealth.com/en/investors/results-and-reports/",
    "QBE": "https://www.qbe.com/investor-relations/reports-presentations",
    "ALL": "https://www.aristocrat.com/investors-and-governance/results-presentations/",
    "GMG": "https://www.goodman.com/investor-centre/results-and-reports",
    "TCL": "https://www.transurban.com/investor-centre/results-and-presentations",
    "STO": "https://www.santos.com/investors/results-presentations/",
    "REA": "https://www.rea-group.com/investor-centre/results-reports/",
}

# Known PDF URL patterns for major companies
# These let us skip IR page scraping and go directly to PDFs
_KNOWN_PDF_PATTERNS: dict[str, list[dict]] = {
    "CBA": [
        {
            "title": "FY2025 Profit Announcement",
            "pdf_url": "https://www.commbank.com.au/content/dam/commbank-assets/investors/docs/results/fy25/full-year-profit-announcement.pdf",
            "date": "2025-08-13",
        },
        {
            "title": "H1 FY2025 Profit Announcement",
            "pdf_url": "https://www.commbank.com.au/content/dam/commbank-assets/investors/docs/results/1h25/CBA-2025-Half-Year-Results-Profit-Announcement.pdf",
            "date": "2025-02-12",
        },
        {
            "title": "FY2024 Profit Announcement",
            "pdf_url": "https://www.commbank.com.au/content/dam/commbank-assets/investors/docs/results/fy24/CBA-FY24-Profit-Announcement.pdf",
            "date": "2024-08-14",
        },
        {
            "title": "H1 FY2024 Profit Announcement",
            "pdf_url": "https://www.commbank.com.au/content/dam/commbank-assets/investors/docs/results/1h24/CBA-2024-Half-Year-Results-Profit-Announcement.pdf",
            "date": "2024-02-14",
        },
    ],
}


class IRHarvester:
    """Finds earnings PDFs from company investor relations pages."""

    def __init__(self, api_key: Optional[str] = None) -> None:
        self.client = anthropic.AsyncAnthropic(api_key=api_key)
        self.extractor = PDFExtractor(api_key=api_key)

    async def get_results_pdfs(self, ticker: str) -> list[dict]:
        """Find results PDF URLs for a ticker.

        Strategy:
        1. Check known PDF patterns first (instant, no API calls)
        2. Scrape IR page for PDF links
        3. Fall back to web_search via PDFExtractor
        """
        ticker = ticker.upper()

        # Strategy 1: Known patterns
        if ticker in _KNOWN_PDF_PATTERNS:
            pdfs = _KNOWN_PDF_PATTERNS[ticker]
            logger.info(f"[ir] {ticker}: {len(pdfs)} known PDF URLs")
            return [{"ticker": ticker, **p} for p in pdfs]

        # Strategy 2: Scrape IR page
        if ticker in IR_PAGES:
            pdfs = await self._scrape_ir_page(ticker)
            if pdfs:
                return pdfs

        # Strategy 3: Web search fallback
        logger.info(f"[ir] {ticker}: falling back to web search")
        return await self.extractor.find_pdfs(ticker)

    async def _scrape_ir_page(self, ticker: str) -> list[dict]:
        """Fetch IR page and use Claude to extract PDF links."""
        url = IR_PAGES.get(ticker)
        if not url:
            return []

        try:
            ctx = ssl.create_default_context()
            req = urllib.request.Request(url, headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            })

            def do_fetch():
                with urllib.request.urlopen(req, context=ctx, timeout=20) as resp:
                    return resp.read().decode("utf-8", errors="replace")

            html = await asyncio.get_event_loop().run_in_executor(None, do_fetch)

            if not html or len(html) < 500:
                logger.warning(f"[ir] {ticker}: IR page too short or empty")
                return []

            # Use Claude to extract PDF links from the HTML
            # Truncate HTML to avoid token limits
            html_truncated = html[:50000]

            message = await self.client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=2048,
                messages=[{
                    "role": "user",
                    "content": (
                        f"Extract all PDF links from this investor relations HTML page for {ticker}.\n"
                        f"Only include links that point to earnings results PDFs "
                        f"(profit announcements, Appendix 4E, results releases).\n"
                        f"Return ONLY a JSON array: [{{\"title\": \"...\", \"pdf_url\": \"...\", \"date\": \"YYYY-MM-DD\"}}]\n"
                        f"Maximum 8 results, newest first. No markdown.\n\n"
                        f"HTML:\n{html_truncated}"
                    ),
                }],
            )

            raw = message.content[0].text.strip()
            start = raw.find("[")
            end = raw.rfind("]")
            if start == -1 or end == -1:
                return []

            items = json.loads(raw[start:end + 1])
            results = []
            for item in items:
                pdf_url = item.get("pdf_url", "")
                if pdf_url and ".pdf" in pdf_url.lower():
                    results.append({
                        "ticker": ticker,
                        "title": item.get("title", ""),
                        "date": item.get("date", ""),
                        "pdf_url": pdf_url,
                    })

            logger.info(f"[ir] {ticker}: found {len(results)} PDFs from IR page")
            return results

        except Exception as e:
            logger.warning(f"[ir] {ticker}: IR page scrape failed: {e}")
            return []

    async def get_latest_results(self, ticker: str) -> dict:
        """Get most recent results PDF and extract data."""
        pdfs = await self.get_results_pdfs(ticker)
        if not pdfs:
            return {}

        # Try the first (most recent) PDF
        return await self.extractor.extract_from_url(pdfs[0]["pdf_url"], ticker)

    async def harvest_all(self, ticker: str) -> list[dict]:
        """Find all available results PDFs and extract data from each."""
        pdfs = await self.get_results_pdfs(ticker)
        if not pdfs:
            return []

        results = []
        for pdf_info in pdfs:
            extracted = await self.extractor.extract_from_url(pdf_info["pdf_url"], ticker)
            if extracted:
                results.append(extracted)
            await asyncio.sleep(1)

        logger.info(f"[ir] {ticker}: extracted {len(results)} results from IR page PDFs")
        return results
