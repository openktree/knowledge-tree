"""Tests for bottom-up exploration pipeline: perspectives, queries, prioritization."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from kt_worker_orchestrator.bottom_up.state import BottomUpScopePlan

# ── Plan dataclass tests ─────────────────────────────────────


def test_scope_plan_dataclass() -> None:
    plan = BottomUpScopePlan(
        node_plans=[{"name": "x", "node_type": "concept"}],
        perspective_plans=[],
        explore_used=3,
        gathered_fact_count=15,
        extracted_count=5,
        content_summary="Summary text",
    )
    assert len(plan.node_plans) == 1
    assert plan.extracted_count == 5
    assert plan.content_summary == "Summary text"


def test_scope_plan_defaults() -> None:
    plan = BottomUpScopePlan()
    assert plan.node_plans == []
    assert plan.perspective_plans == []
    assert plan.explore_used == 0
    assert plan.gathered_fact_count == 0
    assert plan.extracted_count == 0
    assert plan.content_summary == ""


# ── Scout-and-build query generation tests ───────────────────


@pytest.mark.asyncio
async def test_scout_and_build_queries_basic() -> None:
    from kt_worker_orchestrator.bottom_up.scope import _scout_and_build_queries

    ctx = MagicMock()

    with patch(
        "kt_worker_orchestrator.agents.tools.scout.scout_impl",
        new_callable=AsyncMock,
        return_value={
            "CRISPR": {
                "external": [
                    {"title": "Gene therapy advances"},
                    {"title": "CRISPR clinical trials"},
                ],
                "graph_matches": [
                    {"concept": "Cas9"},
                ],
            },
        },
    ):
        queries = await _scout_and_build_queries("CRISPR", 5, ctx)

    assert queries[0] == "CRISPR"
    assert "CRISPR Gene therapy advances" in queries
    assert "CRISPR Cas9" in queries
    assert len(queries) <= 5


@pytest.mark.asyncio
async def test_scout_and_build_queries_caps_to_budget() -> None:
    from kt_worker_orchestrator.bottom_up.scope import _scout_and_build_queries

    ctx = MagicMock()

    with patch(
        "kt_worker_orchestrator.agents.tools.scout.scout_impl",
        new_callable=AsyncMock,
        return_value={
            "CRISPR": {
                "external": [{"title": f"title_{i}"} for i in range(10)],
                "graph_matches": [],
            },
        },
    ):
        queries = await _scout_and_build_queries("CRISPR", 2, ctx)

    assert len(queries) == 2


@pytest.mark.asyncio
async def test_scout_and_build_queries_fallback_on_error() -> None:
    from kt_worker_orchestrator.bottom_up.scope import _scout_and_build_queries

    ctx = MagicMock()

    with patch(
        "kt_worker_orchestrator.agents.tools.scout.scout_impl",
        new_callable=AsyncMock,
        side_effect=Exception("scout failed"),
    ):
        queries = await _scout_and_build_queries("CRISPR", 3, ctx)

    assert queries[0] == "CRISPR"
    assert len(queries) == 3


@pytest.mark.asyncio
async def test_scout_and_build_queries_no_duplicate_scope() -> None:
    from kt_worker_orchestrator.bottom_up.scope import _scout_and_build_queries

    ctx = MagicMock()

    with patch(
        "kt_worker_orchestrator.agents.tools.scout.scout_impl",
        new_callable=AsyncMock,
        return_value={
            "CRISPR": {
                "external": [],
                "graph_matches": [{"concept": "CRISPR"}],
            },
        },
    ):
        queries = await _scout_and_build_queries("CRISPR", 3, ctx)

    # Should not duplicate scope description as graph match concept
    assert queries.count("CRISPR") == 1


# ── Perspective planner tests ────────────────────────────────


@pytest.mark.asyncio
async def test_plan_perspectives_basic() -> None:
    from kt_worker_orchestrator.bottom_up.scope import plan_perspectives

    ctx = MagicMock()
    ctx.model_gateway = MagicMock()
    ctx.model_gateway.orchestrator_model = "test-model"
    ctx.model_gateway.generate_with_tools = AsyncMock(
        return_value=[
            {
                "name": "propose_perspective",
                "arguments": {
                    "claim": "Germline editing prevents suffering",
                    "antithesis": "Germline editing crosses ethical boundaries",
                    "source_concept": "germline editing",
                },
            },
        ]
    )

    built_nodes = [
        {"concept": "CRISPR-Cas9", "node_type": "concept"},
        {"concept": "germline editing", "node_type": "concept"},
    ]

    result = await plan_perspectives(ctx, "CRISPR", built_nodes)
    assert len(result) == 1
    assert result[0]["claim"] == "Germline editing prevents suffering"
    assert result[0]["source_concept_id"] == "germline editing"


@pytest.mark.asyncio
async def test_plan_perspectives_empty_nodes() -> None:
    from kt_worker_orchestrator.bottom_up.scope import plan_perspectives

    ctx = MagicMock()
    result = await plan_perspectives(ctx, "test", [])
    assert result == []


@pytest.mark.asyncio
async def test_plan_perspectives_caps_to_max() -> None:
    from kt_worker_orchestrator.bottom_up.scope import plan_perspectives

    ctx = MagicMock()
    ctx.model_gateway = MagicMock()
    ctx.model_gateway.orchestrator_model = "test-model"
    ctx.model_gateway.generate_with_tools = AsyncMock(
        return_value=[
            {
                "name": "propose_perspective",
                "arguments": {"claim": f"claim {i}", "antithesis": f"anti {i}", "source_concept": "x"},
            }
            for i in range(10)
        ]
    )

    result = await plan_perspectives(
        ctx,
        "test",
        [{"concept": "x", "node_type": "concept"}],
        max_perspectives=3,
    )
    assert len(result) == 3


@pytest.mark.asyncio
async def test_plan_perspectives_handles_error() -> None:
    from kt_worker_orchestrator.bottom_up.scope import plan_perspectives

    ctx = MagicMock()
    ctx.model_gateway = MagicMock()
    ctx.model_gateway.orchestrator_model = "test-model"
    ctx.model_gateway.generate_with_tools = AsyncMock(side_effect=Exception("LLM error"))

    result = await plan_perspectives(
        ctx,
        "test",
        [{"concept": "x", "node_type": "concept"}],
    )
    assert result == []


@pytest.mark.asyncio
async def test_plan_perspectives_filters_incomplete() -> None:
    from kt_worker_orchestrator.bottom_up.scope import plan_perspectives

    ctx = MagicMock()
    ctx.model_gateway = MagicMock()
    ctx.model_gateway.orchestrator_model = "test-model"
    ctx.model_gateway.generate_with_tools = AsyncMock(
        return_value=[
            {
                "name": "propose_perspective",
                "arguments": {"claim": "good claim", "antithesis": "good anti", "source_concept": "x"},
            },
            {
                "name": "propose_perspective",
                "arguments": {"claim": "no antithesis", "source_concept": "x"},
            },  # missing antithesis
            {
                "name": "propose_perspective",
                "arguments": {"claim": "", "antithesis": "empty claim", "source_concept": "x"},
            },  # empty claim
        ]
    )

    result = await plan_perspectives(
        ctx,
        "test",
        [{"concept": "x", "node_type": "concept"}],
    )
    assert len(result) == 1
    assert result[0]["claim"] == "good claim"


# ── Pipeline integration test ────────────────────────────────


@pytest.mark.asyncio
async def test_run_pipeline_basic_flow() -> None:
    """run_bottom_up_scope_pipeline gathers and returns all extracted nodes as plans."""
    from kt_worker_orchestrator.bottom_up.scope import run_bottom_up_scope_pipeline

    ctx = MagicMock()
    ctx.model_gateway = MagicMock()

    mock_gather_result = {
        "queries_executed": 2,
        "facts_gathered": 10,
        "explore_used": 2,
        "explore_remaining": 0,
        "content_summary": "Summary of facts.",
        "extracted_nodes": [
            {"name": "CRISPR-Cas9", "node_type": "concept"},
            {"name": "biology", "node_type": "concept"},
            {"name": "Jennifer Doudna", "node_type": "entity"},
        ],
    }

    with (
        patch(
            "kt_worker_nodes.pipelines.gathering.GatherFactsPipeline",
        ) as mock_cls,
        patch(
            "kt_worker_orchestrator.agents.tools.scout.scout_impl",
            new_callable=AsyncMock,
            return_value={"CRISPR gene editing": {"external": [], "graph_matches": []}},
        ),
    ):
        mock_pipeline = MagicMock()
        mock_pipeline.gather = AsyncMock(return_value=mock_gather_result)
        mock_cls.return_value = mock_pipeline

        plan = await run_bottom_up_scope_pipeline(
            ctx,
            scope_description="CRISPR gene editing",
            explore_slice=2,
        )

        # Verify gather was called with extraction enabled
        call_kwargs = mock_pipeline.gather.call_args
        assert call_kwargs.kwargs.get("enable_extraction") is True
        assert call_kwargs.kwargs.get("enable_summary") is True

    # All extracted nodes passed through (no LLM filter — seeds handle dedup)
    assert len(plan.node_plans) == 3
    names = [n["name"] for n in plan.node_plans]
    assert "CRISPR-Cas9" in names
    assert "Jennifer Doudna" in names
    assert "biology" in names
    assert plan.content_summary == "Summary of facts."
    assert plan.extracted_count == 3


@pytest.mark.asyncio
async def test_run_pipeline_builds_all_extracted_nodes() -> None:
    """run_bottom_up_scope_pipeline returns all extracted nodes (no cap)."""
    from kt_worker_orchestrator.bottom_up.scope import run_bottom_up_scope_pipeline

    ctx = MagicMock()
    ctx.model_gateway = MagicMock()

    extracted = [{"name": f"node_{i}", "node_type": "concept"} for i in range(50)]

    with (
        patch(
            "kt_worker_nodes.pipelines.gathering.GatherFactsPipeline",
        ) as mock_cls,
        patch(
            "kt_worker_orchestrator.agents.tools.scout.scout_impl",
            new_callable=AsyncMock,
            return_value={"test": {"external": [], "graph_matches": []}},
        ),
    ):
        mock_pipeline = MagicMock()
        mock_pipeline.gather = AsyncMock(
            return_value={
                "queries_executed": 1,
                "facts_gathered": 10,
                "explore_used": 1,
                "explore_remaining": 0,
                "extracted_nodes": extracted,
            }
        )
        mock_cls.return_value = mock_pipeline

        plan = await run_bottom_up_scope_pipeline(
            ctx,
            scope_description="test",
            explore_slice=1,
        )

    # All 50 nodes should be included (no nav cap)
    assert len(plan.node_plans) == 50


# ── Workflow threshold test ──────────────────────────────────


def test_wave_threshold() -> None:
    from kt_worker_orchestrator.bottom_up.workflow import _WAVE_THRESHOLD

    assert _WAVE_THRESHOLD == 5


# ── Agent-assisted selection tests ────────────────────────────


@pytest.mark.asyncio
async def test_agent_select_basic() -> None:
    """agent_select_nodes selects nodes via tool calls."""
    from kt_hatchet.models import ProposedNode
    from kt_worker_orchestrator.bottom_up.agent_select import agent_select_nodes

    ctx = MagicMock()
    ctx.model_gateway = MagicMock()
    ctx.model_gateway.orchestrator_model = "test-model"
    ctx.model_gateway.generate_with_tools = AsyncMock(
        return_value=[
            {"name": "select_nodes", "arguments": {"indices": [0, 2]}},
        ]
    )

    nodes = [
        ProposedNode(name="Quantum Computing", node_type="concept", priority=9),
        ProposedNode(name="science", node_type="concept", priority=3),
        ProposedNode(name="Shor's Algorithm", node_type="concept", priority=8),
    ]

    result = await agent_select_nodes(ctx, nodes, max_select=2, instructions="quantum")

    assert len(result) == 3
    assert result[0].selected is True  # Quantum Computing
    assert result[1].selected is False  # science — not selected
    assert result[2].selected is True  # Shor's Algorithm


@pytest.mark.asyncio
async def test_agent_select_with_edits() -> None:
    """agent_select_nodes applies edit_node tool calls."""
    from kt_hatchet.models import ProposedNode
    from kt_worker_orchestrator.bottom_up.agent_select import agent_select_nodes

    ctx = MagicMock()
    ctx.model_gateway = MagicMock()
    ctx.model_gateway.orchestrator_model = "test-model"
    ctx.model_gateway.generate_with_tools = AsyncMock(
        return_value=[
            {"name": "edit_node", "arguments": {"index": 0, "name": "May 2021 Marriage of Alice and Bob"}},
            {"name": "edit_node", "arguments": {"index": 1, "node_type": "entity"}},
            {"name": "select_nodes", "arguments": {"indices": [0, 1]}},
        ]
    )

    nodes = [
        ProposedNode(name="May 2021 marriage", node_type="event", priority=7),
        ProposedNode(name="Alice", node_type="concept", priority=6),
    ]

    result = await agent_select_nodes(ctx, nodes, max_select=5)

    assert result[0].name == "May 2021 Marriage of Alice and Bob"
    assert result[0].selected is True
    assert result[1].node_type == "entity"
    assert result[1].selected is True


@pytest.mark.asyncio
async def test_agent_select_respects_max() -> None:
    """agent_select_nodes stops at max_select even if LLM selects more."""
    from kt_hatchet.models import ProposedNode
    from kt_worker_orchestrator.bottom_up.agent_select import agent_select_nodes

    ctx = MagicMock()
    ctx.model_gateway = MagicMock()
    ctx.model_gateway.orchestrator_model = "test-model"
    ctx.model_gateway.generate_with_tools = AsyncMock(
        return_value=[
            {"name": "select_nodes", "arguments": {"indices": [0, 1, 2, 3, 4]}},
        ]
    )

    nodes = [ProposedNode(name=f"node_{i}", priority=5) for i in range(5)]

    result = await agent_select_nodes(ctx, nodes, max_select=2)

    selected = [n for n in result if n.selected]
    assert len(selected) == 2


@pytest.mark.asyncio
async def test_agent_select_handles_error() -> None:
    """agent_select_nodes handles LLM errors gracefully."""
    from kt_hatchet.models import ProposedNode
    from kt_worker_orchestrator.bottom_up.agent_select import agent_select_nodes

    ctx = MagicMock()
    ctx.model_gateway = MagicMock()
    ctx.model_gateway.orchestrator_model = "test-model"
    ctx.model_gateway.generate_with_tools = AsyncMock(side_effect=Exception("LLM error"))

    nodes = [ProposedNode(name="test", priority=5)]

    result = await agent_select_nodes(ctx, nodes, max_select=1)

    # All nodes should remain deselected on error
    assert result[0].selected is False


@pytest.mark.asyncio
async def test_agent_select_empty_nodes() -> None:
    """agent_select_nodes handles empty node list."""
    from kt_worker_orchestrator.bottom_up.agent_select import agent_select_nodes

    ctx = MagicMock()
    result = await agent_select_nodes(ctx, [], max_select=5)
    assert result == []
