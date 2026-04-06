"""Unit tests for negotiation_runner.moderator — pure helpers + mocked Anthropic client."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from negotiation_runner.models import AgentState, RoundResult
from negotiation_runner.moderator import (
    ModeratorAgent,
    ModeratorOutput,
    _extract_partial_json,
)


# ---------- _extract_partial_json ----------

class TestExtractPartialJson:
    def test_truncated_recovers_with_closing_brace(self):
        text = '{"bull_arguments": ["a", "b"], "bear_arguments": ["c"]'
        out = _extract_partial_json(text)
        assert out is not None
        assert out["bull_arguments"] == ["a", "b"]
        assert out["bear_arguments"] == ["c"]

    def test_truncated_mid_array_recovered_via_closing_brace(self):
        # Closing-brace strategy "]}" closes the dangling array + object
        text = '{"bull_arguments": ["a", "b", "c"], "bear_arguments": ["x"'
        out = _extract_partial_json(text)
        assert out is not None
        assert out.get("bull_arguments") == ["a", "b", "c"]
        assert out.get("bear_arguments") == ["x"]

    def test_string_field_extracted(self):
        text = '{"outlier_challenge": "be specific", "broken'
        out = _extract_partial_json(text)
        assert out is not None
        assert out.get("outlier_challenge") == "be specific"

    def test_completely_garbage_returns_none(self):
        assert _extract_partial_json("totally not json at all") is None

    def test_empty_input_returns_none(self):
        assert _extract_partial_json("") is None


# ---------- helpers for moderate() ----------

def _agent(idx: int, conviction=0.7, archetype="value"):
    return AgentState(
        id=f"agent-{idx}",
        simulation_id="sim-1",
        archetype=archetype,
        name=f"Agent {idx}",
        goals="g", methodology="m", known_biases="b",
        conviction_threshold=0.5, risk_tolerance=0.5,
        initial_probability=0.5, current_probability=0.5,
        conviction=conviction,
    )


def _result(idx: int, probability: float, reasoning="reasoning text"):
    return RoundResult(
        agent_id=f"agent-{idx}",
        round_number=1,
        probability=probability,
        reasoning=reasoning,
        conviction_delta=0.0,
    )


def _build_anthropic_response(text: str):
    """Build a fake anthropic messages.create() response."""
    block = MagicMock()
    block.text = text
    msg = MagicMock()
    msg.content = [block]
    return msg


def _make_client(response_text: str = None, raises: Exception = None):
    client = MagicMock()
    if raises is not None:
        client.messages.create = AsyncMock(side_effect=raises)
    else:
        client.messages.create = AsyncMock(
            return_value=_build_anthropic_response(response_text)
        )
    return client


GOOD_MODERATION = json.dumps({
    "bull_arguments": ["strong PCP comp", "guidance raised", "share buyback"],
    "bear_arguments": ["china drag", "fx headwind", "iron ore -8%"],
    "swing_factors": ["iron ore price", "china steel", "fx aud/usd"],
    "outlier_agent_ids": ["agent-7"],
    "outlier_challenge": "Justify your low conviction extreme view.",
    "dissent_agent_ids": ["agent-3"],
    "dissent_summary": "Lone bear with strong evidence on china demand.",
})


# ---------- ModeratorAgent.moderate ----------

class TestModerate:
    @pytest.mark.asyncio
    async def test_empty_inputs_returns_skeleton(self):
        client = _make_client(GOOD_MODERATION)
        mod = ModeratorAgent(client)
        out = await mod.moderate("BHP", 1, [], [])
        assert isinstance(out, ModeratorOutput)
        assert out.round_number == 1
        assert out.bull_arguments == []
        assert out.bear_arguments == []
        # LLM never called
        client.messages.create.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_happy_path_populates_all_fields(self):
        client = _make_client(GOOD_MODERATION)
        mod = ModeratorAgent(client)
        agents = [_agent(i) for i in range(5)]
        results = [_result(i, p) for i, p in enumerate([0.3, 0.5, 0.7, 0.55, 0.45])]

        out = await mod.moderate("BHP", 2, agents, results)

        assert out.round_number == 2
        assert len(out.bull_arguments) == 3
        assert len(out.bear_arguments) == 3
        assert out.outlier_agent_ids == ["agent-7"]
        assert out.outlier_challenge == "Justify your low conviction extreme view."
        assert out.dissent_agent_ids == ["agent-3"]
        assert "Lone bear" in out.dissent_summary
        assert "MODERATOR BRIEF" in out.moderator_brief
        assert "strong PCP comp" in out.moderator_brief
        assert "iron ore -8%" in out.moderator_brief

    @pytest.mark.asyncio
    async def test_brief_includes_dissent_section_when_present(self):
        client = _make_client(GOOD_MODERATION)
        mod = ModeratorAgent(client)
        out = await mod.moderate("BHP", 1, [_agent(0)], [_result(0, 0.5)])
        assert "HIGH-CONVICTION MINORITY VIEW" in out.moderator_brief
        assert "Lone bear" in out.moderator_brief

    @pytest.mark.asyncio
    async def test_brief_omits_dissent_section_when_absent(self):
        payload = json.dumps({
            "bull_arguments": ["a"], "bear_arguments": ["b"],
            "swing_factors": ["s"],
            "outlier_agent_ids": [], "outlier_challenge": None,
            "dissent_agent_ids": [], "dissent_summary": "",
        })
        client = _make_client(payload)
        mod = ModeratorAgent(client)
        out = await mod.moderate("BHP", 1, [_agent(0)], [_result(0, 0.5)])
        assert "HIGH-CONVICTION MINORITY VIEW" not in out.moderator_brief

    @pytest.mark.asyncio
    async def test_swing_factors_accumulate_across_rounds(self):
        client = _make_client(GOOD_MODERATION)
        mod = ModeratorAgent(client)
        await mod.moderate("BHP", 1, [_agent(0)], [_result(0, 0.5)])
        await mod.moderate("BHP", 2, [_agent(0)], [_result(0, 0.5)])
        await mod.moderate("BHP", 3, [_agent(0)], [_result(0, 0.5)])
        # Three calls, each contributing the same 3 swing factors → counts of 3 each
        finals = mod.get_final_swing_factors()
        assert len(finals) == 3
        assert all(mod._swing_factor_counts[k] == 3 for k in finals)

    @pytest.mark.asyncio
    async def test_swing_factors_sorted_by_frequency(self):
        mod = ModeratorAgent(_make_client(GOOD_MODERATION))
        # Hand-seed the accumulator
        mod._swing_factor_counts = {"a": 1, "b": 5, "c": 3, "d": 10, "e": 2}
        finals = mod.get_final_swing_factors()
        assert finals == ["d", "b", "c", "e", "a"]

    @pytest.mark.asyncio
    async def test_handles_markdown_fences_in_response(self):
        wrapped = f"```json\n{GOOD_MODERATION}\n```"
        client = _make_client(wrapped)
        mod = ModeratorAgent(client)
        out = await mod.moderate("BHP", 1, [_agent(0)], [_result(0, 0.5)])
        assert len(out.bull_arguments) == 3

    @pytest.mark.asyncio
    async def test_truncated_json_recovered_via_partial_parse(self):
        # Truncated mid-second-array — closing-brace strategy "]}" recovers it
        truncated = '{"bull_arguments": ["a", "b", "c"], "bear_arguments": ["x"'
        client = _make_client(truncated)
        mod = ModeratorAgent(client)
        out = await mod.moderate("BHP", 1, [_agent(0)], [_result(0, 0.5)])
        # Partial recovery succeeded
        assert out.bull_arguments == ["a", "b", "c"]
        assert out.bear_arguments == ["x"]

    @pytest.mark.asyncio
    async def test_unrecoverable_json_returns_skeleton(self):
        client = _make_client("complete garbage no json")
        mod = ModeratorAgent(client)
        out = await mod.moderate("BHP", 1, [_agent(0)], [_result(0, 0.5)])
        assert out.round_number == 1
        assert out.bull_arguments == []
        assert out.moderator_brief == ""

    @pytest.mark.asyncio
    async def test_anthropic_exception_returns_skeleton(self):
        client = _make_client(raises=RuntimeError("rate limited"))
        mod = ModeratorAgent(client)
        out = await mod.moderate("BHP", 1, [_agent(0)], [_result(0, 0.5)])
        assert out.round_number == 1
        assert out.bull_arguments == []

    @pytest.mark.asyncio
    async def test_prompt_carries_distribution_stats(self):
        client = _make_client(GOOD_MODERATION)
        mod = ModeratorAgent(client)
        # 3 bulls (>0.6), 2 bears (<0.4), 1 neutral
        results = [
            _result(0, 0.8), _result(1, 0.7), _result(2, 0.65),
            _result(3, 0.5),
            _result(4, 0.3), _result(5, 0.2),
        ]
        agents = [_agent(i) for i in range(6)]
        await mod.moderate("BHP", 1, agents, results)
        prompt = client.messages.create.await_args.kwargs["messages"][0]["content"]
        assert "Bulls=3" in prompt
        assert "Bears=2" in prompt
        assert "Neutral=1" in prompt
        assert "BHP" in prompt

    @pytest.mark.asyncio
    async def test_reasoning_truncated_to_150_chars(self):
        client = _make_client(GOOD_MODERATION)
        mod = ModeratorAgent(client)
        long_reasoning = "x" * 500
        await mod.moderate("BHP", 1, [_agent(0)], [_result(0, 0.5, long_reasoning)])
        prompt = client.messages.create.await_args.kwargs["messages"][0]["content"]
        # 150 x's appear, but not 200
        assert "x" * 150 in prompt
        assert "x" * 200 not in prompt
