"""Unit tests for asx_scraper.metrics_computer pure-logic helpers."""

from __future__ import annotations

import pytest

from asx_scraper.metrics_computer import (
    calc_beat_rate,
    compute_mgmt_credibility,
    data_confidence_tier,
    is_suspect_npat,
)


def _row(beat_miss=None, data_source=None, npat_aud_m=None, surprise_pct=None):
    return {
        "beat_miss": beat_miss,
        "data_source": data_source,
        "npat_aud_m": npat_aud_m,
        "surprise_pct": surprise_pct,
    }


class TestCalcBeatRate:
    def test_all_beats(self):
        rows = [_row(beat_miss="BEAT") for _ in range(4)]
        assert calc_beat_rate(rows) == 1.0

    def test_all_misses(self):
        rows = [_row(beat_miss="MISS") for _ in range(4)]
        assert calc_beat_rate(rows) == 0.0

    def test_mixed(self):
        rows = [
            _row(beat_miss="BEAT"),
            _row(beat_miss="BEAT"),
            _row(beat_miss="MISS"),
            _row(beat_miss="INLINE"),
        ]
        assert calc_beat_rate(rows) == 0.5

    def test_inline_counts_as_known_but_not_beat(self):
        rows = [_row(beat_miss="INLINE"), _row(beat_miss="INLINE")]
        assert calc_beat_rate(rows) == 0.0

    def test_unknown_outcomes_excluded(self):
        rows = [
            _row(beat_miss="BEAT"),
            _row(beat_miss=None),
            _row(beat_miss="UNKNOWN"),
            _row(beat_miss="MISS"),
        ]
        # Only BEAT and MISS are "known" → 1 of 2
        assert calc_beat_rate(rows) == 0.5

    def test_empty_list(self):
        assert calc_beat_rate([]) is None

    def test_no_known_outcomes_returns_none(self):
        rows = [_row(beat_miss=None), _row(beat_miss="UNKNOWN")]
        assert calc_beat_rate(rows) is None


class TestComputeMgmtCredibility:
    def test_perfect_record(self):
        # 1.0*0.5 + 1.0*0.3 + 0.5*0.2 = 0.9
        assert compute_mgmt_credibility(1.0, 1.0, None) == 0.9

    def test_zero_record(self):
        # 0 + 0 + 0.5*0.2 = 0.1
        assert compute_mgmt_credibility(0.0, 0.0, None) == 0.1

    def test_explicit_gdr(self):
        # 0.5*0.5 + 0.5*0.3 + 1.0*0.2 = 0.6
        assert compute_mgmt_credibility(0.5, 0.5, 1.0) == 0.6

    def test_returns_none_if_4q_missing(self):
        assert compute_mgmt_credibility(None, 0.5, 0.5) is None

    def test_returns_none_if_8q_missing(self):
        assert compute_mgmt_credibility(0.5, None, 0.5) is None

    def test_rounded_to_3dp(self):
        out = compute_mgmt_credibility(1/3, 1/3, None)
        # 0.333..*0.5 + 0.333..*0.3 + 0.1 = 0.3666... → 0.367
        assert out == 0.367


class TestDataConfidenceTier:
    def test_high_when_six_pdf_rows(self):
        assert data_confidence_tier(pdf_count=6, total_rows=6) == "HIGH"

    def test_high_when_more_than_six_pdfs(self):
        assert data_confidence_tier(pdf_count=10, total_rows=10) == "HIGH"

    def test_med_when_four_total_few_pdfs(self):
        assert data_confidence_tier(pdf_count=2, total_rows=4) == "MED"

    def test_med_boundary(self):
        assert data_confidence_tier(pdf_count=0, total_rows=4) == "MED"

    def test_low_when_few_rows(self):
        assert data_confidence_tier(pdf_count=0, total_rows=3) == "LOW"

    def test_low_when_no_data(self):
        assert data_confidence_tier(pdf_count=0, total_rows=0) == "LOW"

    def test_high_overrides_low_total(self):
        # 6 PDF rows still = HIGH even though total=6
        assert data_confidence_tier(pdf_count=6, total_rows=6) == "HIGH"


class TestIsSuspectNpat:
    def test_normal_npat_not_suspect(self):
        # PDF says 1000M, MI says 1100M → ratio 0.91, not suspect
        assert is_suspect_npat(1000.0, 1100.0) is False

    def test_unit_error_flagged(self):
        # PDF parsed in billions instead of millions: 1.0 vs MI 1100
        assert is_suspect_npat(1.0, 1100.0) is True

    def test_boundary_just_under_10pct(self):
        # 99 / 1000 = 0.099 → suspect
        assert is_suspect_npat(99.0, 1000.0) is True

    def test_boundary_just_over_10pct(self):
        # 100 / 1000 = 0.10 → not suspect
        assert is_suspect_npat(100.0, 1000.0) is False

    def test_none_npat(self):
        assert is_suspect_npat(None, 1000.0) is False

    def test_none_mi(self):
        assert is_suspect_npat(500.0, None) is False

    def test_zero_mi(self):
        assert is_suspect_npat(500.0, 0) is False

    def test_negative_mi(self):
        # Loss-making reference — skip the check
        assert is_suspect_npat(500.0, -100.0) is False

    def test_negative_npat_uses_abs(self):
        # PDF reports a loss; absolute ratio still computed
        assert is_suspect_npat(-1.0, 1000.0) is True
