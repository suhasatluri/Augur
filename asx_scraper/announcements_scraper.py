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
        """Fetches announcement list from ASX API. Returns filtered earnings announcements."""
        ticker = ticker.upper()
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
            announcements = data if isinstance(data, list) else data.get("data", [])

        except Exception as e:
            logger.warning(f"[announcements] ASX API failed for {ticker}: {e}")
            return []

        # Filter to earnings-related announcements
        earnings = []
        for ann in announcements:
            title = ann.get("header", "") or ann.get("title", "")
            if any(kw.lower() in title.lower() for kw in EARNINGS_KEYWORDS):
                earnings.append({
                    "ticker": ticker,
                    "title": title,
                    "date": ann.get("document_date", ""),
                    "url": ann.get("url", ""),
                    "pdf_url": ann.get("document_release_url", ""),
                })

        logger.info(f"[announcements] {ticker}: {len(earnings)} earnings announcements found")
        return earnings[:8]  # Cap at 8 most recent

    async def parse_pdf(self, pdf_url: str, ticker: str) -> dict:
        """Downloads PDF and uses Claude Sonnet to extract structured data."""
        if not pdf_url:
            return {}

        try:
            # Download PDF
            ctx = ssl.create_default_context()
            req = urllib.request.Request(pdf_url, headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
            })

            def do_download():
                with urllib.request.urlopen(req, context=ctx, timeout=30) as resp:
                    return resp.read()

            pdf_bytes = await asyncio.get_event_loop().run_in_executor(None, do_download)

            if len(pdf_bytes) > 10_000_000:  # 10MB limit
                logger.warning(f"[announcements] PDF too large for {ticker}: {len(pdf_bytes)} bytes")
                return {}

            import base64
            pdf_b64 = base64.standard_b64encode(pdf_bytes).decode("ascii")

            # Send to Claude with PDF
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

    async def _upsert_earnings(self, ticker: str, extracted: dict, announcement: dict) -> bool:
        """Upsert extracted data into asx_earnings and asx_commentary."""
        reporting_date = extracted.get("reporting_date")
        if not reporting_date:
            return False

        try:
            pool = await get_pool()
            async with pool.acquire() as conn:
                # Upsert earnings row
                await conn.execute("""
                    INSERT INTO asx_earnings
                        (ticker, period, reporting_date, result_type,
                         revenue_aud_m, npat_aud_m, eps_basic_cents, eps_diluted_cents,
                         dividend_cents, announcement_url, data_source, data_confidence)
                    VALUES ($1, $2, $3::date, $4, $5, $6, $7, $8, $9, $10, $11, $12)
                    ON CONFLICT (ticker, reporting_date) DO UPDATE SET
                        period = COALESCE(EXCLUDED.period, asx_earnings.period),
                        result_type = COALESCE(EXCLUDED.result_type, asx_earnings.result_type),
                        revenue_aud_m = COALESCE(EXCLUDED.revenue_aud_m, asx_earnings.revenue_aud_m),
                        npat_aud_m = COALESCE(EXCLUDED.npat_aud_m, asx_earnings.npat_aud_m),
                        eps_basic_cents = COALESCE(EXCLUDED.eps_basic_cents, asx_earnings.eps_basic_cents),
                        eps_diluted_cents = COALESCE(EXCLUDED.eps_diluted_cents, asx_earnings.eps_diluted_cents),
                        dividend_cents = COALESCE(EXCLUDED.dividend_cents, asx_earnings.dividend_cents),
                        announcement_url = COALESCE(EXCLUDED.announcement_url, asx_earnings.announcement_url),
                        data_source = EXCLUDED.data_source,
                        data_confidence = EXCLUDED.data_confidence,
                        extracted_at = NOW()
                """,
                    ticker,
                    extracted.get("period"),
                    reporting_date,
                    extracted.get("result_type"),
                    extracted.get("revenue_aud_m"),
                    extracted.get("npat_aud_m"),
                    extracted.get("eps_basic_cents"),
                    extracted.get("eps_diluted_cents"),
                    extracted.get("dividend_cents"),
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
                        VALUES ($1, $2::date, $3, $4, $5)
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
