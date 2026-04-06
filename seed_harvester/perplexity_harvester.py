"""Perplexity Sonar harvester — real-time financial news synthesis for fast layer.

Complements Claude web_search with Perplexity's real-time search.
Used in fast layer only — for current news, analyst sentiment, and recent developments.
Cost: ~$0.005 per query. TTL: 2 hours (same as fast layer).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)

_BASE_URL = "https://api.perplexity.ai/chat/completions"
_MODEL = "sonar"

# Module-level accumulator — reset per simulation in pipeline.py
_session_usage = {
    "requests": 0,
    "prompt_tokens": 0,
    "completion_tokens": 0,
    "cost_usd": 0.0,
}


def reset_session_usage():
    global _session_usage
    _session_usage = {
        "requests": 0,
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "cost_usd": 0.0,
    }


def get_session_usage() -> dict:
    return dict(_session_usage)

_FINANCIAL_NEWS_PROMPT = """You are a financial analyst assistant.
Research {ticker} (ASX:{ticker}) and provide a structured analysis of the last 30 days.

Focus specifically on:
1. Any analyst estimate revisions (upgrades or downgrades)
2. Management commentary or guidance updates
3. Sector conditions affecting {ticker}
4. Any material ASX announcements
5. Market sentiment toward {ticker}
6. Key risks or opportunities emerging

Upcoming reporting date: {reporting_date}

Return your analysis in this exact JSON format:
{{
  "analyst_sentiment": "bullish/neutral/bearish",
  "recent_estimate_revisions": "UP/DOWN/NEUTRAL/UNKNOWN",
  "revision_details": "text description or null",
  "material_news": ["list of significant news items"],
  "sector_conditions": "text summary",
  "key_risks": ["list of risks"],
  "key_opportunities": ["list of opportunities"],
  "management_tone": "confident/cautious/mixed/unknown",
  "data_freshness": "how recent is this data",
  "sources_cited": ["list of source URLs"]
}}

Return ONLY valid JSON. No markdown, no explanation, no preamble."""


def _parse_json(text: str) -> dict:
    """Extract JSON from Perplexity response, handling markdown fences."""
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1]
        if "```" in text:
            text = text[:text.rfind("```")]
        text = text.strip()
    # Find JSON object
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        return json.loads(text[start:end + 1])
    return json.loads(text)


class PerplexityHarvester:
    """Uses Perplexity Sonar for real-time financial news synthesis."""

    def __init__(self) -> None:
        self._api_key: Optional[str] = None

    def _get_key(self) -> Optional[str]:
        if self._api_key is None:
            self._api_key = os.getenv("PERPLEXITY_API_KEY", "")
        return self._api_key or None

    async def get_financial_news(
        self, ticker: str, reporting_date: Optional[str] = None,
    ) -> dict:
        """Query Perplexity Sonar for real-time financial news and analyst sentiment.

        Returns structured dict or empty dict on failure. Never crashes.
        """
        api_key = self._get_key()
        if not api_key:
            logger.info("[perplexity] PERPLEXITY_API_KEY not set — skipping")
            return {}

        ticker = ticker.upper()
        prompt = _FINANCIAL_NEWS_PROMPT.format(
            ticker=ticker,
            reporting_date=reporting_date or "unknown",
        )

        try:
            import requests

            def do_request():
                resp = requests.post(
                    _BASE_URL,
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": _MODEL,
                        "messages": [{"role": "user", "content": prompt}],
                    },
                    timeout=30,
                )
                resp.raise_for_status()
                return resp.json()

            data = await asyncio.get_event_loop().run_in_executor(None, do_request)

            raw = data["choices"][0]["message"]["content"]
            result = _parse_json(raw)

            # Capture token usage for cost tracking
            usage = data.get("usage", {})
            prompt_tokens = usage.get("prompt_tokens", 0)
            completion_tokens = usage.get("completion_tokens", 0)
            # $0.005 flat + $1/M input + $1/M output
            request_cost = (
                0.005
                + prompt_tokens / 1_000_000 * 1.00
                + completion_tokens / 1_000_000 * 1.00
            )

            # Update module-level session accumulator
            _session_usage["requests"] += 1
            _session_usage["prompt_tokens"] += prompt_tokens
            _session_usage["completion_tokens"] += completion_tokens
            _session_usage["cost_usd"] += request_cost

            # Add API-level citations if present
            citations = data.get("citations", [])
            if citations and "sources_cited" not in result:
                result["sources_cited"] = citations
            elif citations:
                result["sources_cited"] = list(set(result.get("sources_cited", []) + citations))

            result["_ticker"] = ticker
            result["_model"] = data.get("model", _MODEL)
            result["_cost"] = round(request_cost, 6)
            result["_usage"] = {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "estimated_cost_usd": round(request_cost, 6),
                "model": _MODEL,
            }

            logger.info(
                f"[perplexity] {ticker}: sentiment={result.get('analyst_sentiment')}, "
                f"revisions={result.get('recent_estimate_revisions')}, "
                f"cost=${result.get('_cost', 0):.4f}"
            )
            return result

        except json.JSONDecodeError as e:
            logger.warning(f"[perplexity] JSON parse failed for {ticker}: {e}")
            return {}
        except Exception as e:
            logger.warning(f"[perplexity] Request failed for {ticker}: {e}")
            return {}

    def to_seed_context(self, news: dict) -> str:
        """Format Perplexity news as a context block for the fast layer prompt."""
        if not news:
            return ""

        lines = ["\nPERPLEXITY REAL-TIME NEWS (last 30 days):"]

        sentiment = news.get("analyst_sentiment", "unknown")
        revisions = news.get("recent_estimate_revisions", "UNKNOWN")
        lines.append(f"Analyst sentiment: {sentiment} | Estimate revisions: {revisions}")

        details = news.get("revision_details")
        if details:
            lines.append(f"Revision details: {details}")

        mgmt = news.get("management_tone", "unknown")
        lines.append(f"Management tone: {mgmt}")

        material = news.get("material_news", [])
        if material:
            lines.append("Material news:")
            for item in material[:5]:
                lines.append(f"  - {item}")

        sector = news.get("sector_conditions")
        if sector:
            lines.append(f"Sector conditions: {sector}")

        risks = news.get("key_risks", [])
        if risks:
            lines.append("Risks: " + "; ".join(risks[:3]))

        opps = news.get("key_opportunities", [])
        if opps:
            lines.append("Opportunities: " + "; ".join(opps[:3]))

        freshness = news.get("data_freshness")
        if freshness:
            lines.append(f"Data freshness: {freshness}")

        return "\n".join(lines)
