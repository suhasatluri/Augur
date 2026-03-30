"""Database layer — Neon PostgreSQL via asyncpg. Supabase-compatible schema."""

from __future__ import annotations

import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)

# Schema DDL — run once to bootstrap
SCHEMA_DDL = """
CREATE TABLE IF NOT EXISTS agent_personas (
    id              UUID PRIMARY KEY,
    simulation_id   TEXT NOT NULL,
    archetype       TEXT NOT NULL,
    variation_index INTEGER NOT NULL,
    name            TEXT NOT NULL,
    goals           TEXT NOT NULL,
    methodology     TEXT NOT NULL,
    known_biases    TEXT NOT NULL,
    conviction_threshold FLOAT NOT NULL,
    risk_tolerance  FLOAT NOT NULL,
    initial_probability  FLOAT NOT NULL,
    initial_reasoning    TEXT DEFAULT '',
    created_at      TIMESTAMPTZ DEFAULT NOW(),

    CONSTRAINT uq_sim_archetype_var UNIQUE (simulation_id, archetype, variation_index)
);

CREATE INDEX IF NOT EXISTS idx_personas_simulation ON agent_personas(simulation_id);
CREATE INDEX IF NOT EXISTS idx_personas_archetype ON agent_personas(archetype);
"""


class PersonaDB:
    """Async database client for agent persona storage.

    Falls back to in-memory storage if DATABASE_URL is not set.
    """

    def __init__(self, database_url: Optional[str] = None) -> None:
        self._url = database_url or os.getenv("DATABASE_URL")
        self._pool = None
        self._memory_store: list[dict] = []

    @property
    def is_connected(self) -> bool:
        return self._pool is not None

    async def connect(self) -> bool:
        """Connect to Neon. Returns False if no URL or connection fails."""
        if not self._url:
            logger.warning("[db] No DATABASE_URL set — using in-memory fallback")
            return False

        try:
            import asyncpg
            self._pool = await asyncpg.create_pool(self._url, min_size=1, max_size=5)
            async with self._pool.acquire() as conn:
                await conn.execute(SCHEMA_DDL)
            logger.info("[db] Connected to Neon and ensured schema")
            return True
        except ImportError:
            logger.warning("[db] asyncpg not installed — using in-memory fallback")
            return False
        except Exception as e:
            logger.error(f"[db] Connection failed: {e} — using in-memory fallback")
            self._pool = None
            return False

    async def store_personas(self, personas: list) -> int:
        """Store AgentPersona objects. Returns count stored."""
        from persona_forge.models import AgentPersona

        if self._pool:
            return await self._store_pg(personas)

        # In-memory fallback
        for p in personas:
            self._memory_store.append(p.model_dump())
        logger.info(f"[db] Stored {len(personas)} personas in memory (no DB)")
        return len(personas)

    async def _store_pg(self, personas: list) -> int:
        """Batch insert into Neon PostgreSQL."""
        from persona_forge.models import AgentPersona

        sql = """
        INSERT INTO agent_personas (
            id, simulation_id, archetype, variation_index, name,
            goals, methodology, known_biases,
            conviction_threshold, risk_tolerance,
            initial_probability, initial_reasoning, created_at
        ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13)
        ON CONFLICT (simulation_id, archetype, variation_index)
        DO UPDATE SET
            name = EXCLUDED.name,
            goals = EXCLUDED.goals,
            methodology = EXCLUDED.methodology,
            known_biases = EXCLUDED.known_biases,
            conviction_threshold = EXCLUDED.conviction_threshold,
            risk_tolerance = EXCLUDED.risk_tolerance,
            initial_probability = EXCLUDED.initial_probability,
            initial_reasoning = EXCLUDED.initial_reasoning,
            created_at = EXCLUDED.created_at
        """
        count = 0
        async with self._pool.acquire() as conn:
            for p in personas:
                await conn.execute(
                    sql,
                    p.id, p.simulation_id, p.archetype.value, p.variation_index,
                    p.name, p.goals, p.methodology, p.known_biases,
                    p.conviction_threshold, p.risk_tolerance,
                    p.initial_probability, p.initial_reasoning, p.created_at,
                )
                count += 1
        logger.info(f"[db] Stored {count} personas in Neon")
        return count

    async def get_personas(self, simulation_id: str) -> list[dict]:
        """Retrieve personas for a simulation."""
        if self._pool:
            async with self._pool.acquire() as conn:
                rows = await conn.fetch(
                    "SELECT * FROM agent_personas WHERE simulation_id = $1 ORDER BY archetype, variation_index",
                    simulation_id,
                )
                return [dict(r) for r in rows]

        return [p for p in self._memory_store if p["simulation_id"] == simulation_id]

    async def close(self) -> None:
        if self._pool:
            await self._pool.close()
            self._pool = None
