"""Quality scoring, earnings history, and consensus extraction for harvest output."""

from __future__ import annotations

from collections import Counter

from seed_harvester.models import HarvestResponse, QualityReport, Seed, SeedType

# All categories we expect a complete harvest to cover
EXPECTED_CATEGORIES = {s.value for s in SeedType}

# Minimum seeds per category to avoid "thin coverage" warning
MIN_PER_CATEGORY = 1

# Confidence thresholds
LOW_CONFIDENCE_THRESHOLD = 0.3
HIGH_CONFIDENCE_THRESHOLD = 0.8


def score_harvest(response: HarvestResponse) -> QualityReport:
    """Score a harvest run on coverage, confidence calibration, and content quality."""

    seeds = response.seeds
    warnings: list[str] = []

    if not seeds:
        return QualityReport(
            overall_score=0.0,
            warnings=["No seeds harvested — API call may have failed"],
        )

    # --- Category coverage ---
    category_counts = Counter(s.seed_type.value for s in seeds)
    missing = EXPECTED_CATEGORIES - set(category_counts.keys())
    if missing:
        warnings.append(f"Missing categories: {', '.join(sorted(missing))}")

    coverage_score = len(set(category_counts.keys()) & EXPECTED_CATEGORIES) / len(EXPECTED_CATEGORIES)

    # --- Confidence calibration ---
    confidences = [s.confidence for s in seeds]
    avg_conf = sum(confidences) / len(confidences)

    all_high = all(c >= HIGH_CONFIDENCE_THRESHOLD for c in confidences)
    if all_high:
        warnings.append("All confidence scores >= 0.8 — possible miscalibration")

    all_low = all(c <= LOW_CONFIDENCE_THRESHOLD for c in confidences)
    if all_low:
        warnings.append("All confidence scores <= 0.3 — model may be excessively hedging")

    spread = max(confidences) - min(confidences)
    if spread < 0.15 and len(seeds) > 3:
        warnings.append(f"Confidence spread is only {spread:.2f} — scores are clustered, poor differentiation")

    # Reward spread in [0.3, 0.6] range — indicates good calibration
    calibration_score = min(spread / 0.4, 1.0)

    # --- Source quality ---
    general_only = sum(1 for s in seeds if s.source.lower() in ("general knowledge", ""))
    if general_only == len(seeds):
        warnings.append("All seeds sourced from 'general knowledge' — no specific data grounding")
    source_score = 1.0 - (general_only / len(seeds))

    # --- Content quality checks ---
    for s in seeds:
        if len(s.content) < 20:
            warnings.append(f"Very short seed content: '{s.content[:40]}...'")
        if len(s.content) > 500:
            warnings.append(f"Overly verbose seed ({len(s.content)} chars): '{s.content[:40]}...'")

    # --- Earnings history & consensus detection ---
    has_earnings_history = _detect_earnings_history(seeds)
    has_consensus = _detect_consensus(seeds)

    if not has_earnings_history:
        warnings.append("No earnings history context — seeds lack historical comparison baseline")
    if not has_consensus:
        warnings.append("No analyst consensus data — predictions lack market expectations anchor")

    # --- Overall score (weighted) ---
    overall = (
        coverage_score * 0.30
        + calibration_score * 0.25
        + source_score * 0.20
        + (0.10 if has_earnings_history else 0.0)
        + (0.10 if has_consensus else 0.0)
        + (0.05 if len(warnings) <= 2 else 0.0)
    )

    return QualityReport(
        overall_score=round(min(overall, 1.0), 2),
        category_coverage=dict(category_counts),
        avg_confidence=round(avg_conf, 3),
        warnings=warnings,
        has_earnings_history=has_earnings_history,
        has_consensus=has_consensus,
    )


def _detect_earnings_history(seeds: list[Seed]) -> bool:
    """Check if any seeds reference historical earnings data."""
    history_keywords = [
        "previous", "prior", "last quarter", "last half", "H1", "H2",
        "FY2024", "FY2025", "FY24", "FY25", "historical", "year-over-year",
        "yoy", "compared to", "beat", "miss", "surprise",
    ]
    for s in seeds:
        text = (s.content + " " + s.reasoning).lower()
        if any(kw.lower() in text for kw in history_keywords):
            return True
    return False


def _detect_consensus(seeds: list[Seed]) -> bool:
    """Check if any seeds reference analyst consensus or estimates."""
    consensus_keywords = [
        "consensus", "analyst", "estimate", "broker", "sell-side",
        "buy-side", "target", "forecast", "EPS", "EBITDA",
        "downgrade", "upgrade", "overweight", "underweight",
    ]
    for s in seeds:
        text = (s.content + " " + s.reasoning).lower()
        if any(kw.lower() in text for kw in consensus_keywords):
            return True
    return False
