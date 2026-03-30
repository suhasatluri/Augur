"""Data models for persona forge — Supabase-compatible schema."""

from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class Archetype(str, enum.Enum):
    BULL_ANALYST = "bull_analyst"
    BEAR_ANALYST = "bear_analyst"
    QUANT_TRADER = "quant_trader"
    RISK_OFFICER = "risk_officer"
    RETAIL_INVESTOR = "retail_investor"


ARCHETYPE_DESCRIPTIONS = {
    Archetype.BULL_ANALYST: {
        "label": "Bull Analyst",
        "focus": "growth-focused, optimistic on guidance, weights revenue acceleration and TAM expansion",
        "bias": "confirmation bias toward bullish signals, anchoring on management optimism",
    },
    Archetype.BEAR_ANALYST: {
        "label": "Bear Analyst",
        "focus": "margin-focused, sceptical of management, weights cost pressures and execution risk",
        "bias": "negativity bias, overweights downside scenarios and management credibility gaps",
    },
    Archetype.QUANT_TRADER: {
        "label": "Quant Trader",
        "focus": "pattern-based, historical beat/miss rate, statistical models and mean reversion",
        "bias": "overfitting to historical patterns, underweights regime changes and structural breaks",
    },
    Archetype.RISK_OFFICER: {
        "label": "Risk Officer",
        "focus": "tail-risk focused, conservative, weights worst-case scenarios and balance sheet stress",
        "bias": "loss aversion, overweights low-probability high-impact events, slow to update priors",
    },
    Archetype.RETAIL_INVESTOR: {
        "label": "Retail Investor",
        "focus": "sentiment-driven, recency bias, weights recent price action and social narrative",
        "bias": "herding, recency bias, overreacts to headlines, anchors on entry price",
    },
}


class AgentPersona(BaseModel):
    """A single analyst agent persona ready for negotiation."""

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    simulation_id: str
    archetype: Archetype
    variation_index: int = Field(ge=0, le=9)
    name: str
    goals: str
    methodology: str
    known_biases: str
    conviction_threshold: float = Field(ge=0.0, le=1.0)
    risk_tolerance: float = Field(ge=0.0, le=1.0)
    initial_probability: float = Field(ge=0.0, le=1.0)
    initial_reasoning: str = ""
    created_at: datetime = Field(default_factory=datetime.utcnow)

    class Config:
        json_schema_extra = {
            "example": {
                "id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
                "simulation_id": "sim-001",
                "archetype": "bull_analyst",
                "variation_index": 0,
                "name": "Growth Hunter Alpha",
                "goals": "Identify upside earnings surprises driven by revenue acceleration",
                "methodology": "Top-down revenue modeling with TAM expansion focus",
                "known_biases": "Anchors on management optimism, underweights cost headwinds",
                "conviction_threshold": 0.6,
                "risk_tolerance": 0.8,
                "initial_probability": 0.65,
            }
        }


class ForgeRequest(BaseModel):
    """Request to forge agent personas for a simulation."""

    simulation_id: str = Field(default_factory=lambda: f"sim-{uuid.uuid4().hex[:8]}")
    ticker: str
    seed_summaries: list[str] = Field(default_factory=list)
    agents_per_archetype: int = Field(default=10, ge=1, le=20)


class ForgeResponse(BaseModel):
    """Response from persona forging."""

    simulation_id: str
    ticker: str
    personas: list[AgentPersona]
    total_count: int = 0
    forge_duration_ms: Optional[float] = None
    stored_in_db: bool = False
