"""Announcements scraper — fetches ASX Appendix 4D/4E PDFs and extracts data via Claude."""

from __future__ import annotations

import asyncio
import io
import json
import logging
import ssl
import urllib.request
from typing import Optional

import anthropic

from db.schema import get_pool

logger = logging.getLogger(__name__)

ASX_ANN_URL = (
    "https://www.asx.com.au/asx/1/company/{ticker}/announcements"
    "?count=20&market_sensitive=true"
)

ASX_ANN_PDF_BASE = "https://announcements.asx.com.au/asxpdf/"

EARNINGS_KEYWORDS = [
    "Appendix 4D",
    "Appendix 4E",
    "Half Year Results",
    "Full Year Results",
    "Half-Year Results",
    "Annual Results",
    "Preliminary Final Report",
]

PDF_EXTRACTION_PROMPT = """Extract these fields from this ASX earnings announcement PDF. Return ONLY valid JSON — no markdown, no commentary.

{
  "period": "e.g. H1 FY2025 or FY2024",
  "result_type": "HALF_YEAR or FULL_YEAR",
  "reporting_date": "YYYY-MM-DD (the date this was released)",
  "revenue_aud_m": float or null,
  "npat_aud_m": float or null,
  "eps_basic_cents": float or null,
  "eps_diluted_cents": float or null,
  "dividend_cents": float or null,
  "result_vs_prior_pct": float or null,
  "guidance_next_period": "text or null",
  "management_quotes": [
    {"quote": "exact quote text", "type": "guidance/outlook/risk/positive"}
  ],
  "currency": "AUD/USD/NZD",
  "data_confidence": "HIGH/MED/LOW"
}

Rules:
- Only extract what is explicitly stated
- Never invent or estimate figures
- Use null for missing fields
- Convert all monetary values to AUD millions
- EPS in cents (not dollars)
- Extract up to 5 most significant management quotes
- data_confidence = HIGH if clear table with figures, MED if figures in text, LOW if unclear"""


class AnnouncementsScraper:
    """Fetches and parses ASX announcements. Focuses on Appendix 4D and 4E."""

    def __init__(self, api_key: Optional[str] = None) -> None:
        self.client = anthropic.AsyncAnthropic(api_key=api_key)

    async def get_earnings_announcements(self, ticker: str) -> list[dict]:
        """Fetches earnings announcement PDFs. Tries ASX API, falls back to Claude web_search."""
        ticker = ticker.upper()

        # Strategy 1: ASX undocumented API
        announcements = await self._try_asx_api(ticker)
        if announcements:
            return announcements[:8]

        # Strategy 2: Claude web_search to find earnings PDFs
        announcements = await self._try_web_search(ticker)
        return announcements[:8]

    async def _try_asx_api(self, ticker: str) -> list[dict]:
        """Try the ASX undocumented announcements API."""
        url = ASX_ANN_URL.format(ticker=ticker)
        try:
            ctx = ssl.create_default_context()
            req = urllib.request.Request(url, headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
                "Accept": "application/json",
            })

            def do_fetch():
                with urllib.request.urlopen(req, context=ctx, timeout=20) as resp:
                    return json.loads(resp.read().decode("utf-8"))

            data = await asyncio.get_event_loop().run_in_executor(None, do_fetch)
            raw = data if isinstance(data, list) else data.get("data", [])

            earnings = []
            for ann in raw:
                title = ann.get("header", "") or ann.get("title", "")
                if any(kw.lower() in title.lower() for kw in EARNINGS_KEYWORDS):
                    earnings.append({
                        "ticker": ticker,
                        "title": title,
                        "date": ann.get("document_date", ""),
                        "url": ann.get("url", ""),
                        "pdf_url": ann.get("document_release_url", ""),
                    })

            if earnings:
                logger.info(f"[announcements] ASX API: {ticker} — {len(earnings)} earnings found")
            return earnings

        except Exception as e:
            logger.debug(f"[announcements] ASX API failed for {ticker}: {e}")
            return []

    async def _try_web_search(self, ticker: str) -> list[dict]:
        """Use Claude web_search to find earnings PDF URLs for a ticker."""
        try:
            message = await self.client.messages.create(
                model="claude-sonnet-4-20250514",
                max_tokens=2048,
                tools=[{"type": "web_search_20250305", "name": "web_search", "max_uses": 3}],
                messages=[{
                    "role": "user",
                    "content": (
                        f"Find the most recent SHORT earnings announcement PDFs for ASX-listed "
                        f"company {ticker}. I need the Profit Announcement or Results Media Release "
                        f"PDFs — NOT the full Annual Report (too large).\n\n"
                        f"Good examples: 'Profit Announcement', 'Results Release', 'ASX Announcement', "
                        f"'Appendix 4E Preliminary Final Report' (short version, under 50 pages).\n"
                        f"Bad examples: 'Annual Report' (200+ pages), 'Investor Presentation'.\n\n"
                        f"Search for: {ticker} ASX profit announcement results release PDF\n\n"
                        f"Return ONLY a JSON array of objects with keys: title, date (YYYY-MM-DD), "
                        f"pdf_url. No markdown, no commentary — just the JSON array.\n"
                        f"Only include URLs that end in .pdf or are direct PDF links.\n"
                        f"Include up to 4 most recent earnings PDFs."
                    ),
                }],
                timeout=120.0,
            )

            # Extract text from response
            raw = ""
            for block in message.content:
                if hasattr(block, "text"):
                    raw = block.text
                    break

            if not raw.strip():
                logger.info(f"[announcements] Web search returned no text for {ticker}")
                return []

            # Parse JSON from response
            text = raw.strip()
            if text.startswith("```"):
                text = text.split("\n", 1)[1]
                if text.endswith("```"):
                    text = text[:text.rfind("```")]
                text = text.strip()

            # Find JSON array
            start = text.find("[")
            end = text.rfind("]")
            if start == -1 or end == -1:
                logger.warning(f"[announcements] No JSON array in web search response for {ticker}")
                return []

            items = json.loads(text[start:end + 1])
            earnings = []
            for item in items:
                pdf_url = item.get("pdf_url", "")
                if not pdf_url:
                    continue
                earnings.append({
                    "ticker": ticker,
                    "title": item.get("title", ""),
                    "date": item.get("date", ""),
                    "url": pdf_url,
                    "pdf_url": pdf_url,
                })

            logger.info(f"[announcements] Web search: {ticker} — {len(earnings)} PDF URLs found")
            return earnings

        except Exception as e:
            logger.warning(f"[announcements] Web search failed for {ticker}: {e}")
            return []

    async def parse_pdf(self, pdf_url: str, ticker: str) -> dict:
        """Downloads PDF and uses Claude Sonnet to extract structured data.

        Downloads the PDF, truncates to first 80 pages if needed, then sends to Claude.
        """
        if not pdf_url:
            return {}

        try:
            from asx_scraper.pdf_extractor import _download_pdf, _truncate_pdf

            pdf_bytes = await _download_pdf(pdf_url)

            if len(pdf_bytes) > 20_000_000:
                logger.warning(f"[announcements] PDF too large for {ticker}: {len(pdf_bytes)} bytes, skipping")
                return {}

            pdf_bytes = await _truncate_pdf(pdf_bytes, max_pages=80)

            import base64
            pdf_b64 = base64.standard_b64encode(pdf_bytes).decode("ascii")

            message = await self.client.messages.create(
                model="claude-sonnet-4-20250514",
                max_tokens=4096,
                messages=[{
                    "role": "user",
                    "content": [
                        {
                            "type": "document",
                            "source": {
                                "type": "base64",
                                "media_type": "application/pdf",
                                "data": pdf_b64,
                            },
                        },
                        {
                            "type": "text",
                            "text": f"Ticker: {ticker}\n\n{PDF_EXTRACTION_PROMPT}",
                        },
                    ],
                }],
                timeout=120.0,
            )

            raw = message.content[0].text.strip()
            # Strip markdown fences if present
            if raw.startswith("```"):
                raw = raw.split("\n", 1)[1]
                if raw.endswith("```"):
                    raw = raw[:raw.rfind("```")]
                raw = raw.strip()

            extracted = json.loads(raw)
            logger.info(
                f"[announcements] Extracted from PDF for {ticker}: "
                f"period={extracted.get('period')}, eps={extracted.get('eps_basic_cents')}"
            )
            return extracted

        except json.JSONDecodeError as e:
            logger.error(f"[announcements] JSON parse failed for {ticker}: {e}")
            return {}
        except Exception as e:
            logger.error(f"[announcements] PDF extraction failed for {ticker}: {e}")
            return {}

    async def _truncate_pdf(self, pdf_bytes: bytes, max_pages: int = 80) -> bytes:
        """Truncate a PDF to the first max_pages pages. Returns original if < max_pages."""
        try:
            from pypdf import PdfReader, PdfWriter

            def do_truncate():
                reader = PdfReader(io.BytesIO(pdf_bytes))
                if len(reader.pages) <= max_pages:
                    return pdf_bytes
                logger.info(f"[announcements] Truncating PDF from {len(reader.pages)} to {max_pages} pages")
                writer = PdfWriter()
                for i in range(min(max_pages, len(reader.pages))):
                    writer.add_page(reader.pages[i])
                output = io.BytesIO()
                writer.write(output)
                return output.getvalue()

            return await asyncio.get_event_loop().run_in_executor(None, do_truncate)
        except ImportError:
            logger.debug("[announcements] pypdf not installed — skipping truncation")
            return pdf_bytes
        except Exception as e:
            logger.warning(f"[announcements] PDF truncation failed: {e}")
            return pdf_bytes

    async def _upsert_earnings(self, ticker: str, extracted: dict, announcement: dict) -> bool:
        """Upsert extracted data into asx_earnings and asx_commentary."""
        reporting_date_str = extracted.get("reporting_date")
        if not reporting_date_str:
            return False

        from datetime import date as date_type
        try:
            reporting_date = date_type.fromisoformat(reporting_date_str)
        except (ValueError, TypeError):
            logger.warning(f"[announcements] Invalid date '{reporting_date_str}' for {ticker}")
            return False

        try:
            pool = await get_pool()
            async with pool.acquire() as conn:
                # Parse period_end_date if provided
                period_end = None
                ped_str = extracted.get("period_end_date")
                if ped_str:
                    try:
                        period_end = date_type.fromisoformat(ped_str)
                    except (ValueError, TypeError):
                        pass

                # Extract consensus if present in PDF
                consensus = extracted.get("consensus") or {}
                eps_consensus = consensus.get("eps_consensus_cents")
                revenue_consensus = consensus.get("revenue_consensus_aud_m")

                # Upsert earnings row
                await conn.execute("""
                    INSERT INTO asx_earnings
                        (ticker, period, reporting_date, period_end_date, result_type,
                         revenue_aud_m, npat_aud_m, eps_basic_cents, eps_diluted_cents,
                         dividend_cents, eps_consensus_cents, revenue_consensus_aud_m,
                         announcement_url, data_source, data_confidence)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15)
                    ON CONFLICT (ticker, reporting_date) DO UPDATE SET
                        period = COALESCE(EXCLUDED.period, asx_earnings.period),
                        period_end_date = COALESCE(EXCLUDED.period_end_date, asx_earnings.period_end_date),
                        result_type = COALESCE(EXCLUDED.result_type, asx_earnings.result_type),
                        revenue_aud_m = COALESCE(EXCLUDED.revenue_aud_m, asx_earnings.revenue_aud_m),
                        npat_aud_m = COALESCE(EXCLUDED.npat_aud_m, asx_earnings.npat_aud_m),
                        eps_basic_cents = COALESCE(EXCLUDED.eps_basic_cents, asx_earnings.eps_basic_cents),
                        eps_diluted_cents = COALESCE(EXCLUDED.eps_diluted_cents, asx_earnings.eps_diluted_cents),
                        dividend_cents = COALESCE(EXCLUDED.dividend_cents, asx_earnings.dividend_cents),
                        eps_consensus_cents = COALESCE(EXCLUDED.eps_consensus_cents, asx_earnings.eps_consensus_cents),
                        revenue_consensus_aud_m = COALESCE(EXCLUDED.revenue_consensus_aud_m, asx_earnings.revenue_consensus_aud_m),
                        announcement_url = COALESCE(EXCLUDED.announcement_url, asx_earnings.announcement_url),
                        data_source = EXCLUDED.data_source,
                        data_confidence = EXCLUDED.data_confidence,
                        extracted_at = NOW()
                """,
                    ticker,
                    extracted.get("period"),
                    reporting_date,
                    period_end,
                    extracted.get("result_type"),
                    extracted.get("revenue_aud_m"),
                    extracted.get("npat_aud_m"),
                    extracted.get("eps_basic_cents"),
                    extracted.get("eps_diluted_cents"),
                    extracted.get("dividend_cents"),
                    eps_consensus,
                    revenue_consensus,
                    announcement.get("pdf_url") or announcement.get("url"),
                    "pdf",
                    extracted.get("data_confidence", "MED"),
                )

                # Insert management commentary
                quotes = extracted.get("management_quotes") or []
                for q in quotes[:5]:
                    if not q.get("quote"):
                        continue
                    await conn.execute("""
                        INSERT INTO asx_commentary
                            (ticker, reporting_date, quote, quote_type, extracted_from)
                        VALUES ($1, $2, $3, $4, $5)
                    """,
                        ticker,
                        reporting_date,
                        q["quote"],
                        q.get("type"),
                        announcement.get("title", ""),
                    )

            logger.info(f"[announcements] Upserted earnings for {ticker} @ {reporting_date}")
            return True
        except Exception as e:
            logger.error(f"[announcements] DB upsert failed for {ticker}: {e}")
            return False

    async def scrape(self, ticker: str) -> list[dict]:
        """Main entry: fetch announcements, parse PDFs, upsert to DB."""
        ticker = ticker.upper()
        announcements = await self.get_earnings_announcements(ticker)

        if not announcements:
            logger.info(f"[announcements] No earnings announcements for {ticker}")
            return []

        results = []
        for ann in announcements:
            pdf_url = ann.get("pdf_url", "")
            if not pdf_url:
                continue

            extracted = await self.parse_pdf(pdf_url, ticker)
            if not extracted:
                continue

            saved = await self._upsert_earnings(ticker, extracted, ann)
            results.append({
                "ticker": ticker,
                "period": extracted.get("period"),
                "reporting_date": extracted.get("reporting_date"),
                "eps_basic_cents": extracted.get("eps_basic_cents"),
                "data_confidence": extracted.get("data_confidence"),
                "saved_to_db": saved,
            })
            await asyncio.sleep(1)  # Rate limit between PDFs

        logger.info(f"[announcements] {ticker}: extracted {len(results)} earnings records")
        return results
