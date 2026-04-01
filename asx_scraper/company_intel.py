"""Company Intelligence Harvester — fetches quarterly updates and investor presentations.

Leading indicators between earnings results: trading updates, operational reviews,
investor presentations. These signal direction before the numbers land.
"""

from __future__ import annotations

import asyncio
import json
import logging
import ssl
import urllib.request
from datetime import datetime, timedelta
from typing import Optional

import anthropic

from asx_scraper.pdf_extractor import PDFExtractor, _download_pdf, _truncate_pdf
from db.schema import get_pool

logger = logging.getLogger(__name__)

COMPANY_INTEL_PAGES = {
    "CBA": {
        "quarterly_url": "https://www.commbank.com.au/about-us/investors/financial-information/quarterly-updates.html",
        "results_url": "https://www.commbank.com.au/about-us/investors.html",
        "ir_base": "https://www.commbank.com.au",
    },
    "BHP": {
        "quarterly_url": "https://www.bhp.com/investors/financial-results-operational-reviews",
        "results_url": "https://www.bhp.com/investors",
        "ir_base": "https://www.bhp.com",
    },
    "CSL": {
        "quarterly_url": None,
        "results_url": "https://www.csl.com/investors/investor-news",
        "ir_base": "https://www.csl.com",
    },
    "WBC": {
        "quarterly_url": "https://www.westpac.com.au/about-westpac/investor-centre/results-and-presentations",
        "results_url": "https://www.westpac.com.au/about-westpac/investor-centre",
        "ir_base": "https://www.westpac.com.au",
    },
    "ANZ": {
        "quarterly_url": "https://www.anz.com/shareholder/centre/reporting/trading-updates",
        "results_url": "https://www.anz.com/shareholder",
        "ir_base": "https://www.anz.com",
    },
    "NAB": {
        "quarterly_url": "https://www.nab.com.au/about-us/investor-relations/results-and-presentations",
        "results_url": "https://www.nab.com.au/about-us/investor-relations",
        "ir_base": "https://www.nab.com.au",
    },
    "WES": {
        "quarterly_url": None,
        "results_url": "https://www.wesfarmers.com.au/investors/results-reports-and-presentations",
        "ir_base": "https://www.wesfarmers.com.au",
    },
    "WOW": {
        "quarterly_url": "https://www.woolworthsgroup.com.au/investors/results-and-presentations",
        "results_url": "https://www.woolworthsgroup.com.au/investors",
        "ir_base": "https://www.woolworthsgroup.com.au",
    },
    "RIO": {
        "quarterly_url": "https://www.riotinto.com/investors/reports-and-presentations",
        "results_url": "https://www.riotinto.com/investors",
        "ir_base": "https://www.riotinto.com",
    },
    "FMG": {
        "quarterly_url": "https://www.fmgl.com.au/investors/asx-announcements",
        "results_url": "https://www.fmgl.com.au/investors",
        "ir_base": "https://www.fmgl.com.au",
    },
    "MQG": {
        "quarterly_url": "https://www.macquarie.com/au/en/investors/quarterly-updates.html",
        "results_url": "https://www.macquarie.com/au/en/investors.html",
        "ir_base": "https://www.macquarie.com",
    },
    "TLS": {
        "quarterly_url": None,
        "results_url": "https://www.telstra.com.au/aboutus/investors/financial-information",
        "ir_base": "https://www.telstra.com.au",
    },
    "WTC": {
        "quarterly_url": None,
        "results_url": "https://www.wisetech.com/company/investor-information",
        "ir_base": "https://www.wisetech.com",
    },
    "XRO": {
        "quarterly_url": None,
        "results_url": "https://www.xero.com/au/company/investors",
        "ir_base": "https://www.xero.com",
    },
    "COL": {
        "quarterly_url": None,
        "results_url": "https://www.colesgroup.com.au/investors",
        "ir_base": "https://www.colesgroup.com.au",
    },
    "JBH": {
        "quarterly_url": None,
        "results_url": "https://www.jbhifi.com.au/pages/investor-centre",
        "ir_base": "https://www.jbhifi.com.au",
    },
    "COH": {
        "quarterly_url": None,
        "results_url": "https://www.cochlear.com/au/en/corporate/investors",
        "ir_base": "https://www.cochlear.com",
    },
    "RHC": {
        "quarterly_url": None,
        "results_url": "https://www.ramsayhealth.com/investor-centre",
        "ir_base": "https://www.ramsayhealth.com",
    },
    "QBE": {
        "quarterly_url": None,
        "results_url": "https://www.qbe.com/au/about/investor-information",
        "ir_base": "https://www.qbe.com",
    },
}

QUARTERLY_EXTRACTION_PROMPT = """You are reading an ASX company quarterly trading update or operational review PDF.
Extract forward-looking signals. Return ONLY valid JSON — no markdown, no commentary.

{
  "ticker": "string",
  "quarter": "e.g. Q1 FY2026 or 3Q FY2025",
  "period_end_date": "YYYY-MM-DD",
  "volume_metrics": {"key operational metric name": "value with units"},
  "margin_trend": "IMPROVING or STABLE or DECLINING or UNKNOWN",
  "cost_trend": "IMPROVING or STABLE or WORSENING or UNKNOWN",
  "outlook_sentiment": "positive or neutral or negative",
  "guidance_update": "text or null",
  "key_signals": ["up to 5 forward-looking signals as short sentences"],
  "management_quotes": [{"quote": "exact text, max 60 words", "sentiment": "positive/neutral/negative"}],
  "risks_mentioned": ["list of risks"],
  "data_confidence": "HIGH/MED/LOW"
}

Rules:
- Only extract what is explicitly stated — never invent data
- Focus on FORWARD-LOOKING signals, not backward-looking results
- margin_trend: compare to prior period if stated, else UNKNOWN
- cost_trend: look for cost-out programs, wage pressure, inflation mentions
- outlook_sentiment: management tone on near-term outlook
- key_signals: most important 5 things an analyst would want to know"""

PRESENTATION_EXTRACTION_PROMPT = """You are reading an ASX company investor presentation PDF.
Extract strategic signals. Return ONLY valid JSON — no markdown, no commentary.

{
  "ticker": "string",
  "presentation_date": "YYYY-MM-DD",
  "presentation_type": "results briefing / investor day / strategy update / AGM",
  "financial_targets": ["list of stated financial targets with timeframes"],
  "growth_initiatives": ["list of growth investments or new projects"],
  "headwinds_mentioned": ["list of challenges or risks discussed"],
  "tailwinds_mentioned": ["list of positive macro or sector trends noted"],
  "guidance_language": "1-2 sentence summary of forward guidance tone",
  "management_confidence": "HIGH or MEDIUM or LOW",
  "data_confidence": "HIGH/MED/LOW"
}

Rules:
- Only extract what is explicitly stated
- financial_targets: include timeframes (e.g. "ROE >12% by FY2027")
- guidance_language: summarise the overall tone, not individual quotes
- management_confidence: HIGH if strong guidance with specific targets, MEDIUM if cautious/hedged, LOW if withdrawn/vague"""


class CompanyIntelHarvester:
    """Fetches quarterly updates and investor presentations from company IR pages."""

    INTEL_TTL_DAYS = 7

    def __init__(self, api_key: Optional[str] = None) -> None:
        self.client = anthropic.AsyncAnthropic(api_key=api_key)
        self.pdf_extractor = PDFExtractor(api_key=api_key)

    async def _find_pdf_via_search(self, ticker: str, doc_type: str) -> Optional[str]:
        """Use Claude web_search to find a quarterly/presentation PDF URL."""
        queries = {
            "quarterly": f'{ticker} ASX quarterly update OR "trading update" OR "operational review" PDF 2025 OR 2026',
            "presentation": f'{ticker} ASX "investor presentation" OR "results presentation" PDF 2025 OR 2026',
        }
        query = queries.get(doc_type, queries["quarterly"])

        try:
            message = await self.client.messages.create(
                model="claude-sonnet-4-20250514",
                max_tokens=1024,
                tools=[{"type": "web_search_20250305", "name": "web_search", "max_uses": 2}],
                messages=[{
                    "role": "user",
                    "content": (
                        f"Search: {query}\n\n"
                        f"Return ONLY a JSON array of objects with keys: title, date (YYYY-MM-DD), pdf_url.\n"
                        f"Only include direct PDF links. Maximum 3 results, newest first.\n"
                        f"No markdown, no commentary."
                    ),
                }],
            )

            raw = ""
            for block in message.content:
                if hasattr(block, "text"):
                    raw = block.text
                    break

            if not raw.strip():
                return None

            text = raw.strip()
            if text.startswith("```"):
                text = text.split("\n", 1)[1]
                if "```" in text:
                    text = text[:text.rfind("```")]
                text = text.strip()

            start = text.find("[")
            end = text.rfind("]")
            if start == -1 or end == -1:
                return None

            items = json.loads(text[start:end + 1])
            for item in items:
                url = item.get("pdf_url", "")
                if url and ".pdf" in url.lower():
                    return url

            return None
        except Exception as e:
            logger.debug(f"[intel] Web search for {ticker} {doc_type} failed: {e}")
            return None

    async def _extract_from_pdf(self, pdf_url: str, ticker: str, prompt: str) -> dict:
        """Download and extract from a PDF using a specific prompt."""
        try:
            import base64
            pdf_bytes = await _download_pdf(pdf_url, timeout=45)
            if len(pdf_bytes) > 25_000_000:
                return {}
            pdf_bytes = await _truncate_pdf(pdf_bytes, max_pages=60)
            pdf_b64 = base64.standard_b64encode(pdf_bytes).decode("ascii")

            message = await self.client.messages.create(
                model="claude-sonnet-4-20250514",
                max_tokens=2048,
                messages=[{
                    "role": "user",
                    "content": [
                        {
                            "type": "document",
                            "source": {"type": "base64", "media_type": "application/pdf", "data": pdf_b64},
                        },
                        {"type": "text", "text": f"Ticker: {ticker}\n\n{prompt}"},
                    ],
                }],
            )

            raw = message.content[0].text.strip()
            if raw.startswith("```"):
                raw = raw.split("\n", 1)[1]
                if raw.endswith("```"):
                    raw = raw[:raw.rfind("```")]
                raw = raw.strip()

            start = raw.find("{")
            end = raw.rfind("}")
            if start != -1 and end != -1:
                return json.loads(raw[start:end + 1])
            return json.loads(raw)

        except Exception as e:
            logger.warning(f"[intel] PDF extraction failed for {ticker}: {e}")
            return {}

    async def get_quarterly_update(self, ticker: str) -> dict:
        """Fetch most recent quarterly trading update for a ticker."""
        ticker = ticker.upper()
        logger.info(f"[intel] Fetching quarterly update for {ticker}")

        pdf_url = await self._find_pdf_via_search(ticker, "quarterly")
        if not pdf_url:
            logger.info(f"[intel] {ticker}: no quarterly PDF found")
            return {}

        logger.info(f"[intel] {ticker}: quarterly PDF → {pdf_url}")
        result = await self._extract_from_pdf(pdf_url, ticker, QUARTERLY_EXTRACTION_PROMPT)
        if result:
            result["_pdf_url"] = pdf_url
        return result

    async def get_investor_presentation(self, ticker: str) -> dict:
        """Fetch most recent investor presentation for a ticker."""
        ticker = ticker.upper()
        logger.info(f"[intel] Fetching investor presentation for {ticker}")

        pdf_url = await self._find_pdf_via_search(ticker, "presentation")
        if not pdf_url:
            logger.info(f"[intel] {ticker}: no presentation PDF found")
            return {}

        logger.info(f"[intel] {ticker}: presentation PDF → {pdf_url}")
        result = await self._extract_from_pdf(pdf_url, ticker, PRESENTATION_EXTRACTION_PROMPT)
        if result:
            result["_pdf_url"] = pdf_url
        return result

    def _combine_signals(self, quarterly: dict, presentation: dict) -> dict:
        """Combine quarterly and presentation data into unified signals."""
        signals: list[str] = []
        outlook = "neutral"

        # From quarterly
        if quarterly:
            for sig in quarterly.get("key_signals", []):
                signals.append(sig)
            q_outlook = quarterly.get("outlook_sentiment", "neutral")
            if q_outlook in ("positive", "negative"):
                outlook = q_outlook

        # From presentation
        if presentation:
            for target in presentation.get("financial_targets", []):
                signals.append(f"Target: {target}")
            for growth in presentation.get("growth_initiatives", [])[:2]:
                signals.append(f"Growth: {growth}")
            for risk in presentation.get("headwinds_mentioned", [])[:2]:
                signals.append(f"Risk: {risk}")

            # Presentation confidence can upgrade/downgrade outlook
            mgmt_conf = presentation.get("management_confidence", "MEDIUM")
            if mgmt_conf == "HIGH" and outlook != "negative":
                outlook = "positive"
            elif mgmt_conf == "LOW" and outlook != "positive":
                outlook = "negative"

        return {
            "leading_indicators": signals[:10],
            "overall_outlook": outlook,
            "margin_trend": quarterly.get("margin_trend", "UNKNOWN") if quarterly else "UNKNOWN",
            "cost_trend": quarterly.get("cost_trend", "UNKNOWN") if quarterly else "UNKNOWN",
            "guidance_update": quarterly.get("guidance_update") if quarterly else None,
            "guidance_language": presentation.get("guidance_language") if presentation else None,
            "risks": (quarterly.get("risks_mentioned", []) if quarterly else [])
                     + (presentation.get("headwinds_mentioned", []) if presentation else []),
        }

    async def harvest(self, ticker: str) -> dict:
        """Combine quarterly + presentation data. Store in Neon."""
        ticker = ticker.upper()

        # Check cache
        try:
            pool = await get_pool()
            async with pool.acquire() as conn:
                cached = await conn.fetchrow(
                    "SELECT * FROM asx_company_intel WHERE ticker = $1", ticker
                )
                if cached and cached["next_refresh_at"] and cached["next_refresh_at"] > datetime.utcnow():
                    logger.info(f"[intel] {ticker}: using cached data (expires {cached['next_refresh_at']})")
                    return {
                        "ticker": ticker,
                        "quarterly": json.loads(cached["quarterly_data"]) if cached["quarterly_data"] else {},
                        "presentation": json.loads(cached["presentation_data"]) if cached["presentation_data"] else {},
                        "combined": json.loads(cached["combined_signals"]) if cached["combined_signals"] else {},
                        "cached": True,
                    }
        except Exception:
            pass

        # Fetch both in parallel
        quarterly, presentation = await asyncio.gather(
            self.get_quarterly_update(ticker),
            self.get_investor_presentation(ticker),
            return_exceptions=True,
        )

        if isinstance(quarterly, Exception):
            logger.warning(f"[intel] {ticker} quarterly error: {quarterly}")
            quarterly = {}
        if isinstance(presentation, Exception):
            logger.warning(f"[intel] {ticker} presentation error: {presentation}")
            presentation = {}

        combined = self._combine_signals(quarterly, presentation)

        confidence = "LOW"
        if quarterly and presentation:
            confidence = "HIGH"
        elif quarterly or presentation:
            confidence = "MED"

        # Store in Neon
        try:
            pool = await get_pool()
            async with pool.acquire() as conn:
                await conn.execute("""
                    INSERT INTO asx_company_intel
                        (ticker, quarterly_data, presentation_data, combined_signals,
                         quarterly_pdf_url, presentation_pdf_url, harvested_at,
                         next_refresh_at, data_confidence)
                    VALUES ($1, $2, $3, $4, $5, $6, NOW(), $7, $8)
                    ON CONFLICT (ticker) DO UPDATE SET
                        quarterly_data = EXCLUDED.quarterly_data,
                        presentation_data = EXCLUDED.presentation_data,
                        combined_signals = EXCLUDED.combined_signals,
                        quarterly_pdf_url = EXCLUDED.quarterly_pdf_url,
                        presentation_pdf_url = EXCLUDED.presentation_pdf_url,
                        harvested_at = NOW(),
                        next_refresh_at = EXCLUDED.next_refresh_at,
                        data_confidence = EXCLUDED.data_confidence
                """,
                    ticker,
                    json.dumps(quarterly, default=str) if quarterly else None,
                    json.dumps(presentation, default=str) if presentation else None,
                    json.dumps(combined, default=str),
                    quarterly.get("_pdf_url") if quarterly else None,
                    presentation.get("_pdf_url") if presentation else None,
                    datetime.utcnow() + timedelta(days=self.INTEL_TTL_DAYS),
                    confidence,
                )
            logger.info(f"[intel] {ticker}: stored (confidence={confidence})")
        except Exception as e:
            logger.error(f"[intel] {ticker}: DB store failed: {e}")

        result = {
            "ticker": ticker,
            "quarterly": quarterly,
            "presentation": presentation,
            "combined": combined,
            "data_confidence": confidence,
            "cached": False,
        }
        return result
