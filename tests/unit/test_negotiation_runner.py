"""Unit tests for negotiation_runner.runner — pure helpers + debate response parsing.

Skips run() (full DB + multi-LLM orchestration — belongs in integration tests).
Focuses on the regression-prone surface: JSON parsing, distribution stats,
cost math, and Sonnet response handling in _debate_archetype_batch.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from negotiation_runner.models import AgentState, RoundSummary
from negotiation_runner.runner import (
    NegotiationRunner,
    _compute_summary_stats,
    _parse_json,
)


# ---------- _parse_json ----------

class TestParseJson:
    def test_plain_array(self):
        assert _parse_json('[{"a": 1}, {"b": 2}]') == [{"a": 1}, {"b": 2}]

    def test_markdown_fence_with_lang(self):
        assert _parse_json('```json\n[{"a": 1}]\n```') == [{"a": 1}]

    def test_markdown_fence_no_lang(self):
        assert _parse_json('```\n[{"a": 1}]\n```') == [{"a": 1}]

    def test_array_embedded_in_prose(self):
        assert _parse_json('Here:\n[{"a": 1}, {"b": 2}]\nthx') == [{"a": 1}, {"b": 2}]

    def test_empty_array(self):
        assert _parse_json("[]") == []

    def test_no_array_raises(self):
        with pytest.raises(json.JSONDecodeError):
            _parse_json("not json")


# ---------- _compute_summary_stats ----------

def _agent(prob, archetype="value"):
    return AgentState(
        id="x", simulation_id="s", archetype=archetype, name="n",
        goals="g", methodology="m", known_biases="b",
        conviction_threshold=0.5, risk_tolerance=0.5,
        initial_probability=prob, current_probability=prob, conviction=0.5,
    )


class TestComputeSummaryStats:
    def test_basic_distribution(self):
        agents = [_agent(p) for p in [0.2, 0.5, 0.8]]
        s = _compute_summary_stats(agents, round_number=1)
        assert s.round_number == 1
        assert s.mean_probability == 0.5
        assert s.median_probability == 0.5
        assert s.min_probability == 0.2
        assert s.max_probability == 0.8

    def test_bull_bear_neutral_classification(self):
        # >0.6 bull, <0.4 bear, else neutral
        agents = [_agent(p) for p in [0.7, 0.65, 0.5, 0.4, 0.3, 0.1]]
        s = _compute_summary_stats(agents, 1)
        assert s.bull_count == 2     # 0.7, 0.65
        assert s.bear_count == 2     # 0.3, 0.1
        assert s.neutral_count == 2  # 0.5, 0.4 (0.4 is not <0.4)

    def test_neutral_boundaries(self):
        # 0.6 is not >0.6 → neutral; 0.4 is not <0.4 → neutral
        agents = [_agent(p) for p in [0.6, 0.4]]
        s = _compute_summary_stats(agents, 1)
        assert s.bull_count == 0
        assert s.bear_count == 0
        assert s.neutral_count == 2

    def test_single_agent_zero_std(self):
        s = _compute_summary_stats([_agent(0.5)], 1)
        assert s.std_dev == 0.0

    def test_round_number_propagated(self):
        s = _compute_summary_stats([_agent(0.5)], round_number=42)
        assert s.round_number == 42

    def test_values_rounded_to_4dp(self):
        agents = [_agent(p) for p in [0.123456789, 0.987654321]]
        s = _compute_summary_stats(agents, 1)
        # mean ~0.5556 — 4 decimals
        assert len(str(s.mean_probability).split(".")[-1]) <= 4


# ---------- token_summary cost math ----------

class TestTokenSummary:
    def test_zero_tokens_zero_cost(self):
        r = NegotiationRunner.__new__(NegotiationRunner)
        r._token_counts = {"sonnet_input": 0, "sonnet_output": 0,
                           "haiku_input": 0, "haiku_output": 0}
        s = r.token_summary
        assert s["estimated_cost_usd"] == 0.0

    def test_sonnet_pricing(self):
        # 1M sonnet_input @ $3 + 1M sonnet_output @ $15 = $18
        r = NegotiationRunner.__new__(NegotiationRunner)
        r._token_counts = {"sonnet_input": 1_000_000, "sonnet_output": 1_000_000,
                           "haiku_input": 0, "haiku_output": 0}
        assert r.token_summary["estimated_cost_usd"] == 18.0

    def test_haiku_pricing(self):
        # 1M haiku_input @ $0.25 + 1M haiku_output @ $1.25 = $1.50
        r = NegotiationRunner.__new__(NegotiationRunner)
        r._token_counts = {"sonnet_input": 0, "sonnet_output": 0,
                           "haiku_input": 1_000_000, "haiku_output": 1_000_000}
        assert r.token_summary["estimated_cost_usd"] == 1.5

    def test_combined_pricing_rounded_to_4dp(self):
        r = NegotiationRunner.__new__(NegotiationRunner)
        r._token_counts = {"sonnet_input": 100_000, "sonnet_output": 50_000,
                           "haiku_input": 200_000, "haiku_output": 100_000}
        # 0.3 + 0.75 + 0.05 + 0.125 = 1.225
        assert r.token_summary["estimated_cost_usd"] == 1.225

    def test_summary_includes_raw_counts(self):
        r = NegotiationRunner.__new__(NegotiationRunner)
        r._token_counts = {"sonnet_input": 100, "sonnet_output": 200,
                           "haiku_input": 300, "haiku_output": 400}
        s = r.token_summary
        assert s["sonnet_input"] == 100
        assert s["sonnet_output"] == 200
        assert s["haiku_input"] == 300
        assert s["haiku_output"] == 400


# ---------- _debate_archetype_batch response parsing ----------

def _make_runner_for_debate():
    """Build a NegotiationRunner without invoking __init__'s anthropic client."""
    r = NegotiationRunner.__new__(NegotiationRunner)
    r._token_counts = {"sonnet_input": 0, "sonnet_output": 0,
                       "haiku_input": 0, "haiku_output": 0}
    r._outlier_map = {}
    r._seed_context = "(test)"
    r._date_context = ""
    r.num_rounds = 3
    r.client = MagicMock()
    return r


def _build_anthropic_response(text: str, input_tokens=10, output_tokens=20):
    block = MagicMock()
    block.text = text
    msg = MagicMock()
    msg.content = [block]
    msg.usage.input_tokens = input_tokens
    msg.usage.output_tokens = output_tokens
    return msg


def _summary():
    return RoundSummary(
        round_number=1, mean_probability=0.5, median_probability=0.5,
        std_dev=0.1, min_probability=0.3, max_probability=0.7,
        bull_count=1, bear_count=1, neutral_count=1, narrative="opening",
    )


class TestDebateArchetypeBatch:
    @pytest.mark.asyncio
    async def test_parses_well_formed_response(self):
        agents = [_agent(0.5) for _ in range(2)]
        agents[0].id = "agent-1"
        agents[1].id = "agent-2"
        payload = json.dumps([
            {"agent_id": "agent-1", "probability": 0.7,
             "reasoning": "bull case", "conviction_delta": 0.1},
            {"agent_id": "agent-2", "probability": 0.3,
             "reasoning": "bear case", "conviction_delta": -0.05},
        ])
        runner = _make_runner_for_debate()
        runner.client.messages.create = AsyncMock(
            return_value=_build_anthropic_response(payload, 100, 50)
        )
        results = await runner._debate_archetype_batch(
            "BHP", 1, _summary(), "value", agents,
        )
        assert len(results) == 2
        assert results[0].probability == 0.7
        assert results[0].agent_id == "agent-1"
        assert results[1].conviction_delta == -0.05
        # Token accumulation
        assert runner._token_counts["sonnet_input"] == 100
        assert runner._token_counts["sonnet_output"] == 50

    @pytest.mark.asyncio
    async def test_clamps_probability_to_unit_interval(self):
        agents = [_agent(0.5)]
        agents[0].id = "agent-1"
        payload = json.dumps([
            {"agent_id": "agent-1", "probability": 1.5,
             "reasoning": "x", "conviction_delta": 0.0},
        ])
        runner = _make_runner_for_debate()
        runner.client.messages.create = AsyncMock(
            return_value=_build_anthropic_response(payload)
        )
        results = await runner._debate_archetype_batch("BHP", 1, _summary(), "value", agents)
        assert results[0].probability == 1.0

    @pytest.mark.asyncio
    async def test_clamps_negative_probability(self):
        agents = [_agent(0.5)]
        agents[0].id = "agent-1"
        payload = json.dumps([
            {"agent_id": "agent-1", "probability": -0.3,
             "reasoning": "x", "conviction_delta": 0.0},
        ])
        runner = _make_runner_for_debate()
        runner.client.messages.create = AsyncMock(
            return_value=_build_anthropic_response(payload)
        )
        results = await runner._debate_archetype_batch("BHP", 1, _summary(), "value", agents)
        assert results[0].probability == 0.0

    @pytest.mark.asyncio
    async def test_clamps_conviction_delta_to_pm_0_2(self):
        agents = [_agent(0.5)]
        agents[0].id = "agent-1"
        payload = json.dumps([
            {"agent_id": "agent-1", "probability": 0.5,
             "reasoning": "x", "conviction_delta": 0.9},
        ])
        runner = _make_runner_for_debate()
        runner.client.messages.create = AsyncMock(
            return_value=_build_anthropic_response(payload)
        )
        results = await runner._debate_archetype_batch("BHP", 1, _summary(), "value", agents)
        assert results[0].conviction_delta == 0.2

    @pytest.mark.asyncio
    async def test_filters_unknown_agent_ids(self):
        agents = [_agent(0.5)]
        agents[0].id = "agent-1"
        payload = json.dumps([
            {"agent_id": "agent-1", "probability": 0.5,
             "reasoning": "ok", "conviction_delta": 0.0},
            {"agent_id": "agent-9999", "probability": 0.5,
             "reasoning": "stranger", "conviction_delta": 0.0},
        ])
        runner = _make_runner_for_debate()
        runner.client.messages.create = AsyncMock(
            return_value=_build_anthropic_response(payload)
        )
        results = await runner._debate_archetype_batch("BHP", 1, _summary(), "value", agents)
        assert len(results) == 1
        assert results[0].agent_id == "agent-1"

    @pytest.mark.asyncio
    async def test_skips_malformed_items(self):
        agents = [_agent(0.5), _agent(0.5)]
        agents[0].id = "agent-1"
        agents[1].id = "agent-2"
        payload = json.dumps([
            {"agent_id": "agent-1", "probability": 0.5,
             "reasoning": "ok", "conviction_delta": 0.0},
            {"agent_id": "agent-2"},  # missing required fields
            {"agent_id": "agent-1", "probability": "not-a-float",
             "reasoning": "x", "conviction_delta": 0.0},
        ])
        runner = _make_runner_for_debate()
        runner.client.messages.create = AsyncMock(
            return_value=_build_anthropic_response(payload)
        )
        results = await runner._debate_archetype_batch("BHP", 1, _summary(), "value", agents)
        assert len(results) == 1
        assert results[0].agent_id == "agent-1"

    @pytest.mark.asyncio
    async def test_unparseable_response_returns_empty(self):
        agents = [_agent(0.5)]
        agents[0].id = "agent-1"
        runner = _make_runner_for_debate()
        runner.client.messages.create = AsyncMock(
            return_value=_build_anthropic_response("absolute garbage")
        )
        results = await runner._debate_archetype_batch("BHP", 1, _summary(), "value", agents)
        assert results == []

    @pytest.mark.asyncio
    async def test_markdown_wrapped_response_parsed(self):
        agents = [_agent(0.5)]
        agents[0].id = "agent-1"
        payload = json.dumps([
            {"agent_id": "agent-1", "probability": 0.6,
             "reasoning": "ok", "conviction_delta": 0.0},
        ])
        runner = _make_runner_for_debate()
        runner.client.messages.create = AsyncMock(
            return_value=_build_anthropic_response(f"```json\n{payload}\n```")
        )
        results = await runner._debate_archetype_batch("BHP", 1, _summary(), "value", agents)
        assert len(results) == 1
        assert results[0].probability == 0.6
