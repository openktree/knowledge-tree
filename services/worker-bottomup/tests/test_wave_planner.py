"""Tests for wave planner: parsing, subdivision, and budget utilization."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

# Import directly from submodules to avoid bottom_up/__init__.py
# which triggers get_hatchet() at module level.
from kt_worker_bottomup.bottom_up.state import ScopePlan
from kt_worker_bottomup.bottom_up.wave_planner import (
    MAX_SCOPE_EXPLORE,
    WavePlanParseError,
    parse_scope_plans,
    subdivide_large_scopes_with_llm,
    subdivide_scopes,
)

# ── parse_scope_plans ───────────────────────────────────────────


def test_parse_scope_plans_basic() -> None:
    raw = json.dumps(
        [
            {"scope": "scope A", "explore_budget": 4, "nav_budget": 20},
            {"scope": "scope B", "explore_budget": 3, "nav_budget": 15},
        ]
    )
    plans = parse_scope_plans(raw, wave_explore=10, wave_nav=50)
    assert len(plans) == 2
    assert plans[0].explore_budget == 4
    assert plans[1].explore_budget == 3


def test_parse_scope_plans_no_hard_cap() -> None:
    """After removing the hard cap of 5, scopes >5 should pass through."""
    raw = json.dumps(
        [
            {"scope": "big scope", "explore_budget": 30, "nav_budget": 100},
        ]
    )
    plans = parse_scope_plans(raw, wave_explore=50, wave_nav=200)
    assert len(plans) == 1
    assert plans[0].explore_budget == 30  # Not capped to 5


def test_parse_scope_plans_caps_at_remaining() -> None:
    raw = json.dumps(
        [
            {"scope": "scope A", "explore_budget": 100, "nav_budget": 500},
        ]
    )
    plans = parse_scope_plans(raw, wave_explore=20, wave_nav=100)
    assert plans[0].explore_budget == 20  # Capped at remaining


def test_parse_scope_plans_stops_when_exhausted() -> None:
    raw = json.dumps(
        [
            {"scope": "scope A", "explore_budget": 5, "nav_budget": 25},
            {"scope": "scope B", "explore_budget": 5, "nav_budget": 25},
            {"scope": "scope C", "explore_budget": 5, "nav_budget": 0},
        ]
    )
    # wave_explore=10 means scope C gets 0 explore and 0 nav -> break
    plans = parse_scope_plans(raw, wave_explore=10, wave_nav=50)
    assert len(plans) == 2


def test_parse_scope_plans_invalid_json() -> None:
    with pytest.raises(WavePlanParseError, match="Invalid JSON"):
        parse_scope_plans("not json", wave_explore=10, wave_nav=50)


def test_parse_scope_plans_no_valid_entries() -> None:
    raw = json.dumps([{"no_scope_key": True}])
    with pytest.raises(WavePlanParseError, match="No valid scope plans"):
        parse_scope_plans(raw, wave_explore=10, wave_nav=50)


# ── subdivide_scopes ────────────────────────────────────────────


def test_subdivide_scopes_small_passthrough() -> None:
    """Scopes at or below MAX_SCOPE_EXPLORE pass through unchanged."""
    scopes = [
        ScopePlan(scope="small", explore_budget=3, nav_budget=15),
        ScopePlan(scope="exact", explore_budget=MAX_SCOPE_EXPLORE, nav_budget=25),
    ]
    result = subdivide_scopes(scopes, wave_explore=8, wave_nav=40)
    assert len(result) == 2
    assert result[0].scope == "small"
    assert result[0].explore_budget == 3
    assert result[1].scope == "exact"
    assert result[1].explore_budget == MAX_SCOPE_EXPLORE


def test_subdivide_scopes_basic() -> None:
    """A scope with explore=20 splits into 4 sub-scopes of 5."""
    scopes = [ScopePlan(scope="big topic", explore_budget=20, nav_budget=100)]
    result = subdivide_scopes(scopes, wave_explore=20, wave_nav=100)
    assert len(result) == 4
    for i, s in enumerate(result):
        assert s.explore_budget == 5
        assert s.nav_budget == 25
        assert f"(part {i + 1}/4)" in s.scope


def test_subdivide_scopes_uneven() -> None:
    """A scope with explore=7 splits into 5 + 2."""
    scopes = [ScopePlan(scope="medium", explore_budget=7, nav_budget=35)]
    result = subdivide_scopes(scopes, wave_explore=7, wave_nav=35)
    assert len(result) == 2
    assert result[0].explore_budget == 5
    assert result[1].explore_budget == 2


def test_subdivide_scopes_respects_remaining() -> None:
    """Subdivision stops when wave budget is exhausted."""
    scopes = [ScopePlan(scope="huge", explore_budget=50, nav_budget=250)]
    result = subdivide_scopes(scopes, wave_explore=12, wave_nav=60)
    total_explore = sum(s.explore_budget for s in result)
    assert total_explore == 12  # Capped at wave budget


def test_subdivide_scopes_multiple() -> None:
    """Multiple scopes, some need subdivision."""
    scopes = [
        ScopePlan(scope="small", explore_budget=3, nav_budget=15),
        ScopePlan(scope="big", explore_budget=12, nav_budget=60),
    ]
    result = subdivide_scopes(scopes, wave_explore=15, wave_nav=75)
    assert result[0].scope == "small"
    assert result[0].explore_budget == 3
    # The big scope (12) splits into ceil(12/5) = 3 parts
    big_parts = [s for s in result if "big" in s.scope and "part" in s.scope]
    assert len(big_parts) == 3
    assert big_parts[0].explore_budget == 5
    assert big_parts[1].explore_budget == 5
    assert big_parts[2].explore_budget == 2


# ── subdivide_large_scopes_with_llm ────────────────────────────


@pytest.mark.asyncio
async def test_subdivide_large_llm_calls_for_big_scopes() -> None:
    """Scopes above SUBDIVISION_THRESHOLD trigger LLM subdivision."""
    scopes = [ScopePlan(scope="huge topic", explore_budget=30, nav_budget=150)]

    sub_scopes_json = json.dumps(
        [
            {"scope": "sub-angle 1", "explore_budget": 5, "nav_budget": 25},
            {"scope": "sub-angle 2", "explore_budget": 5, "nav_budget": 25},
            {"scope": "sub-angle 3", "explore_budget": 5, "nav_budget": 25},
            {"scope": "sub-angle 4", "explore_budget": 5, "nav_budget": 25},
            {"scope": "sub-angle 5", "explore_budget": 5, "nav_budget": 25},
            {"scope": "sub-angle 6", "explore_budget": 5, "nav_budget": 25},
        ]
    )
    agent_ctx = MagicMock()
    agent_ctx.model_gateway.generate = AsyncMock(return_value=sub_scopes_json)
    agent_ctx.model_gateway.orchestrator_model = "test-model"

    result = await subdivide_large_scopes_with_llm(scopes, 30, 150, agent_ctx)
    assert len(result) == 6
    assert all(s.explore_budget <= MAX_SCOPE_EXPLORE for s in result)
    assert sum(s.explore_budget for s in result) == 30
    agent_ctx.model_gateway.generate.assert_called_once()


@pytest.mark.asyncio
async def test_subdivide_large_llm_fallback_on_failure() -> None:
    """When LLM fails, falls back to mechanical subdivision."""
    scopes = [ScopePlan(scope="huge topic", explore_budget=25, nav_budget=125)]

    agent_ctx = MagicMock()
    agent_ctx.model_gateway.generate = AsyncMock(side_effect=RuntimeError("LLM down"))
    agent_ctx.model_gateway.orchestrator_model = "test-model"

    result = await subdivide_large_scopes_with_llm(scopes, 25, 125, agent_ctx)
    # Falls back to mechanical: ceil(25/5) = 5 parts
    assert len(result) == 5
    assert all(s.explore_budget <= MAX_SCOPE_EXPLORE for s in result)
    assert sum(s.explore_budget for s in result) == 25


@pytest.mark.asyncio
async def test_subdivide_large_passes_small_through() -> None:
    """Scopes below SUBDIVISION_THRESHOLD go to mechanical subdivision only."""
    scopes = [
        ScopePlan(scope="small", explore_budget=4, nav_budget=20),
        ScopePlan(scope="medium", explore_budget=10, nav_budget=50),
    ]

    agent_ctx = MagicMock()
    agent_ctx.model_gateway.generate = AsyncMock()

    result = await subdivide_large_scopes_with_llm(scopes, 14, 70, agent_ctx)
    # small (4) passes through, medium (10) gets mechanically split into 5+5
    assert len(result) == 3
    assert result[0].scope == "small"
    assert result[0].explore_budget == 4
    # LLM should NOT have been called (both below threshold)
    agent_ctx.model_gateway.generate.assert_not_called()


# ── _plan_wave utilization retry ────────────────────────────────


@pytest.mark.asyncio
async def test_plan_wave_retries_on_low_utilization(monkeypatch) -> None:
    """_plan_wave retries with a hint when the LLM underutilizes the budget."""
    monkeypatch.setattr("kt_worker_bottomup.shared.asyncio.sleep", AsyncMock())
    from kt_worker_bottomup.shared import _plan_wave

    wave_explore = 60
    wave_nav = 0

    # First call: LLM returns 3 scopes of 5 (15/60 = 25% < 70%)
    underutilized = json.dumps(
        [
            {"scope": "scope A", "explore_budget": 5, "nav_budget": 0},
            {"scope": "scope B", "explore_budget": 5, "nav_budget": 0},
            {"scope": "scope C", "explore_budget": 5, "nav_budget": 0},
        ]
    )
    # Second call: LLM returns 12 scopes of 5 (60/60 = 100%)
    full_plan = json.dumps([{"scope": f"scope {i}", "explore_budget": 5, "nav_budget": 0} for i in range(12)])

    agent_ctx = MagicMock()
    agent_ctx.model_gateway.generate = AsyncMock(side_effect=[underutilized, full_plan])
    agent_ctx.model_gateway.orchestrator_model = "test-model"
    agent_ctx.model_gateway.orchestrator_thinking_level = ""

    scopes = await _plan_wave(
        query="test query",
        wave=1,
        total_waves=1,
        briefings=[],
        wave_explore=wave_explore,
        wave_nav=wave_nav,
        scout_results={},
        agent_ctx=agent_ctx,
    )

    # Should have called generate twice (first underutilized, then retry)
    assert agent_ctx.model_gateway.generate.call_count == 2

    # Second call's user message should contain the hint
    second_call_args = agent_ctx.model_gateway.generate.call_args_list[1]
    second_user_msg = second_call_args[1]["messages"][1]["content"]
    assert "IMPORTANT" in second_user_msg
    assert "only used 15" in second_user_msg

    # Result should be 12 scopes of 5 each
    assert len(scopes) == 12
    assert sum(s.explore_budget for s in scopes) == 60
