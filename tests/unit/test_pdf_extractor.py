"""Unit tests for asx_scraper.pdf_extractor — pure-logic helpers, no network."""

from __future__ import annotations

import json
from unittest.mock import patch, MagicMock

import pytest

from asx_scraper.pdf_extractor import (
    EARNINGS_KEYWORDS,
    _parse_json_response,
    find_earnings_pdfs_markit,
    CDN_BASE,
    CDN_TOKEN,
)


class TestParseJsonResponse:
    def test_plain_json(self):
        assert _parse_json_response('{"a": 1}') == {"a": 1}

    def test_strips_markdown_fence_with_lang(self):
        raw = '```json\n{"a": 1, "b": "x"}\n```'
        assert _parse_json_response(raw) == {"a": 1, "b": "x"}

    def test_strips_markdown_fence_no_lang(self):
        raw = '```\n{"a": 1}\n```'
        assert _parse_json_response(raw) == {"a": 1}

    def test_extracts_outermost_json_with_prose(self):
        raw = 'Here is the result:\n{"ticker": "BHP", "eps": 123.4}\nThanks!'
        assert _parse_json_response(raw) == {"ticker": "BHP", "eps": 123.4}

    def test_nested_objects(self):
        raw = '{"a": {"b": {"c": 1}}, "d": [1, 2, 3]}'
        assert _parse_json_response(raw) == {"a": {"b": {"c": 1}}, "d": [1, 2, 3]}

    def test_leading_trailing_whitespace(self):
        assert _parse_json_response('   \n{"a": 1}\n   ') == {"a": 1}

    def test_invalid_json_raises(self):
        with pytest.raises(json.JSONDecodeError):
            _parse_json_response("not json at all")

    def test_truncated_json_raises(self):
        with pytest.raises(json.JSONDecodeError):
            _parse_json_response('{"a": 1')

    def test_realistic_pdf_extraction_payload(self):
        raw = '```json\n' + json.dumps({
            "ticker": "BHP",
            "period": "FY2025",
            "result_type": "FULL_YEAR",
            "revenue_aud_m": 65000.0,
            "npat_aud_m": 12000.0,
            "eps_basic_cents": 240.5,
            "consensus": {"eps_consensus_cents": 235.0, "source": "Bloomberg"},
            "management_quotes": [
                {"speaker": "CEO", "quote": "Strong year", "sentiment": "positive", "category": "outlook"},
            ],
            "data_confidence": "HIGH",
        }) + '\n```'
        out = _parse_json_response(raw)
        assert out["ticker"] == "BHP"
        assert out["consensus"]["source"] == "Bloomberg"
        assert len(out["management_quotes"]) == 1


class TestEarningsKeywords:
    @pytest.mark.parametrize("headline", [
        "Appendix 4D Half Year Report",
        "Appendix 4E and Annual Report",
        "Half Year Result and Investor Presentation",
        "Full Year Result Announcement",
        "Profit Announcement FY25",
        "Preliminary Final Report",
        "Results Announcement - 1H FY25",
    ])
    def test_matches_earnings_headlines(self, headline):
        assert any(kw.lower() in headline.lower() for kw in EARNINGS_KEYWORDS)

    @pytest.mark.parametrize("headline", [
        "Change of Director's Interest Notice",
        "Quarterly Activities Report",
        "Notice of Annual General Meeting",
        "Trading Halt",
        "Dividend Distribution",
    ])
    def test_skips_non_earnings_headlines(self, headline):
        assert not any(kw.lower() in headline.lower() for kw in EARNINGS_KEYWORDS)


class TestFindEarningsPdfsMarkit:
    def _mock_response(self, payload):
        """Build a context-manager mock for urlopen."""
        cm = MagicMock()
        cm.__enter__.return_value.read.return_value = json.dumps(payload).encode("utf-8")
        cm.__exit__.return_value = False
        return cm

    def test_filters_to_earnings_only_and_builds_cdn_url(self):
        payload = {
            "data": {
                "items": [
                    {
                        "headline": "Appendix 4D Half Year Report",
                        "documentKey": "doc-4d-key",
                        "date": "2025-02-15T08:30:00",
                    },
                    {
                        "headline": "Change of Director's Interest Notice",
                        "documentKey": "doc-skip",
                        "date": "2025-02-10T08:30:00",
                    },
                    {
                        "headline": "Full Year Result Announcement",
                        "documentKey": "doc-fy-key",
                        "date": "2024-08-20T08:30:00",
                    },
                ]
            }
        }

        with patch("urllib.request.urlopen", return_value=self._mock_response(payload)):
            results = find_earnings_pdfs_markit("bhp")

        assert len(results) == 2
        headlines = [r["headline"] for r in results]
        assert "Appendix 4D Half Year Report" in headlines
        assert "Full Year Result Announcement" in headlines
        assert all("Director" not in h for h in headlines)

        r0 = next(r for r in results if "4D" in r["headline"])
        assert r0["pdf_url"] == f"{CDN_BASE}/doc-4d-key?access_token={CDN_TOKEN}"
        assert r0["type"] == "4D"
        assert r0["date"] == "2025-02-15"

        r1 = next(r for r in results if "Full Year" in r["headline"])
        assert r1["type"] == "RESULTS"

    def test_4e_type_classification(self):
        payload = {
            "data": {
                "items": [
                    {
                        "headline": "Appendix 4E Preliminary Final Report",
                        "documentKey": "k",
                        "date": "2025-08-01T00:00:00",
                    }
                ]
            }
        }
        with patch("urllib.request.urlopen", return_value=self._mock_response(payload)):
            results = find_earnings_pdfs_markit("CBA")
        assert len(results) == 1
        assert results[0]["type"] == "4E"

    def test_skips_items_with_missing_document_key(self):
        payload = {
            "data": {
                "items": [
                    {"headline": "Appendix 4D", "documentKey": "", "date": "2025-01-01T00:00:00"},
                    {"headline": "Appendix 4E", "documentKey": "good", "date": "2025-02-01T00:00:00"},
                ]
            }
        }
        with patch("urllib.request.urlopen", return_value=self._mock_response(payload)):
            results = find_earnings_pdfs_markit("XYZ")
        assert len(results) == 1
        assert "good" in results[0]["pdf_url"]

    def test_returns_empty_list_on_network_error(self):
        with patch("urllib.request.urlopen", side_effect=OSError("network down")):
            results = find_earnings_pdfs_markit("BHP")
        assert results == []

    def test_returns_empty_when_no_items(self):
        with patch("urllib.request.urlopen", return_value=self._mock_response({"data": {"items": []}})):
            assert find_earnings_pdfs_markit("BHP") == []

    def test_ticker_uppercased(self):
        payload = {"data": {"items": []}}
        with patch("urllib.request.urlopen", return_value=self._mock_response(payload)) as mocked:
            find_earnings_pdfs_markit("bhp")
            call_url = mocked.call_args[0][0].full_url
            assert "/BHP/" in call_url
