"""Augur database schema — Neon PostgreSQL, Supabase-compatible."""

from __future__ import annotations

import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)

SCHEMA_DDL = """
-- ============================================================
-- simulations: top-level container for each prediction run
-- ============================================================
CREATE TABLE IF NOT EXISTS simulations (
    id              TEXT PRIMARY KEY,
    ticker          TEXT NOT NULL,
    reporting_date  DATE,
    status          TEXT NOT NULL DEFAULT 'pending'
                    CHECK (status IN ('pending','forging','negotiating','complete','failed')),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at    TIMESTAMPTZ,
    seed_quality    FLOAT CHECK (seed_quality >= 0 AND seed_quality <= 1)
);

CREATE INDEX IF NOT EXISTS idx_simulations_ticker ON simulations(ticker);
CREATE INDEX IF NOT EXISTS idx_simulations_status ON simulations(status);

-- ============================================================
-- agents: one row per agent persona in a simulation
-- ============================================================
CREATE TABLE IF NOT EXISTS agents (
    id                  UUID PRIMARY KEY,
    simulation_id       TEXT NOT NULL REFERENCES simulations(id) ON DELETE CASCADE,
    archetype           TEXT NOT NULL,
    persona             JSONB NOT NULL,
    initial_probability FLOAT NOT NULL CHECK (initial_probability >= 0 AND initial_probability <= 1),
    current_probability FLOAT NOT NULL CHECK (current_probability >= 0 AND current_probability <= 1),
    conviction          FLOAT NOT NULL DEFAULT 0.5 CHECK (conviction >= 0 AND conviction <= 1),
    round_memory        JSONB NOT NULL DEFAULT '[]'::JSONB,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_agents_simulation ON agents(simulation_id);
CREATE INDEX IF NOT EXISTS idx_agents_archetype ON agents(archetype);

-- ============================================================
-- round_results: per-agent state after each negotiation round
-- ============================================================
CREATE TABLE IF NOT EXISTS round_results (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    simulation_id   TEXT NOT NULL REFERENCES simulations(id) ON DELETE CASCADE,
    round_number    INTEGER NOT NULL CHECK (round_number >= 0),
    agent_id        UUID NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
    probability     FLOAT NOT NULL CHECK (probability >= 0 AND probability <= 1),
    reasoning       TEXT NOT NULL DEFAULT '',
    conviction_delta FLOAT NOT NULL DEFAULT 0,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT uq_round_agent UNIQUE (simulation_id, round_number, agent_id)
);

CREATE INDEX IF NOT EXISTS idx_rounds_simulation ON round_results(simulation_id);
CREATE INDEX IF NOT EXISTS idx_rounds_agent ON round_results(agent_id);
CREATE INDEX IF NOT EXISTS idx_rounds_round ON round_results(simulation_id, round_number);

-- ============================================================
-- asx_companies: company master data (refreshed weekly)
-- ============================================================
CREATE TABLE IF NOT EXISTS asx_companies (
    ticker          TEXT PRIMARY KEY,
    company_name    TEXT,
    sector          TEXT,
    industry        TEXT,
    market_cap_aud  BIGINT,
    shares_on_issue BIGINT,
    fiscal_year_end TEXT,       -- e.g. "JUN", "DEC", "MAR"
    ir_page_url     TEXT,
    last_updated    TIMESTAMPTZ DEFAULT NOW()
);

-- ============================================================
-- asx_earnings: one row per reporting period per company
-- ============================================================
CREATE TABLE IF NOT EXISTS asx_earnings (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    ticker                  TEXT NOT NULL,
    period                  TEXT NOT NULL,       -- e.g. "H1 FY2025"
    reporting_date          DATE NOT NULL,
    result_type             TEXT,                -- "HALF_YEAR" or "FULL_YEAR"

    -- Actuals
    revenue_aud_m           FLOAT,
    npat_aud_m              FLOAT,
    eps_basic_cents         FLOAT,
    eps_diluted_cents       FLOAT,
    dividend_cents          FLOAT,

    -- vs consensus
    eps_consensus_cents     FLOAT,
    revenue_consensus_aud_m FLOAT,
    beat_miss               TEXT,                -- BEAT / MISS / INLINE / UNKNOWN
    surprise_pct            FLOAT,

    -- Price reaction
    price_day_before        FLOAT,
    price_day_after         FLOAT,
    price_reaction_pct      FLOAT,
    price_implied_result    TEXT,                -- BEAT / MISS / INLINE

    -- Source
    announcement_url        TEXT,
    data_source             TEXT,                -- pdf / price_proxy / web_search
    data_confidence         TEXT,                -- HIGH / MED / LOW
    extracted_at            TIMESTAMPTZ DEFAULT NOW(),

    UNIQUE(ticker, reporting_date)
);

CREATE INDEX IF NOT EXISTS idx_asx_earnings_ticker ON asx_earnings(ticker);
CREATE INDEX IF NOT EXISTS idx_asx_earnings_date ON asx_earnings(reporting_date);

-- ============================================================
-- asx_commentary: management quotes per result
-- ============================================================
CREATE TABLE IF NOT EXISTS asx_commentary (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    ticker          TEXT NOT NULL,
    reporting_date  DATE NOT NULL,
    quote           TEXT NOT NULL,
    quote_type      TEXT,            -- guidance / outlook / risk / positive
    extracted_from  TEXT,            -- PDF filename
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_asx_commentary_ticker ON asx_commentary(ticker);

-- ============================================================
-- asx_metrics: computed metrics per company (refreshed after each scrape)
-- ============================================================
CREATE TABLE IF NOT EXISTS asx_metrics (
    ticker                  TEXT PRIMARY KEY,
    beat_rate_8q            FLOAT,
    beat_rate_4q            FLOAT,
    avg_surprise_pct        FLOAT,
    guidance_delivery_rate  FLOAT,
    mgmt_credibility_score  FLOAT,
    data_confidence         TEXT,       -- HIGH / MED / LOW
    quarters_available      INT,
    last_computed           TIMESTAMPTZ DEFAULT NOW()
);

-- ============================================================
-- asx_calendar: upcoming earnings dates
-- ============================================================
CREATE TABLE IF NOT EXISTS asx_calendar (
    ticker                  TEXT NOT NULL,
    expected_reporting_date  DATE NOT NULL,
    result_type             TEXT,
    confirmed               BOOLEAN DEFAULT FALSE,
    source                  TEXT,
    created_at              TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (ticker, expected_reporting_date)
);

CREATE INDEX IF NOT EXISTS idx_asx_calendar_date ON asx_calendar(expected_reporting_date);
"""

_pool = None


async def get_pool(database_url: Optional[str] = None):
    """Get or create the shared connection pool."""
    global _pool
    if _pool is not None:
        return _pool

    import asyncpg

    url = database_url or os.getenv("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL not set")

    _pool = await asyncpg.create_pool(url, min_size=1, max_size=5)
    return _pool


async def ensure_schema(database_url: Optional[str] = None) -> bool:
    """Connect to Neon and run schema DDL. Returns True on success."""
    try:
        pool = await get_pool(database_url)
        async with pool.acquire() as conn:
            await conn.execute(SCHEMA_DDL)
        logger.info("[schema] Schema ensured in Neon")
        return True
    except Exception as e:
        logger.error(f"[schema] Failed to ensure schema: {e}")
        return False


async def close_pool() -> None:
    global _pool
    if _pool:
        await _pool.close()
        _pool = None
