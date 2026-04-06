"""Unit tests for seed_harvester.perplexity_harvester — pure helpers + mocked HTTP."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from seed_harvester import perplexity_harvester as ph
from seed_harvester.perplexity_harvester import (
    PerplexityHarvester,
    _parse_json,
    get_session_usage,
    reset_session_usage,
)


# ---------- _parse_json ----------

class TestParseJson:
    def test_plain(self):
        assert _parse_json('{"a": 1}') == {"a": 1}

    def test_markdown_fence_with_lang(self):
        assert _parse_json('```json\n{"a": 1}\n```') == {"a": 1}

    def test_markdown_fence_no_lang(self):
        assert _parse_json('```\n{"a": 1}\n```') == {"a": 1}

    def test_prose_around_json(self):
        assert _parse_json('Here:\n{"a": 1, "b": 2}\nthx') == {"a": 1, "b": 2}

    def test_invalid_raises(self):
        with pytest.raises(json.JSONDecodeError):
            _parse_json("totally not json")


# ---------- session accumulator ----------

class TestSessionUsage:
    def setup_method(self):
        reset_session_usage()

    def test_starts_zeroed(self):
        u = get_session_usage()
        assert u == {"requests": 0, "prompt_tokens": 0, "completion_tokens": 0, "cost_usd": 0.0}

    def test_get_returns_copy(self):
        u = get_session_usage()
        u["requests"] = 999
        # Original unchanged
        assert get_session_usage()["requests"] == 0

    def test_reset_clears_state(self):
        ph._session_usage["requests"] = 5
        ph._session_usage["cost_usd"] = 1.23
        reset_session_usage()
        assert get_session_usage()["requests"] == 0
        assert get_session_usage()["cost_usd"] == 0.0


# ---------- get_financial_news ----------

def _build_response(content: str, prompt_tokens=100, completion_tokens=200, citations=None):
    """Build a fake requests.Response for Perplexity."""
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    resp.json.return_value = {
        "model": "sonar",
        "choices": [{"message": {"content": content}}],
        "usage": {"prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens},
        "citations": citations or [],
    }
    return resp


GOOD_PAYLOAD = json.dumps({
    "analyst_sentiment": "bullish",
    "recent_estimate_revisions": "UP",
    "revision_details": "Macquarie raised PT",
    "material_news": ["Q1 beat"],
    "sector_conditions": "favourable",
    "key_risks": ["fx"],
    "key_opportunities": ["expansion"],
    "management_tone": "confident",
    "data_freshness": "last week",
    "sources_cited": ["https://example.com/a"],
})


class TestGetFinancialNews:
    def setup_method(self):
        reset_session_usage()
        self.h = PerplexityHarvester()

    @pytest.mark.asyncio
    async def test_no_api_key_returns_empty(self, monkeypatch):
        monkeypatch.delenv("PERPLEXITY_API_KEY", raising=False)
        h = PerplexityHarvester()
        result = await h.get_financial_news("BHP")
        assert result == {}
        # No accumulator change
        assert get_session_usage()["requests"] == 0

    @pytest.mark.asyncio
    async def test_happy_path_parses_and_enriches(self, monkeypatch):
        monkeypatch.setenv("PERPLEXITY_API_KEY", "fake-key")
        with patch("requests.post", return_value=_build_response(GOOD_PAYLOAD)):
            result = await self.h.get_financial_news("bhp", reporting_date="2026-08-15")

        assert result["analyst_sentiment"] == "bullish"
        assert result["_ticker"] == "BHP"
        assert result["_model"] == "sonar"
        # Cost = 0.005 + 100/1M + 200/1M = 0.0053
        assert result["_cost"] == pytest.approx(0.0053, abs=1e-6)
        assert result["_usage"]["prompt_tokens"] == 100
        assert result["_usage"]["completion_tokens"] == 200

    @pytest.mark.asyncio
    async def test_happy_path_updates_session_accumulator(self, monkeypatch):
        monkeypatch.setenv("PERPLEXITY_API_KEY", "fake-key")
        with patch("requests.post", return_value=_build_response(GOOD_PAYLOAD)):
            await self.h.get_financial_news("BHP")

        u = get_session_usage()
        assert u["requests"] == 1
        assert u["prompt_tokens"] == 100
        assert u["completion_tokens"] == 200
        assert u["cost_usd"] == pytest.approx(0.0053, abs=1e-6)

    @pytest.mark.asyncio
    async def test_multiple_calls_accumulate(self, monkeypatch):
        monkeypatch.setenv("PERPLEXITY_API_KEY", "fake-key")
        with patch("requests.post", return_value=_build_response(GOOD_PAYLOAD, 50, 50)):
            await self.h.get_financial_news("BHP")
            await self.h.get_financial_news("CBA")
            await self.h.get_financial_news("CSL")

        u = get_session_usage()
        assert u["requests"] == 3
        assert u["prompt_tokens"] == 150
        assert u["completion_tokens"] == 150
        # 3 * (0.005 + 100/1M)
        assert u["cost_usd"] == pytest.approx(3 * 0.0051, abs=1e-6)

    @pytest.mark.asyncio
    async def test_cost_math_includes_token_component(self, monkeypatch):
        monkeypatch.setenv("PERPLEXITY_API_KEY", "fake-key")
        with patch(
            "requests.post",
            return_value=_build_response(GOOD_PAYLOAD, 1_000_000, 1_000_000),
        ):
            result = await self.h.get_financial_news("BHP")
        # 0.005 + 1.00 + 1.00 = 2.005
        assert result["_cost"] == pytest.approx(2.005, abs=1e-6)

    @pytest.mark.asyncio
    async def test_citations_merged_when_response_lacks_sources_cited(self, monkeypatch):
        monkeypatch.setenv("PERPLEXITY_API_KEY", "fake-key")
        payload = json.dumps({"analyst_sentiment": "neutral"})  # no sources_cited key
        resp = _build_response(payload, citations=["https://x.com", "https://y.com"])
        with patch("requests.post", return_value=resp):
            result = await self.h.get_financial_news("BHP")
        assert result["sources_cited"] == ["https://x.com", "https://y.com"]

    @pytest.mark.asyncio
    async def test_citations_unioned_with_existing_sources(self, monkeypatch):
        monkeypatch.setenv("PERPLEXITY_API_KEY", "fake-key")
        payload = json.dumps({"sources_cited": ["https://a.com"]})
        resp = _build_response(payload, citations=["https://a.com", "https://b.com"])
        with patch("requests.post", return_value=resp):
            result = await self.h.get_financial_news("BHP")
        assert set(result["sources_cited"]) == {"https://a.com", "https://b.com"}

    @pytest.mark.asyncio
    async def test_http_error_returns_empty_and_no_accumulation(self, monkeypatch):
        monkeypatch.setenv("PERPLEXITY_API_KEY", "fake-key")
        resp = MagicMock()
        resp.raise_for_status.side_effect = RuntimeError("HTTP 500")
        with patch("requests.post", return_value=resp):
            result = await self.h.get_financial_news("BHP")
        assert result == {}
        assert get_session_usage()["requests"] == 0

    @pytest.mark.asyncio
    async def test_network_error_returns_empty(self, monkeypatch):
        monkeypatch.setenv("PERPLEXITY_API_KEY", "fake-key")
        with patch("requests.post", side_effect=ConnectionError("dns fail")):
            result = await self.h.get_financial_news("BHP")
        assert result == {}
        assert get_session_usage()["requests"] == 0

    @pytest.mark.asyncio
    async def test_malformed_json_content_returns_empty(self, monkeypatch):
        monkeypatch.setenv("PERPLEXITY_API_KEY", "fake-key")
        with patch("requests.post", return_value=_build_response("not json at all")):
            result = await self.h.get_financial_news("BHP")
        assert result == {}
        # Parse failure happens before accumulator update — no cost charged
        assert get_session_usage()["requests"] == 0


# ---------- to_seed_context ----------

class TestToSeedContext:
    def setup_method(self):
        self.h = PerplexityHarvester()

    def test_empty_dict_returns_empty_string(self):
        assert self.h.to_seed_context({}) == ""

    def test_minimal_news(self):
        out = self.h.to_seed_context({"analyst_sentiment": "bullish"})
        assert "PERPLEXITY REAL-TIME NEWS" in out
        assert "Analyst sentiment: bullish" in out
        assert "Estimate revisions: UNKNOWN" in out

    def test_full_news_includes_all_sections(self):
        news = {
            "analyst_sentiment": "bearish",
            "recent_estimate_revisions": "DOWN",
            "revision_details": "Cut by Goldman",
            "management_tone": "cautious",
            "material_news": ["a", "b", "c"],
            "sector_conditions": "weak",
            "key_risks": ["r1", "r2"],
            "key_opportunities": ["o1"],
            "data_freshness": "today",
        }
        out = self.h.to_seed_context(news)
        assert "bearish" in out
        assert "DOWN" in out
        assert "Cut by Goldman" in out
        assert "cautious" in out
        assert "- a" in out and "- b" in out and "- c" in out
        assert "Sector conditions: weak" in out
        assert "Risks: r1; r2" in out
        assert "Opportunities: o1" in out
        assert "Data freshness: today" in out

    def test_truncates_material_news_to_5(self):
        news = {"material_news": [f"item{i}" for i in range(10)]}
        out = self.h.to_seed_context(news)
        assert "item4" in out
        assert "item5" not in out

    def test_truncates_risks_to_3(self):
        news = {"key_risks": ["r1", "r2", "r3", "r4", "r5"]}
        out = self.h.to_seed_context(news)
        assert "r3" in out
        assert "r4" not in out
