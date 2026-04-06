"""Unit tests for earnings_calendar_harvester pure-logic helpers:
_parse_date and merge_sources.
"""

from __future__ import annotations

from datetime import date

import pytest

from scripts.earnings_calendar_harvester import _parse_date, merge_sources


class TestParseDate:
    def test_iso_format(self):
        assert _parse_date("2026-08-15") == date(2026, 8, 15)

    def test_dd_mm_yyyy(self):
        assert _parse_date("15/08/2026") == date(2026, 8, 15)

    def test_dd_mb_yyyy(self):
        assert _parse_date("15 Aug 2026") == date(2026, 8, 15)

    def test_long_month_format(self):
        assert _parse_date("August 15, 2026") == date(2026, 8, 15)

    def test_dd_mm_yyyy_dash(self):
        assert _parse_date("15-08-2026") == date(2026, 8, 15)

    def test_iso_embedded_in_prose(self):
        # Falls through to regex extractor
        assert _parse_date("Reporting on 2026-08-15 (provisional)") == date(2026, 8, 15)

    def test_strips_whitespace(self):
        assert _parse_date("  2026-08-15  ") == date(2026, 8, 15)

    def test_empty_returns_none(self):
        assert _parse_date("") is None

    def test_literal_null_returns_none(self):
        assert _parse_date("null") is None

    def test_unparseable_returns_none(self):
        assert _parse_date("sometime next year") is None

    def test_invalid_iso_in_prose_returns_none(self):
        # Regex finds "2026-13-99" but strptime rejects it
        assert _parse_date("date is 2026-13-99") is None


class TestMergeSources:
    YF_DATE = date(2026, 8, 20)
    YF_TYPE = "FY2026"

    def _yf(self, d=None, t=None):
        return (d or self.YF_DATE, t or self.YF_TYPE)

    def _px(self, d, t="FY2026", conf="high", raw="raw text"):
        return (d, t, conf, raw)

    # ---- both None ----
    def test_both_none_returns_none(self):
        assert merge_sources(None, None) is None

    # ---- both present, agree ----
    def test_both_agree_same_date(self):
        out = merge_sources(self._yf(), self._px(self.YF_DATE))
        assert out["final_date"] == self.YF_DATE
        assert out["final_confidence"] == "high"
        assert out["final_source"] == "yfinance+perplexity"
        assert out["bucket"] == "both_agree"
        assert out["raw_text"] == "raw text"

    def test_both_agree_within_threshold(self):
        # 5 days apart → still agree
        out = merge_sources(
            self._yf(date(2026, 8, 20)),
            self._px(date(2026, 8, 25)),
        )
        assert out["bucket"] == "both_agree"
        # Prefers Perplexity's exact date
        assert out["final_date"] == date(2026, 8, 25)
        assert out["final_confidence"] == "high"

    def test_both_agree_at_boundary_7_days(self):
        out = merge_sources(
            self._yf(date(2026, 8, 20)),
            self._px(date(2026, 8, 27)),
        )
        assert out["bucket"] == "both_agree"

    def test_both_agree_yfinance_after_perplexity(self):
        # Order independence — abs delta
        out = merge_sources(
            self._yf(date(2026, 8, 27)),
            self._px(date(2026, 8, 20)),
        )
        assert out["bucket"] == "both_agree"
        assert out["final_date"] == date(2026, 8, 20)

    def test_agree_uses_yfinance_type_when_perplexity_type_missing(self):
        out = merge_sources(
            self._yf(self.YF_DATE, "FY2026"),
            self._px(self.YF_DATE, t=None),
        )
        assert out["final_type"] == "FY2026"

    def test_agree_prefers_perplexity_type_when_present(self):
        out = merge_sources(
            self._yf(self.YF_DATE, "FY2026"),
            self._px(self.YF_DATE, t="H1 FY2026"),
        )
        assert out["final_type"] == "H1 FY2026"

    # ---- both present, disagree ----
    def test_disagree_falls_back_to_yfinance(self):
        out = merge_sources(
            self._yf(date(2026, 8, 20)),
            self._px(date(2026, 9, 1)),  # 12 days apart
        )
        assert out["bucket"] == "yfinance"
        assert out["final_date"] == date(2026, 8, 20)
        assert out["final_source"] == "yfinance"
        assert out["final_confidence"] == "medium"
        assert out["raw_text"] is None

    def test_disagree_just_over_threshold(self):
        out = merge_sources(
            self._yf(date(2026, 8, 20)),
            self._px(date(2026, 8, 28)),  # 8 days
        )
        assert out["bucket"] == "yfinance"

    # ---- yfinance only ----
    def test_yfinance_only(self):
        out = merge_sources(self._yf(), None)
        assert out["bucket"] == "yfinance"
        assert out["final_source"] == "yfinance"
        assert out["final_confidence"] == "medium"
        assert out["final_date"] == self.YF_DATE
        assert out["raw_text"] is None

    # ---- perplexity only ----
    def test_perplexity_only_high_confidence_capped_to_medium(self):
        out = merge_sources(None, self._px(date(2026, 8, 20), conf="high"))
        assert out["bucket"] == "perplexity"
        assert out["final_confidence"] == "medium"
        assert out["final_source"] == "perplexity"
        assert out["raw_text"] == "raw text"

    def test_perplexity_only_medium_passes_through(self):
        out = merge_sources(None, self._px(date(2026, 8, 20), conf="medium"))
        assert out["final_confidence"] == "medium"

    def test_perplexity_only_low_passes_through(self):
        out = merge_sources(None, self._px(date(2026, 8, 20), conf="low"))
        assert out["final_confidence"] == "low"

    # ---- custom threshold ----
    def test_custom_agree_threshold(self):
        # 10 days apart, default threshold (7) → disagree
        d_yf = date(2026, 8, 20)
        d_px = date(2026, 8, 30)
        assert merge_sources(self._yf(d_yf), self._px(d_px))["bucket"] == "yfinance"
        # Same dates with threshold=14 → agree
        out = merge_sources(self._yf(d_yf), self._px(d_px), agree_threshold_days=14)
        assert out["bucket"] == "both_agree"
