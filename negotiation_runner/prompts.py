"""Prompt templates for negotiation rounds."""

# Haiku generates a narrative summary of the round distribution
ROUND_SUMMARY_PROMPT = """You are summarising a debate round for an ASX earnings prediction simulation.

Ticker: {ticker}
Round: {round_number} of {total_rounds}

Current probability distribution across {agent_count} analyst agents:
- Mean P(earnings beat): {mean_prob:.3f}
- Median: {median_prob:.3f}
- Std Dev: {std_dev:.3f}
- Range: [{min_prob:.2f} — {max_prob:.2f}]
- Bulls (P>0.6): {bull_count}  |  Neutral (0.4-0.6): {neutral_count}  |  Bears (P<0.4): {bear_count}

{movement_note}

Write a 2-3 sentence narrative summary of the current state of the debate.
Focus on: where consensus is forming, which camps are strongest, and what the key disagreement is.
Be specific and analytical — this will be shown to each agent before they update their view.

Return ONLY the narrative text, no JSON, no formatting."""

# Sonnet processes a batch of agents for one archetype in one round
DEBATE_BATCH_PROMPT = """You are running a negotiation round for an ASX earnings prediction simulation.

Ticker: {ticker}
Round: {round_number} of {total_rounds}

=== ROUND SUMMARY (what the swarm currently believes) ===
{round_narrative}

Distribution: Mean={mean_prob:.3f}, Median={median_prob:.3f}, StdDev={std_dev:.3f}
Bulls={bull_count} | Neutral={neutral_count} | Bears={bear_count}

=== YOUR AGENTS ({archetype}, batch of {batch_size}) ===
Process each agent below. For each one:
- Consider their persona, methodology, and biases
- Look at their previous positions and reasoning
- Consider the round summary and what other agents believe
- Decide: should they UPDATE their probability or HOLD?
- Agents with low conviction_threshold move easily; high conviction_threshold agents need strong evidence to shift
- Movement should be realistic: most agents move 0.01-0.05 per round, rarely more than 0.10
- Agents can move TOWARD or AWAY from consensus depending on their persona

{agent_blocks}

Return ONLY a JSON array with one object per agent, each containing:
- agent_id: the UUID string
- probability: new P(earnings beat), 0.0-1.0
- reasoning: 2-3 sentences explaining their updated (or held) view
- conviction_delta: how much their conviction changed (-0.2 to +0.2, positive = more confident)

No markdown, no commentary — just the JSON array."""

AGENT_BLOCK_TEMPLATE = """--- Agent: {name} (id: {agent_id}) ---
Archetype: {archetype}
Goals: {goals}
Methodology: {methodology}
Known biases: {known_biases}
Conviction threshold: {conviction_threshold:.2f} | Risk tolerance: {risk_tolerance:.2f}
Current probability: {current_probability:.3f}
Current conviction: {conviction:.3f}
Previous rounds:
{round_history}"""


def build_agent_block(agent) -> str:
    """Build the text block for a single agent in the batch prompt."""
    if agent.round_history:
        history_lines = []
        for rh in agent.round_history[-3:]:  # Last 3 rounds max
            history_lines.append(
                f"  Round {rh['round']}: P={rh['probability']:.3f} "
                f"(delta={rh['conviction_delta']:+.3f}) — {rh['reasoning'][:120]}"
            )
        history_text = "\n".join(history_lines)
    else:
        history_text = "  (first round — no history)"

    return AGENT_BLOCK_TEMPLATE.format(
        name=agent.name,
        agent_id=agent.id,
        archetype=agent.archetype,
        goals=agent.goals,
        methodology=agent.methodology,
        known_biases=agent.known_biases,
        conviction_threshold=agent.conviction_threshold,
        risk_tolerance=agent.risk_tolerance,
        current_probability=agent.current_probability,
        conviction=agent.conviction,
        round_history=history_text,
    )
