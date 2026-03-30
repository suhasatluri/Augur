"""Data models for prediction synthesis."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field

DISCLAIMER = (
    "This is not financial advice. Research tool only. "
    "Augur does not hold an AFSL."
)


class ProbabilityDistribution(BaseModel):
    """Final probability breakdown across the agent swarm."""

    p_beat: float = Field(ge=0.0, le=1.0)
    p_miss: float = Field(ge=0.0, le=1.0)
    p_inline: float = Field(ge=0.0, le=1.0)
    mean_probability: float
    median_probability: float
    std_dev: float
    confidence_band_low: float
    confidence_band_high: float


class SwingFactor(BaseModel):
    """A key variable driving disagreement between bull and bear agents."""

    theme: str
    description: str
    bull_view: str
    bear_view: str
    mentions: int = 0
    disagreement_score: float = Field(ge=0.0, le=1.0)


class SentimentCascade(BaseModel):
    """Assessment of likely market reaction severity."""

    direction: str  # "miss_cascade", "beat_cascade", "muted"
    severity: str   # "severe", "moderate", "mild"
    retail_conviction: float
    retail_mean_probability: float
    reasoning: str


class PredictionReport(BaseModel):
    """Complete prediction output — JSON + human summary."""

    simulation_id: str
    ticker: str
    verdict: str  # "LIKELY BEAT", "LEAN BEAT", "TOSS-UP", "LEAN MISS", "LIKELY MISS"
    distribution: ProbabilityDistribution
    swing_factors: list[SwingFactor]
    sentiment_cascade: SentimentCascade
    convergence_score: float
    high_uncertainty: bool
    human_summary: str
    generated_at: datetime = Field(default_factory=datetime.utcnow)
    disclaimer: str = DISCLAIMER
