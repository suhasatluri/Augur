"""Data models for negotiation runner."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class AgentState(BaseModel):
    """In-memory state of an agent during negotiation."""

    id: str
    simulation_id: str
    archetype: str
    name: str
    goals: str
    methodology: str
    known_biases: str
    conviction_threshold: float
    risk_tolerance: float
    initial_probability: float
    current_probability: float
    conviction: float
    round_history: list[dict] = Field(default_factory=list)


class RoundResult(BaseModel):
    """A single agent's output for one round."""

    agent_id: str
    round_number: int
    probability: float = Field(ge=0.0, le=1.0)
    reasoning: str
    conviction_delta: float = Field(ge=-1.0, le=1.0)


class RoundSummary(BaseModel):
    """Aggregate statistics for a completed round."""

    round_number: int
    mean_probability: float
    median_probability: float
    std_dev: float
    min_probability: float
    max_probability: float
    bull_count: int = 0      # P > 0.6
    bear_count: int = 0      # P < 0.4
    neutral_count: int = 0   # 0.4 <= P <= 0.6
    biggest_mover: str = ""
    biggest_move_delta: float = 0.0
    narrative: str = ""      # Haiku-generated round narrative


class SimulationResult(BaseModel):
    """Final output of a complete negotiation."""

    simulation_id: str
    ticker: str
    rounds_completed: int
    final_mean_probability: float
    final_median_probability: float
    final_std_dev: float
    convergence_score: float  # 1.0 - std_dev (higher = more consensus)
    high_uncertainty: bool    # True if std_dev > 0.25
    round_summaries: list[RoundSummary]
    swing_factors: list[str] = Field(default_factory=list)
    duration_ms: float
    status: str = "complete"
