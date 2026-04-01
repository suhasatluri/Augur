"""PDF Extractor — downloads ASX earnings PDFs and extracts structured data via Claude."""

from __future__ import annotations

import asyncio
import base64
import io
import json
import logging
import ssl
import urllib.request
from datetime import date as date_type
from typing import Optional

import anthropic

logger = logging.getLogger(__name__)

EXTRACTION_PROMPT = """You are reading an ASX earnings announcement PDF.
Extract these fields. Return ONLY valid JSON — no markdown, no commentary.
Never invent data. Use null for missing fields.
All monetary values in AUD millions. EPS and dividends in Australian cents.

{
  "ticker": "string",
  "company_name": "string",
  "period": "e.g. H1 FY2025 or FY2025",
  "result_type": "HALF_YEAR or FULL_YEAR",
  "period_end_date": "YYYY-MM-DD (end of the reporting period)",
  "reporting_date": "YYYY-MM-DD (date this announcement was released)",
  "revenue_aud_m": float or null,
  "npat_aud_m": float or null,
  "npat_vs_prior_pct": float or null,
  "eps_basic_cents": float or null,
  "eps_diluted_cents": float or null,
  "dividend_final_cents": float or null,
  "dividend_interim_cents": float or null,
  "consensus": {
    "eps_consensus_cents": float or null,
    "revenue_consensus_aud_m": float or null,
    "source": "string or null (e.g. 'Visible Alpha', 'Bloomberg', 'broker consensus')"
  },
  "guidance_next_period": "text or null",
  "management_quotes": [
    {
      "speaker": "CEO/CFO/Chairman",
      "quote": "max 80 words, exact text",
      "sentiment": "positive/negative/neutral",
      "category": "outlook/risk/growth/cost"
    }
  ],
  "key_metrics": {
    "roe_pct": float or null,
    "net_interest_margin_pct": float or null,
    "cost_to_income_pct": float or null,
    "operating_cash_flow_aud_m": float or null
  },
  "data_confidence": "HIGH/MED/LOW"
}

Rules:
- Only extract what is EXPLICITLY stated in the document
- Never estimate or calculate values not directly stated
- Use null for any field you cannot find
- If the company reports in USD (e.g. BHP, CSL, RIO), convert to AUD using the rate stated in the PDF. If no rate is stated, use null and set data_confidence to MED.
- EPS must be in CENTS (multiply by 100 if stated in dollars)
- Dividends must be in CENTS
- Revenue and NPAT must be in AUD MILLIONS (divide by 1000 if stated in billions)
- data_confidence = HIGH if clear financial summary table, MED if figures scattered in text, LOW if unclear
- Extract up to 5 most significant management quotes with speaker attribution
- For period: use format "H1 FY2025" for half-year, "FY2025" for full-year

CONSENSUS EXTRACTION (important):
- Many ASX earnings PDFs mention analyst consensus, broker estimates, or market expectations
- Look for phrases like: "vs consensus", "analyst estimate", "market expectation", "broker forecast", "Visible Alpha consensus", "Bloomberg consensus", "ahead of expectations", "below market forecasts"
- If the PDF states a consensus EPS or revenue figure, extract it into the consensus object
- If it says "beat consensus by X%" or "X% above consensus", extract the consensus source
- If no consensus figures are mentioned anywhere in the PDF, set all consensus fields to null
- NEVER guess or calculate consensus — only extract if explicitly stated"""

# Search queries ordered by specificity
_SEARCH_QUERIES = [
    'site:announcements.asx.com.au {ticker} "Appendix 4E"',
    'site:announcements.asx.com.au {ticker} "Appendix 4D"',
    'site:announcements.asx.com.au {ticker} "full year results" OR "half year results"',
    '{ticker} ASX "profit announcement" PDF results',
]


def _parse_json_response(raw: str) -> dict:
    """Extract JSON object from model response, stripping markdown fences."""
    text = raw.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1]
        if text.endswith("```"):
            text = text[:text.rfind("```")]
        text = text.strip()
    # Find outermost JSON object
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        return json.loads(text[start:end + 1])
    return json.loads(text)


async def _download_pdf(url: str, timeout: int = 45) -> bytes:
    """Download a PDF from a URL. Returns bytes."""
    ctx = ssl.create_default_context()
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    })

    def do_download():
        with urllib.request.urlopen(req, context=ctx, timeout=timeout) as resp:
            return resp.read()

    return await asyncio.get_event_loop().run_in_executor(None, do_download)


async def _truncate_pdf(pdf_bytes: bytes, max_pages: int = 80) -> bytes:
    """Truncate PDF to first max_pages. Returns original if already small enough."""
    try:
        from pypdf import PdfReader, PdfWriter

        def do_truncate():
            reader = PdfReader(io.BytesIO(pdf_bytes))
            if len(reader.pages) <= max_pages:
                return pdf_bytes, len(reader.pages)
            writer = PdfWriter()
            for i in range(max_pages):
                writer.add_page(reader.pages[i])
            output = io.BytesIO()
            writer.write(output)
            return output.getvalue(), len(reader.pages)

        result, total_pages = await asyncio.get_event_loop().run_in_executor(None, do_truncate)
        if total_pages > max_pages:
            logger.info(f"[pdf] Truncated {total_pages} → {max_pages} pages")
        return result
    except ImportError:
        logger.debug("[pdf] pypdf not installed — skipping truncation")
        return pdf_bytes
    except Exception as e:
        logger.warning(f"[pdf] Truncation failed: {e}")
        return pdf_bytes


class PDFExtractor:
    """Downloads ASX earnings PDFs and extracts structured financial data via Claude."""

    def __init__(self, api_key: Optional[str] = None) -> None:
        self.client = anthropic.AsyncAnthropic(api_key=api_key)

    async def extract_from_url(self, pdf_url: str, ticker: str) -> dict:
        """Download a PDF and extract structured earnings data."""
        ticker = ticker.upper()
        if not pdf_url:
            return {}

        try:
            logger.info(f"[pdf] Downloading {pdf_url}")
            pdf_bytes = await _download_pdf(pdf_url)

            if len(pdf_bytes) > 25_000_000:
                logger.warning(f"[pdf] PDF too large ({len(pdf_bytes)} bytes), skipping")
                return {}

            pdf_bytes = await _truncate_pdf(pdf_bytes, max_pages=80)

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
                            "text": f"Ticker: {ticker}\n\n{EXTRACTION_PROMPT}",
                        },
                    ],
                }],
            )

            raw = message.content[0].text.strip()
            extracted = _parse_json_response(raw)
            extracted["_pdf_url"] = pdf_url
            extracted["_ticker"] = ticker

            logger.info(
                f"[pdf] {ticker}: period={extracted.get('period')}, "
                f"revenue={extracted.get('revenue_aud_m')}, "
                f"npat={extracted.get('npat_aud_m')}, "
                f"eps={extracted.get('eps_basic_cents')}c, "
                f"confidence={extracted.get('data_confidence')}"
            )
            return extracted

        except json.JSONDecodeError as e:
            logger.error(f"[pdf] JSON parse failed for {ticker}: {e}")
            return {}
        except Exception as e:
            logger.error(f"[pdf] Extraction failed for {ticker} ({pdf_url}): {e}")
            return {}

    async def find_pdfs(self, ticker: str) -> list[dict]:
        """Use Claude web_search to discover earnings PDF URLs for a ticker.

        Tries multiple search strategies:
        1. site:announcements.asx.com.au — Appendix 4E
        2. site:announcements.asx.com.au — Appendix 4D
        3. site:announcements.asx.com.au — results keywords
        4. General search for profit announcement PDFs
        """
        ticker = ticker.upper()
        all_urls: dict[str, dict] = {}  # url → {title, date}

        for query_template in _SEARCH_QUERIES:
            query = query_template.format(ticker=ticker)
            urls = await self._search_for_pdfs(ticker, query)
            for item in urls:
                url = item.get("pdf_url", "")
                if url and url not in all_urls:
                    all_urls[url] = item

            if len(all_urls) >= 4:
                break
            await asyncio.sleep(0.5)

        results = list(all_urls.values())
        logger.info(f"[pdf] {ticker}: found {len(results)} unique PDF URLs")
        return results

    async def _search_for_pdfs(self, ticker: str, query: str) -> list[dict]:
        """Run a single web_search query to find PDF URLs."""
        try:
            message = await self.client.messages.create(
                model="claude-sonnet-4-20250514",
                max_tokens=2048,
                tools=[{"type": "web_search_20250305", "name": "web_search", "max_uses": 2}],
                messages=[{
                    "role": "user",
                    "content": (
                        f"Search for: {query}\n\n"
                        f"Return ONLY a JSON array of objects with keys: title, date (YYYY-MM-DD if known), pdf_url.\n"
                        f"Only include direct PDF links (URLs ending in .pdf).\n"
                        f"No markdown, no commentary — just the JSON array.\n"
                        f"Maximum 4 results."
                    ),
                }],
            )

            raw = ""
            for block in message.content:
                if hasattr(block, "text"):
                    raw = block.text
                    break

            if not raw.strip():
                return []

            text = raw.strip()
            if text.startswith("```"):
                text = text.split("\n", 1)[1]
                if "```" in text:
                    text = text[:text.rfind("```")]
                text = text.strip()

            start = text.find("[")
            end = text.rfind("]")
            if start == -1 or end == -1:
                return []

            items = json.loads(text[start:end + 1])
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

            return results

        except Exception as e:
            logger.debug(f"[pdf] Web search failed for '{query}': {e}")
            return []

    async def find_and_extract(
        self, ticker: str, result_type: str = "both"
    ) -> list[dict]:
        """Find PDF URLs via web_search, download, and extract structured data.

        Args:
            ticker: ASX ticker code
            result_type: "both", "full_year", or "half_year"

        Returns list of extracted records sorted newest first.
        """
        ticker = ticker.upper()
        pdfs = await self.find_pdfs(ticker)

        if not pdfs:
            logger.info(f"[pdf] No PDFs found for {ticker}")
            return []

        results = []
        for pdf_info in pdfs:
            extracted = await self.extract_from_url(pdf_info["pdf_url"], ticker)
            if not extracted:
                continue

            # Filter by result_type if requested
            rt = (extracted.get("result_type") or "").upper()
            if result_type == "full_year" and rt != "FULL_YEAR":
                continue
            if result_type == "half_year" and rt != "HALF_YEAR":
                continue

            results.append(extracted)
            await asyncio.sleep(1)

        # Sort by reporting_date descending
        def sort_key(r):
            try:
                return date_type.fromisoformat(r.get("reporting_date", "1900-01-01"))
            except (ValueError, TypeError):
                return date_type(1900, 1, 1)

        results.sort(key=sort_key, reverse=True)

        # Deduplicate by period
        seen_periods: set[str] = set()
        deduped = []
        for r in results:
            period = r.get("period", "")
            if period and period in seen_periods:
                continue
            seen_periods.add(period)
            deduped.append(r)

        logger.info(f"[pdf] {ticker}: extracted {len(deduped)} unique periods")
        return deduped
