"""Data models for seed harvester."""

from __future__ import annotations

import enum
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class SeedType(str, enum.Enum):
    FINANCIAL = "financial"
    SENTIMENT = "sentiment"
    GUIDANCE = "guidance"
    MACRO = "macro"
    SECTOR = "sector"


class Seed(BaseModel):
    """A single intelligence seed extracted by an agent."""

    ticker: str
    seed_type: SeedType
    content: str
    confidence: float = Field(ge=0.0, le=1.0)
    source: str = ""
    reasoning: str = ""
    harvested_at: datetime = Field(default_factory=datetime.utcnow)

    def __repr__(self) -> str:
        return f"Seed({self.ticker}, {self.seed_type.value}, conf={self.confidence:.2f})"


class CacheEntry(BaseModel):
    """Wrapper around cached seeds with TTL metadata."""

    ticker: str
    seeds: list[Seed]
    layer: str  # "slow" or "fast"
    created_at: datetime = Field(default_factory=datetime.utcnow)
    ttl_seconds: int
    invalidated: bool = False

    @property
    def is_expired(self) -> bool:
        age = (datetime.utcnow() - self.created_at).total_seconds()
        return age > self.ttl_seconds or self.invalidated


class HarvestRequest(BaseModel):
    """Request to harvest seeds for a ticker."""

    ticker: str
    force_refresh: bool = False
    layers: list[str] = Field(default_factory=lambda: ["slow", "fast"])


class HarvestResponse(BaseModel):
    """Response containing harvested seeds."""

    ticker: str
    seeds: list[Seed]
    slow_layer_cached: bool = False
    fast_layer_cached: bool = False
    harvest_duration_ms: Optional[float] = None
