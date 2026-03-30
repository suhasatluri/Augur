"""Database operations for negotiation runner."""

from __future__ import annotations

import json
import logging
from datetime import datetime

from negotiation_runner.models import AgentState, RoundResult

logger = logging.getLogger(__name__)


async def load_agents(pool, simulation_id: str) -> list[AgentState]:
    """Load all agents for a simulation from Neon."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT id, simulation_id, archetype, persona,
                      initial_probability, current_probability, conviction, round_memory
               FROM agents
               WHERE simulation_id = $1
               ORDER BY archetype, (persona->>'variation_index')::int""",
            simulation_id,
        )

    agents = []
    for r in rows:
        persona = json.loads(r["persona"]) if isinstance(r["persona"], str) else r["persona"]
        history = json.loads(r["round_memory"]) if isinstance(r["round_memory"], str) else r["round_memory"]
        agents.append(AgentState(
            id=str(r["id"]),
            simulation_id=r["simulation_id"],
            archetype=r["archetype"],
            name=persona.get("name", "Unknown"),
            goals=persona.get("goals", ""),
            methodology=persona.get("methodology", ""),
            known_biases=persona.get("known_biases", ""),
            conviction_threshold=persona.get("conviction_threshold", 0.5),
            risk_tolerance=persona.get("risk_tolerance", 0.5),
            initial_probability=r["initial_probability"],
            current_probability=r["current_probability"],
            conviction=r["conviction"],
            round_history=history if isinstance(history, list) else [],
        ))

    logger.info(f"[db] Loaded {len(agents)} agents for {simulation_id}")
    return agents


async def store_round_results(pool, simulation_id: str, results: list[RoundResult]) -> int:
    """Batch insert round results into Neon."""
    sql = """
    INSERT INTO round_results (simulation_id, round_number, agent_id, probability, reasoning, conviction_delta)
    VALUES ($1, $2, $3::uuid, $4, $5, $6)
    ON CONFLICT (simulation_id, round_number, agent_id)
    DO UPDATE SET probability = EXCLUDED.probability,
                  reasoning = EXCLUDED.reasoning,
                  conviction_delta = EXCLUDED.conviction_delta
    """
    count = 0
    async with pool.acquire() as conn:
        for r in results:
            await conn.execute(sql, simulation_id, r.round_number, r.agent_id,
                               r.probability, r.reasoning, r.conviction_delta)
            count += 1
    return count


async def update_agent_state(pool, agent: AgentState) -> None:
    """Update an agent's current_probability, conviction, and round_memory in Neon."""
    async with pool.acquire() as conn:
        await conn.execute(
            """UPDATE agents
               SET current_probability = $1,
                   conviction = $2,
                   round_memory = $3::jsonb
               WHERE id = $4::uuid""",
            agent.current_probability,
            agent.conviction,
            json.dumps(agent.round_history),
            agent.id,
        )


async def update_simulation_status(pool, simulation_id: str, status: str) -> None:
    """Update simulation status and optionally set completed_at."""
    async with pool.acquire() as conn:
        if status == "complete":
            await conn.execute(
                "UPDATE simulations SET status = $1, completed_at = $2 WHERE id = $3",
                status, datetime.utcnow(), simulation_id,
            )
        else:
            await conn.execute(
                "UPDATE simulations SET status = $1 WHERE id = $2",
                status, simulation_id,
            )
    logger.info(f"[db] Simulation {simulation_id} → {status}")
