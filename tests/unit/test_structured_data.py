from seed_harvester.structured_data import StructuredDataFetcher


class TestBiasScore:
    def setup_method(self):
        self.fetcher = StructuredDataFetcher()

    def _make_data(self, rec_mean=None, target=None, current=None,
                   earnings_growth=None, beat_rate=None):
        """Build the nested dict structure compute_ticker_bias_score expects."""
        yf = {}
        if rec_mean is not None:
            yf["recommendationMean"] = rec_mean
        if target is not None:
            yf["targetMeanPrice"] = target
        if current is not None:
            yf["currentPrice"] = current
        if earnings_growth is not None:
            yf["earningsGrowth"] = earnings_growth
        sa = {}
        if beat_rate is not None:
            sa["beat_rate"] = beat_rate
        return {"source_yfinance": yf, "source_stockanalysis": sa}

    def test_neutral_inputs_returns_near_half(self):
        """All neutral inputs should return ~0.50"""
        data = self._make_data(rec_mean=3.0, target=100, current=100,
                               earnings_growth=0.0, beat_rate=0.5)
        score, _ = self.fetcher.compute_ticker_bias_score(data)
        assert 0.45 <= score <= 0.55

    def test_bullish_inputs_above_half(self):
        """Strong buy + 50% upside + 30% growth + high beat rate = bullish"""
        data = self._make_data(rec_mean=1.0, target=150, current=100,
                               earnings_growth=0.30, beat_rate=0.9)
        score, _ = self.fetcher.compute_ticker_bias_score(data)
        assert score > 0.65

    def test_bearish_inputs_below_half(self):
        """Strong sell + negative upside + negative growth + low beat rate"""
        data = self._make_data(rec_mean=5.0, target=75, current=100,
                               earnings_growth=-0.30, beat_rate=0.1)
        score, _ = self.fetcher.compute_ticker_bias_score(data)
        assert score < 0.35

    def test_score_never_exceeds_ceiling(self):
        """Score must never exceed 0.80"""
        data = self._make_data(rec_mean=1.0, target=300, current=100,
                               earnings_growth=5.0, beat_rate=1.0)
        score, _ = self.fetcher.compute_ticker_bias_score(data)
        assert score <= 0.80

    def test_score_never_below_floor(self):
        """Score must never go below 0.20"""
        data = self._make_data(rec_mean=5.0, target=10, current=100,
                               earnings_growth=-5.0, beat_rate=0.0)
        score, _ = self.fetcher.compute_ticker_bias_score(data)
        assert score >= 0.20

    def test_missing_data_returns_neutral(self):
        """Empty data should fall back to ~0.50"""
        data = {"source_yfinance": {}, "source_stockanalysis": {}}
        score, _ = self.fetcher.compute_ticker_bias_score(data)
        assert 0.45 <= score <= 0.55
