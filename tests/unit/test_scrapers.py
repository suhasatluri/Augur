"""Unit tests for asx_scraper.sources — pure helpers + fixture-based HTML parsing.

Smoke-level coverage to catch regressions in:
- asic_short_interest signal thresholds
- marketindex._parse_val + get_financials/get_director_transactions HTML parsers
- director_trades.compute_director_signal thresholds
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from asx_scraper.sources.asic_short_interest import (
    get_short_interest,
    get_short_signal,
)
from asx_scraper.sources.director_trades import compute_director_signal
from asx_scraper.sources import marketindex as mi


# ============================================================
# asic_short_interest
# ============================================================

class TestAsicShortSignal:
    def test_high_band(self):
        sig, score = get_short_signal(10.0)
        assert sig == "HIGH"
        assert score < 0.5  # inverse — high shorting → low score

    def test_high_floor(self):
        # 8.0 / 20 = 0.4 → 0.5 - 0.4 = 0.1
        _, s = get_short_signal(8.0)
        assert s == pytest.approx(0.1, abs=1e-6)
        # Very high doesn't go below 0.05
        _, s = get_short_signal(50.0)
        assert s == 0.05

    def test_elevated_band(self):
        sig, score = get_short_signal(5.0)
        assert sig == "ELEVATED"
        assert 0.3 <= score < 0.5

    def test_normal_band(self):
        sig, score = get_short_signal(2.0)
        assert sig == "NORMAL"
        assert score == 0.5

    def test_low_band(self):
        sig, score = get_short_signal(0.5)
        assert sig == "LOW"
        assert score > 0.5
        assert score <= 0.65

    def test_zero_short_interest(self):
        sig, score = get_short_signal(0.0)
        assert sig == "LOW"
        assert score == 0.65

    def test_boundary_8_is_high(self):
        sig, _ = get_short_signal(8.0)
        assert sig == "HIGH"

    def test_boundary_4_is_elevated(self):
        sig, _ = get_short_signal(4.0)
        assert sig == "ELEVATED"

    def test_boundary_1_5_is_normal(self):
        sig, _ = get_short_signal(1.5)
        assert sig == "NORMAL"


class TestGetShortInterest:
    def test_returns_none_when_ticker_absent(self):
        assert get_short_interest("FAKE", data={}) is None

    def test_enriches_with_signal(self):
        fake = {"BHP": {
            "ticker": "BHP", "pct_shorted": 5.0,
            "short_positions": 100, "total_in_issue": 2000, "as_of_date": "20260401",
        }}
        out = get_short_interest("bhp", data=fake)
        assert out["ticker"] == "BHP"
        assert out["signal"] == "ELEVATED"
        assert "signal_score" in out
        assert out["pct_shorted"] == 5.0


# ============================================================
# marketindex._parse_val
# ============================================================

class TestParseVal:
    def test_plain_number(self):
        assert mi._parse_val("123.45") == 123.45

    def test_with_currency_symbol(self):
        assert mi._parse_val("$1,234.56") == 1234.56

    def test_negative_in_parens(self):
        assert mi._parse_val("(456.78)") == -456.78

    def test_dash_returns_none(self):
        assert mi._parse_val("-") is None

    def test_empty_returns_none(self):
        assert mi._parse_val("") is None

    def test_unparseable_returns_none(self):
        assert mi._parse_val("n/a") is None


# ============================================================
# marketindex.get_financials — fixture HTML
# ============================================================

_FINANCIALS_HTML = """
<html><body>
<table>
  <tr><th></th><th>06/2025</th><th>06/2024</th><th>06/2023</th></tr>
  <tr><td>Revenue ($M)</td><td>1,500</td><td>1,400</td><td>1,300</td></tr>
  <tr><td>NPAT ($M)</td><td>300</td><td>250</td><td>200</td></tr>
  <tr><td>EPS (¢)</td><td>50.5</td><td>42.0</td><td>35.0</td></tr>
  <tr><td>DPS (¢)</td><td>20</td><td>18</td><td>15</td></tr>
</table>
</body></html>
"""


class TestGetFinancials:
    def test_parses_year_headers_and_metrics(self):
        with patch.object(mi, "_get", return_value=_FINANCIALS_HTML):
            out = mi.get_financials("BHP")
        assert out["ticker"] == "BHP"
        assert out["years"] == ["06/2025", "06/2024", "06/2023"]
        assert out["npat"] == [300.0, 250.0, 200.0]
        assert out["revenue"] == [1500.0, 1400.0, 1300.0]
        assert out["eps_cents"] == [50.5, 42.0, 35.0]
        assert out["npat_m"] == 300.0
        assert out["npat_prior_m"] == 250.0
        assert out["revenue_m"] == 1500.0

    def test_beat_rate_when_growing(self):
        # NPAT 300 > 250 > 200 → 2/2 growth → beat_rate=1.0
        with patch.object(mi, "_get", return_value=_FINANCIALS_HTML):
            out = mi.get_financials("BHP")
        assert out["beat_rate"] == 1.0

    def test_returns_empty_when_fetch_fails(self):
        with patch.object(mi, "_get", return_value=None):
            assert mi.get_financials("BHP") == {}

    def test_returns_empty_when_no_year_table(self):
        html = "<html><body><table><tr><th>foo</th></tr></table></body></html>"
        with patch.object(mi, "_get", return_value=html):
            assert mi.get_financials("BHP") == {}


# ============================================================
# marketindex.get_director_transactions — fixture HTML
# ============================================================

# Build a date string ~30 days ago so transactions pass the 12-month cutoff
from datetime import datetime, timedelta as _td
_RECENT = (datetime.now() - _td(days=30)).strftime("%d/%m/%y")

_DIRECTOR_HTML = f"""
<html><body>
<table>
  <tr><th>Date</th><th>Director</th><th>Type</th><th>Amount</th><th>Price</th><th>Value</th><th>Notes</th></tr>
  <tr><td>{_RECENT}</td><td>Jane Doe</td><td>Buy</td><td>10000</td><td>50.00</td><td>500000</td><td>On-market</td></tr>
  <tr><td>{_RECENT}</td><td>John Smith</td><td>Buy</td><td>20000</td><td>50.00</td><td>1000000</td><td>On-market</td></tr>
  <tr><td>{_RECENT}</td><td>Bob Lee</td><td>Sell</td><td>2000</td><td>50.00</td><td>100000</td><td>On-market</td></tr>
  <tr><td>{_RECENT}</td><td>Excluded</td><td>Issued</td><td>5000</td><td>0</td><td>0</td><td>Options grant</td></tr>
</table>
</body></html>
"""


class TestGetDirectorTransactions:
    def test_parses_buys_and_sells(self):
        with patch.object(mi, "_get", return_value=_DIRECTOR_HTML):
            out = mi.get_director_transactions("BHP")
        assert out["buy_count"] == 2
        assert out["sell_count"] == 1
        # Net = 1.5M buys - 100k sells = 1.4M → STRONG_BUY (>1M)
        assert out["signal"] == "STRONG_BUY"
        assert out["signal_score"] == 0.75
        assert out["net_buy_value"] == 1_400_000.0

    def test_excludes_issued_grants(self):
        with patch.object(mi, "_get", return_value=_DIRECTOR_HTML):
            out = mi.get_director_transactions("BHP")
        # The "Issued" row is parsed but excluded from on-market counts
        assert out["buy_count"] == 2  # not 3
        # All transactions still appear in raw list
        assert len(out["transactions"]) == 4

    def test_default_when_fetch_fails(self):
        with patch.object(mi, "_get", return_value=None):
            out = mi.get_director_transactions("BHP")
        assert out["signal"] == "NEUTRAL"
        assert out["buy_count"] == 0

    def test_default_when_no_table(self):
        with patch.object(mi, "_get", return_value="<html><body>nothing</body></html>"):
            out = mi.get_director_transactions("BHP")
        assert out["signal"] == "NEUTRAL"


# ============================================================
# director_trades.compute_director_signal
# ============================================================

class TestComputeDirectorSignal:
    def test_strong_buy_above_1m(self):
        trades = [{"type": "Buy", "value": 1_500_000}]
        out = compute_director_signal(trades)
        assert out["signal"] == "STRONG_BUY"
        assert out["signal_score"] == 0.75
        assert out["buy_count"] == 1
        assert out["sell_count"] == 0

    def test_buy_band(self):
        trades = [{"type": "Buy", "value": 500_000}]
        out = compute_director_signal(trades)
        assert out["signal"] == "BUY"
        assert out["signal_score"] == 0.65

    def test_neutral_band(self):
        trades = [{"type": "Buy", "value": 50_000}]
        assert compute_director_signal(trades)["signal"] == "NEUTRAL"

    def test_sell_band(self):
        trades = [{"type": "Sell", "value": 500_000}]
        out = compute_director_signal(trades)
        assert out["signal"] == "SELL"
        assert out["net_buy_value"] == -500_000.0

    def test_strong_sell_band(self):
        trades = [{"type": "Sell", "value": 2_000_000}]
        assert compute_director_signal(trades)["signal"] == "STRONG_SELL"

    def test_buys_offset_sells(self):
        trades = [
            {"type": "Buy", "value": 600_000},
            {"type": "Sell", "value": 400_000},
        ]
        out = compute_director_signal(trades)
        # Net = 200k → BUY band (>100k)
        assert out["signal"] == "BUY"
        assert out["net_buy_value"] == 200_000.0

    def test_handles_none_values(self):
        trades = [{"type": "Buy", "value": None}, {"type": "Sell", "value": None}]
        out = compute_director_signal(trades)
        assert out["net_buy_value"] == 0.0
        assert out["signal"] == "NEUTRAL"

    def test_empty_trades(self):
        out = compute_director_signal([])
        assert out["signal"] == "NEUTRAL"
        assert out["total_trades"] == 0
        assert out["buy_count"] == 0
        assert out["sell_count"] == 0
