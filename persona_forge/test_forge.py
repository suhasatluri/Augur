"""Test script — forge personas from BHP harvest seeds and display results."""

from __future__ import annotations

import asyncio
import logging
import os
import sys

from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))

from persona_forge.forge import PersonaForge
from persona_forge.models import Archetype, ForgeRequest


# Sample seed summaries from a BHP harvest run
BHP_SEED_SUMMARIES = [
    "[FINANCIAL] Iron ore revenue likely dominant earnings driver, margins sensitive to price realizations vs inflated costs",
    "[FINANCIAL] Copper margins likely improving from structural supply constraints, offset by operational cost pressures",
    "[GUIDANCE] Management guidance on FY2026 capex and production targets — copper expansion and iron ore sustaining capex",
    "[GUIDANCE] Dividend policy framework commentary and potential for special dividends tied to balance sheet strength",
    "[SECTOR] ESG requirements and decarbonization impacting operational costs and project planning",
    "[SECTOR] Competitive positioning in copper strengthening on asset quality, but permitting timelines a risk",
    "[MACRO] China stimulus and property sector recovery pace critical for iron ore demand",
    "[MACRO] AUD/USD movements significant for earnings translation — USD revenue vs AUD cost base",
    "[SENTIMENT] Analyst consensus shifting cautious on iron ore amid price weakness below $100/t",
    "[SENTIMENT] Broker views divided on capital allocation credibility — returns vs growth capex tension",
]


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    ticker = sys.argv[1] if len(sys.argv) > 1 else "BHP"
    count = int(sys.argv[2]) if len(sys.argv) > 2 else 10

    request = ForgeRequest(
        ticker=ticker,
        seed_summaries=BHP_SEED_SUMMARIES,
        agents_per_archetype=count,
    )

    print(f"\n{'='*70}")
    print(f"  AUGUR Persona Forge — {ticker}")
    print(f"  Simulation: {request.simulation_id}")
    print(f"  Target: {count} agents × {len(Archetype)} archetypes = {count * len(Archetype)} total")
    print(f"{'='*70}\n")

    forge = PersonaForge()
    result = await forge.forge(request)

    # --- Summary ---
    print(f"\n{'─'*70}")
    print(f"  FORGE RESULTS")
    print(f"{'─'*70}")
    print(f"  Total Personas:  {result.total_count}")
    print(f"  Stored in DB:    {'YES (Neon)' if result.stored_in_db else 'NO (in-memory fallback)'}")
    print(f"  Duration:        {result.forge_duration_ms:.0f}ms")

    # --- Per-archetype stats ---
    print(f"\n{'─'*70}")
    print(f"  ARCHETYPE DISTRIBUTION")
    print(f"{'─'*70}")
    for arch in Archetype:
        group = [p for p in result.personas if p.archetype == arch]
        if not group:
            print(f"  {arch.value:20s}  FAILED (0 personas)")
            continue
        probs = [p.initial_probability for p in group]
        risks = [p.risk_tolerance for p in group]
        convictions = [p.conviction_threshold for p in group]
        avg_prob = sum(probs) / len(probs)
        prob_spread = max(probs) - min(probs)
        print(f"  {arch.value:20s}  n={len(group):2d}  "
              f"P(beat)={avg_prob:.2f} [{min(probs):.2f}-{max(probs):.2f}]  "
              f"risk={sum(risks)/len(risks):.2f}  "
              f"conviction={sum(convictions)/len(convictions):.2f}  "
              f"spread={prob_spread:.2f}")

    # --- All personas ---
    print(f"\n{'─'*70}")
    print(f"  ALL PERSONAS ({result.total_count})")
    print(f"{'─'*70}")
    for i, p in enumerate(result.personas, 1):
        prob_bar = "▓" * int(p.initial_probability * 10) + "░" * (10 - int(p.initial_probability * 10))
        print(f"\n  [{i:2d}] {p.name} ({p.archetype.value})")
        print(f"       P(beat): {p.initial_probability:.2f} [{prob_bar}]  "
              f"risk: {p.risk_tolerance:.2f}  conviction: {p.conviction_threshold:.2f}")
        print(f"       Goals:      {p.goals}")
        print(f"       Method:     {p.methodology}")
        print(f"       Biases:     {p.known_biases}")
        print(f"       Reasoning:  {p.initial_reasoning}")

    print(f"\n{'='*70}")
    print(f"  FORGE COMPLETE — {result.total_count} personas ready for negotiation_runner")
    print(f"{'='*70}\n")


if __name__ == "__main__":
    asyncio.run(main())
