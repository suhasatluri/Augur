from seed_harvester.quality import score_harvest
from seed_harvester.models import HarvestResponse, Seed, SeedType


def _make_seed(seed_type=SeedType.FINANCIAL, content="Test content here",
               confidence=0.5, source="test"):
    return Seed(
        ticker="TEST",
        seed_type=seed_type,
        content=content,
        confidence=confidence,
        source=source,
    )


class TestSeedQuality:

    def test_empty_harvest_low_score(self):
        """Empty harvest should score 0"""
        response = HarvestResponse(ticker="TEST", seeds=[])
        quality = score_harvest(response)
        assert quality.overall_score == 0.0
        assert len(quality.warnings) > 0

    def test_single_category_has_warnings(self):
        """Only one seed type should warn about missing categories"""
        response = HarvestResponse(
            ticker="TEST",
            seeds=[_make_seed(SeedType.FINANCIAL)],
        )
        quality = score_harvest(response)
        assert any("Missing categories" in w for w in quality.warnings)

    def test_full_coverage_higher_score(self):
        """Seeds covering all categories score higher"""
        seeds = [
            _make_seed(SeedType.FINANCIAL, confidence=0.8, source="yfinance",
                       content="Previous FY2024 results showed strong growth"),
            _make_seed(SeedType.SENTIMENT, confidence=0.6, source="news",
                       content="Analyst consensus estimates EPS at $1.20"),
            _make_seed(SeedType.GUIDANCE, confidence=0.5, source="company"),
            _make_seed(SeedType.MACRO, confidence=0.4, source="rba"),
            _make_seed(SeedType.SECTOR, confidence=0.3, source="peers"),
        ]
        response = HarvestResponse(ticker="TEST", seeds=seeds)
        quality = score_harvest(response)
        assert quality.overall_score > 0.5

    def test_warnings_on_no_seeds(self):
        """No seeds should generate warnings"""
        response = HarvestResponse(ticker="TEST", seeds=[])
        quality = score_harvest(response)
        assert len(quality.warnings) > 0
