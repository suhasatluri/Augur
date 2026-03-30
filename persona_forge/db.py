"""Database layer for persona_forge — stores agents in Neon PostgreSQL."""

from __future__ import annotations

import json
import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)


class PersonaDB:
    """Async database client for agent persona storage.

    Uses the shared `agents` table with JSONB persona column.
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
        """Connect to Neon and ensure schema. Returns False on failure."""
        if not self._url:
            logger.warning("[db] No DATABASE_URL set — using in-memory fallback")
            return False

        try:
            from db.schema import ensure_schema, get_pool
            await ensure_schema(self._url)
            self._pool = await get_pool(self._url)
            logger.info("[db] Connected to Neon (agents table ready)")
            return True
        except ImportError:
            logger.warning("[db] db.schema not available — trying asyncpg directly")
            try:
                import asyncpg
                self._pool = await asyncpg.create_pool(self._url, min_size=1, max_size=5)
                logger.info("[db] Connected to Neon (schema not auto-ensured)")
                return True
            except Exception as e:
                logger.error(f"[db] Connection failed: {e} — using in-memory fallback")
                return False
        except Exception as e:
            logger.error(f"[db] Connection failed: {e} — using in-memory fallback")
            self._pool = None
            return False

    async def ensure_simulation(self, simulation_id: str, ticker: str, seed_quality: float = 0.0) -> None:
        """Create a simulation row if it doesn't exist."""
        if not self._pool:
            return

        async with self._pool.acquire() as conn:
            await conn.execute(
                """INSERT INTO simulations (id, ticker, status, seed_quality)
                   VALUES ($1, $2, 'forging', $3)
                   ON CONFLICT (id) DO UPDATE SET status = 'forging'""",
                simulation_id, ticker, seed_quality,
            )

    async def store_personas(self, personas: list) -> int:
        """Store AgentPersona objects into the agents table. Returns count stored."""
        if self._pool:
            return await self._store_pg(personas)

        for p in personas:
            self._memory_store.append(p.model_dump())
        logger.info(f"[db] Stored {len(personas)} personas in memory (no DB)")
        return len(personas)

    async def _store_pg(self, personas: list) -> int:
        """Batch insert into Neon agents table with JSONB persona."""
        sql = """
        INSERT INTO agents (
            id, simulation_id, archetype, persona,
            initial_probability, current_probability, conviction
        ) VALUES ($1, $2, $3, $4::jsonb, $5, $6, $7)
        ON CONFLICT (id) DO UPDATE SET
            persona = EXCLUDED.persona,
            initial_probability = EXCLUDED.initial_probability,
            current_probability = EXCLUDED.current_probability,
            conviction = EXCLUDED.conviction
        """
        count = 0
        async with self._pool.acquire() as conn:
            for p in personas:
                persona_json = json.dumps({
                    "name": p.name,
                    "variation_index": p.variation_index,
                    "goals": p.goals,
                    "methodology": p.methodology,
                    "known_biases": p.known_biases,
                    "risk_tolerance": p.risk_tolerance,
                    "conviction_threshold": p.conviction_threshold,
                    "initial_reasoning": p.initial_reasoning,
                })
                await conn.execute(
                    sql,
                    p.id, p.simulation_id, p.archetype.value, persona_json,
                    p.initial_probability, p.initial_probability,  # current = initial at forge time
                    p.conviction_threshold,
                )
                count += 1
        logger.info(f"[db] Stored {count} agents in Neon")
        return count

    async def get_agents(self, simulation_id: str) -> list[dict]:
        """Retrieve agents for a simulation."""
        if self._pool:
            async with self._pool.acquire() as conn:
                rows = await conn.fetch(
                    "SELECT * FROM agents WHERE simulation_id = $1 ORDER BY archetype, (persona->>'variation_index')::int",
                    simulation_id,
                )
                return [dict(r) for r in rows]

        return [p for p in self._memory_store if p["simulation_id"] == simulation_id]

    async def close(self) -> None:
        # Pool is shared via db.schema — don't close it here
        pass
