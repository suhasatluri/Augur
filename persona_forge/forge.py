"""PersonaForge — generates 50 diverse analyst personas via Claude Sonnet."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Optional

import anthropic

from persona_forge.db import PersonaDB
from persona_forge.models import (
    AgentPersona,
    Archetype,
    ARCHETYPE_DESCRIPTIONS,
    ForgeRequest,
    ForgeResponse,
)

logger = logging.getLogger(__name__)

FORGE_PROMPT = """You are designing diverse analyst agent personas for an ASX earnings prediction simulation.

Ticker: {ticker}
Archetype: {archetype_label}
Focus: {archetype_focus}
Known bias pattern: {archetype_bias}

Seed intelligence (from harvester):
{seed_context}

Generate exactly {count} DISTINCT variations of this archetype. Each variation must have a unique personality, methodology, and initial probability estimate. Vary them meaningfully — don't just change names.

Variation guidelines:
- Spread conviction_threshold across the range: some easily swayed (0.2-0.4), some stubborn (0.7-0.9)
- Spread risk_tolerance to match: {risk_guidance}
- initial_probability is P(earnings beat consensus). Spread across a realistic range for this archetype: {probability_guidance}
- Give each a memorable 2-3 word name that reflects their personality
- goals: 1-2 sentences on what this specific agent optimises for
- methodology: 1-2 sentences on HOW they analyse (what data, what framework)
- known_biases: 1 sentence on their specific cognitive bias pattern
- initial_reasoning: 1-2 sentences explaining why they arrived at their initial_probability given the seed data

Return ONLY a JSON array of objects with keys: name, goals, methodology, known_biases, conviction_threshold, risk_tolerance, initial_probability, initial_reasoning.
No markdown, no commentary — just the JSON array."""

ARCHETYPE_PARAMS = {
    # Calibration target: (0.72 + 0.30 + 0.50 + 0.40 + 0.58) / 5 = 0.50
    Archetype.BULL_ANALYST: {
        "risk_guidance": "generally high (0.6-0.9), bulls tolerate risk for upside",
        "probability_guidance": "0.64-0.80 — bulls lean toward beat. You MUST spread values across this full range. Midpoint should be ~0.72. Do NOT cluster all values near the low end.",
    },
    Archetype.BEAR_ANALYST: {
        "risk_guidance": "moderate to low (0.2-0.5), bears are cautious by nature",
        "probability_guidance": "0.22-0.38 — bears lean toward miss. Spread across range. Midpoint ~0.30.",
    },
    Archetype.QUANT_TRADER: {
        "risk_guidance": "varies widely (0.3-0.8), depends on model confidence",
        "probability_guidance": "0.42-0.58 — quants cluster near base rate, symmetric around 0.50. Some should be ABOVE 0.50, some below.",
    },
    Archetype.RISK_OFFICER: {
        "risk_guidance": "low (0.1-0.4), risk officers are inherently conservative",
        "probability_guidance": "0.32-0.48 — risk officers weight downside but not extreme. Midpoint ~0.40.",
    },
    Archetype.RETAIL_INVESTOR: {
        "risk_guidance": "varies wildly (0.2-0.9), retail is heterogeneous",
        "probability_guidance": "0.42-0.74 — retail follows sentiment, wide spread. Some should be strongly bullish (0.65+). Midpoint ~0.58.",
    },
}


def _parse_json_response(raw: str) -> list[dict]:
    """Extract JSON array from model response, stripping markdown fences."""
    text = raw.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1]
        if text.endswith("```"):
            text = text[: text.rfind("```")]
        text = text.strip()
    return json.loads(text)


class PersonaForge:
    """Generates analyst agent personas for swarm simulation."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        database_url: Optional[str] = None,
    ) -> None:
        self.client = anthropic.AsyncAnthropic(api_key=api_key)
        self.db = PersonaDB(database_url=database_url)

    async def forge(self, request: ForgeRequest) -> ForgeResponse:
        """Generate all personas for a simulation. 5 parallel API calls (one per archetype)."""
        start = time.monotonic()

        # Connect to DB (gracefully falls back to memory)
        await self.db.connect()

        # Create simulation row
        await self.db.ensure_simulation(
            request.simulation_id, request.ticker,
        )

        seed_context = self._build_seed_context(request.seed_summaries)

        # Fire all 5 archetypes in parallel
        tasks = [
            self._forge_archetype(
                archetype=arch,
                ticker=request.ticker,
                simulation_id=request.simulation_id,
                seed_context=seed_context,
                count=request.agents_per_archetype,
            )
            for arch in Archetype
        ]

        results = await asyncio.gather(*tasks, return_exceptions=True)

        all_personas: list[AgentPersona] = []
        for arch, result in zip(Archetype, results):
            if isinstance(result, Exception):
                logger.error(f"[forge] Failed to forge {arch.value}: {result}")
                continue
            all_personas.extend(result)

        # Calibration check — weighted average should be ~0.50
        if all_personas:
            avg_prob = sum(p.initial_probability for p in all_personas) / len(all_personas)
            if not (0.47 <= avg_prob <= 0.53):
                logger.warning(
                    f"[forge] CALIBRATION WARNING: weighted avg initial_probability "
                    f"= {avg_prob:.3f} (target 0.47-0.53) for {request.ticker}"
                )
            else:
                logger.info(f"[forge] Calibration OK: avg initial_probability = {avg_prob:.3f}")

        # Store in DB
        stored_in_db = False
        if all_personas:
            count = await self.db.store_personas(all_personas)
            stored_in_db = self.db.is_connected and count > 0

        elapsed = (time.monotonic() - start) * 1000

        return ForgeResponse(
            simulation_id=request.simulation_id,
            ticker=request.ticker,
            personas=all_personas,
            total_count=len(all_personas),
            forge_duration_ms=round(elapsed, 1),
            stored_in_db=stored_in_db,
        )

    async def _forge_archetype(
        self,
        archetype: Archetype,
        ticker: str,
        simulation_id: str,
        seed_context: str,
        count: int,
    ) -> list[AgentPersona]:
        """Generate all variations for a single archetype via one Sonnet call."""

        desc = ARCHETYPE_DESCRIPTIONS[archetype]
        params = ARCHETYPE_PARAMS[archetype]

        prompt = FORGE_PROMPT.format(
            ticker=ticker,
            archetype_label=desc["label"],
            archetype_focus=desc["focus"],
            archetype_bias=desc["bias"],
            seed_context=seed_context,
            count=count,
            risk_guidance=params["risk_guidance"],
            probability_guidance=params["probability_guidance"],
        )

        logger.info(f"[forge] Generating {count} {desc['label']} personas for {ticker}...")

        message = await self.client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=4096,
            messages=[{"role": "user", "content": prompt}],
        )

        raw = message.content[0].text
        try:
            items = _parse_json_response(raw)
        except json.JSONDecodeError:
            logger.error(f"[forge] JSON parse failed for {archetype.value}: {raw[:200]}")
            return []

        personas: list[AgentPersona] = []
        for i, item in enumerate(items[:count]):
            try:
                persona = AgentPersona(
                    simulation_id=simulation_id,
                    archetype=archetype,
                    variation_index=i,
                    name=item["name"],
                    goals=item["goals"],
                    methodology=item["methodology"],
                    known_biases=item["known_biases"],
                    conviction_threshold=float(item["conviction_threshold"]),
                    risk_tolerance=float(item["risk_tolerance"]),
                    initial_probability=float(item["initial_probability"]),
                    initial_reasoning=item.get("initial_reasoning", ""),
                )
                personas.append(persona)
            except (KeyError, ValueError) as e:
                logger.warning(f"[forge] Skipping malformed persona: {e}")

        logger.info(f"[forge] Forged {len(personas)} {desc['label']} personas")
        return personas

    def _build_seed_context(self, seed_summaries: list[str]) -> str:
        if not seed_summaries:
            return "(no seed data provided — use general knowledge of the ticker)"
        return "\n".join(f"- {s}" for s in seed_summaries)
