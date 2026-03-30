"""Database operations for prediction synthesiser."""

from __future__ import annotations

import json
import logging

logger = logging.getLogger(__name__)


async def load_final_agent_states(pool, simulation_id: str) -> list[dict]:
    """Load all agents with their final state."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT id, archetype, persona,
                      initial_probability, current_probability, conviction, round_memory
               FROM agents
               WHERE simulation_id = $1
               ORDER BY archetype""",
            simulation_id,
        )
    results = []
    for r in rows:
        persona = json.loads(r["persona"]) if isinstance(r["persona"], str) else r["persona"]
        memory = json.loads(r["round_memory"]) if isinstance(r["round_memory"], str) else r["round_memory"]
        results.append({
            "id": str(r["id"]),
            "archetype": r["archetype"],
            "name": persona.get("name", "Unknown"),
            "initial_probability": r["initial_probability"],
            "current_probability": r["current_probability"],
            "conviction": r["conviction"],
            "round_memory": memory if isinstance(memory, list) else [],
        })
    logger.info(f"[synth-db] Loaded {len(results)} agents for {simulation_id}")
    return results


async def load_all_round_results(pool, simulation_id: str) -> list[dict]:
    """Load all round_results for reasoning analysis."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT rr.round_number, rr.agent_id, rr.probability,
                      rr.reasoning, rr.conviction_delta, a.archetype
               FROM round_results rr
               JOIN agents a ON a.id = rr.agent_id
               WHERE rr.simulation_id = $1
               ORDER BY rr.round_number, a.archetype""",
            simulation_id,
        )
    return [dict(r) for r in rows]


async def load_simulation(pool, simulation_id: str) -> dict | None:
    """Load simulation metadata."""
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM simulations WHERE id = $1", simulation_id
        )
    return dict(row) if row else None
