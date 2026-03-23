"""Unit tests for explore_scope sub-explorer agent."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from kt_worker_orchestrator.agents.orchestrator_state import OrchestratorState, SubExplorerState
from kt_agents_core.state import AgentContext

pytestmark = pytest.mark.asyncio


def _make_ctx() -> AgentContext:
    """Create an AgentContext with all-mock dependencies."""
    graph_engine = AsyncMock()
    provider_registry = AsyncMock()
    model_gateway = AsyncMock()
    embedding_service = AsyncMock()
    session = AsyncMock()
    graph_engine._node_repo = AsyncMock()

    # Make begin_nested() return a proper async context manager (sync call, async CM)
    nested_cm = AsyncMock()
    nested_cm.__aenter__ = AsyncMock(return_value=None)
    nested_cm.__aexit__ = AsyncMock(return_value=False)
    session.begin_nested = MagicMock(return_value=nested_cm)

    return AgentContext(
        graph_engine=graph_engine,
        provider_registry=provider_registry,
        model_gateway=model_gateway,
        embedding_service=embedding_service,
        session=session,
    )


def _make_orchestrator_state(**kwargs: object) -> OrchestratorState:
    """Create an OrchestratorState with defaults."""
    defaults: dict[str, object] = {"query": "test query", "nav_budget": 10, "explore_budget": 5}
    defaults.update(kwargs)
    return OrchestratorState(**defaults)  # type: ignore[arg-type]


# ── SubExplorerState tests ─────────────────────────────────────────


def test_sub_explorer_state_explore_remaining() -> None:
    """SubExplorerState.explore_remaining computes correctly."""
    state = SubExplorerState(
        scope="test scope",
        parent_query="test",
        query="test scope",
        nav_budget=5,
        explore_budget=3,
        explore_used=1,
    )
    assert state.explore_remaining == 2


def test_sub_explorer_state_has_visited() -> None:
    """SubExplorerState.has_visited checks visited_nodes."""
    state = SubExplorerState(
        scope="test",
        parent_query="test",
        query="test",
        nav_budget=5,
        explore_budget=3,
        visited_nodes=["node-1"],
    )
    assert state.has_visited("node-1") is True
    assert state.has_visited("node-2") is False


# ── OrchestratorState sub_explorer_summaries field ─────────────────


def test_orchestrator_state_has_sub_explorer_summaries() -> None:
    """OrchestratorState includes sub_explorer_summaries field."""
    state = _make_orchestrator_state()
    assert hasattr(state, "sub_explorer_summaries")
    assert state.sub_explorer_summaries == []


# ── explore_scope_impl tests ──────────────────────────────────────


async def test_explore_scope_impl_budget_deducted_upfront() -> None:
    """explore_scope_impl deducts explore budget from orchestrator upfront."""
    from kt_worker_orchestrator.agents.tools.explore_scope import explore_scope_impl

    ctx = _make_ctx()
    orch_state = _make_orchestrator_state(explore_budget=10, nav_budget=20)

    # Mock the sub-explorer graph to do nothing (immediate finish)
    with patch(
        "kt_worker_orchestrator.agents.tools.explore_scope.build_sub_explorer_graph"
    ) as mock_build:
        mock_graph = MagicMock()
        mock_compiled = MagicMock()
        # Return state with 2 of 4 explore used
        mock_compiled.ainvoke = AsyncMock(return_value={
            "summary": "Explored the scope",
            "created_nodes": ["n1"],
            "created_edges": [],
            "visited_nodes": ["n1"],
            "explore_used": 2,
            "gathered_fact_count": 5,
            "phase": "done",
        })
        mock_graph.compile = MagicMock(return_value=mock_compiled)
        mock_build.return_value = mock_graph

        result = await explore_scope_impl("test scope", 4, 5, ctx, orch_state)

    # Budget should reflect: deducted 4 upfront, refunded 2 (4-2=2 unused)
    assert orch_state.explore_used == 2  # 4 deducted - 2 refunded
    assert result["explore_used"] == 2
    assert result["explore_allocated"] == 4
    assert result["explore_refunded"] == 2


async def test_explore_scope_impl_budget_cap() -> None:
    """explore_scope_impl rejects when requested budget exceeds remaining."""
    from kt_worker_orchestrator.agents.tools.explore_scope import explore_scope_impl

    ctx = _make_ctx()
    orch_state = _make_orchestrator_state(explore_budget=3, nav_budget=5)

    # Request 10 explore but only 3 available — should return error
    result = await explore_scope_impl("test scope", 10, 10, ctx, orch_state)

    assert "error" in result
    assert "exceeds remaining" in result["error"]
    assert result["scope"] == "test scope"
    # Budget should NOT have been deducted
    assert orch_state.explore_used == 0


async def test_explore_scope_impl_propagates_nodes_and_edges() -> None:
    """explore_scope_impl propagates created nodes/edges back to orchestrator."""
    from kt_worker_orchestrator.agents.tools.explore_scope import explore_scope_impl

    ctx = _make_ctx()
    orch_state = _make_orchestrator_state(explore_budget=5, nav_budget=10)

    with patch(
        "kt_worker_orchestrator.agents.tools.explore_scope.build_sub_explorer_graph"
    ) as mock_build:
        mock_graph = MagicMock()
        mock_compiled = MagicMock()
        mock_compiled.ainvoke = AsyncMock(return_value={
            "summary": "Built nodes",
            "created_nodes": ["node-a", "node-b"],
            "created_edges": ["edge-1"],
            "visited_nodes": ["node-a", "node-b", "node-c"],
            "explore_used": 2,
            "gathered_fact_count": 10,
            "phase": "done",
        })
        mock_graph.compile = MagicMock(return_value=mock_compiled)
        mock_build.return_value = mock_graph

        result = await explore_scope_impl("test scope", 3, 5, ctx, orch_state)

    assert "node-a" in orch_state.created_nodes
    assert "node-b" in orch_state.created_nodes
    assert "edge-1" in orch_state.created_edges
    assert "node-a" in orch_state.visited_nodes
    assert "node-b" in orch_state.visited_nodes
    assert "node-c" in orch_state.visited_nodes
    assert orch_state.gathered_fact_count == 10


async def test_explore_scope_impl_summary_accumulation() -> None:
    """explore_scope_impl appends briefing to sub_explorer_summaries."""
    from kt_worker_orchestrator.agents.tools.explore_scope import explore_scope_impl

    ctx = _make_ctx()
    orch_state = _make_orchestrator_state(explore_budget=10, nav_budget=10)

    with patch(
        "kt_worker_orchestrator.agents.tools.explore_scope.build_sub_explorer_graph"
    ) as mock_build:
        mock_graph = MagicMock()
        mock_compiled = MagicMock()
        mock_compiled.ainvoke = AsyncMock(return_value={
            "summary": "Scope A findings",
            "created_nodes": ["n1"],
            "created_edges": [],
            "visited_nodes": ["n1"],
            "explore_used": 3,
            "gathered_fact_count": 5,
            "phase": "done",
        })
        mock_graph.compile = MagicMock(return_value=mock_compiled)
        mock_build.return_value = mock_graph

        await explore_scope_impl("scope A", 4, 3, ctx, orch_state)

    assert len(orch_state.sub_explorer_summaries) == 1
    assert orch_state.sub_explorer_summaries[0]["scope"] == "scope A"
    assert orch_state.sub_explorer_summaries[0]["summary"] == "Scope A findings"


async def test_explore_scope_impl_error_handling() -> None:
    """explore_scope_impl returns partial results on error."""
    from kt_worker_orchestrator.agents.tools.explore_scope import explore_scope_impl

    ctx = _make_ctx()
    orch_state = _make_orchestrator_state(explore_budget=5, nav_budget=5)

    with patch(
        "kt_worker_orchestrator.agents.tools.explore_scope.build_sub_explorer_graph"
    ) as mock_build:
        mock_graph = MagicMock()
        mock_compiled = MagicMock()
        mock_compiled.ainvoke = AsyncMock(side_effect=RuntimeError("LLM failure"))
        mock_graph.compile = MagicMock(return_value=mock_compiled)
        mock_build.return_value = mock_graph

        result = await explore_scope_impl("failing scope", 3, 3, ctx, orch_state)

    # Should have a summary despite error
    assert "error" in result["summary"].lower() or "failing scope" in result["summary"]
    # Briefing should still be appended
    assert len(orch_state.sub_explorer_summaries) == 1


async def test_explore_scope_impl_zero_explore_budget_error() -> None:
    """explore_scope_impl returns error when orchestrator has no explore budget."""
    from kt_worker_orchestrator.agents.tools.explore_scope import explore_scope_impl

    ctx = _make_ctx()
    orch_state = _make_orchestrator_state(explore_budget=5, explore_used=5)

    result = await explore_scope_impl("test", 3, 3, ctx, orch_state)

    assert "error" in result


async def test_explore_scope_impl_no_duplicate_nodes() -> None:
    """explore_scope_impl does not add duplicate node IDs to orchestrator."""
    from kt_worker_orchestrator.agents.tools.explore_scope import explore_scope_impl

    ctx = _make_ctx()
    orch_state = _make_orchestrator_state(explore_budget=5, nav_budget=10)
    orch_state.visited_nodes.append("existing-node")

    with patch(
        "kt_worker_orchestrator.agents.tools.explore_scope.build_sub_explorer_graph"
    ) as mock_build:
        mock_graph = MagicMock()
        mock_compiled = MagicMock()
        mock_compiled.ainvoke = AsyncMock(return_value={
            "summary": "Done",
            "created_nodes": [],
            "created_edges": [],
            "visited_nodes": ["existing-node", "new-node"],
            "explore_used": 1,
            "gathered_fact_count": 0,
            "phase": "done",
        })
        mock_graph.compile = MagicMock(return_value=mock_compiled)
        mock_build.return_value = mock_graph

        await explore_scope_impl("test", 2, 3, ctx, orch_state)

    # "existing-node" should not be duplicated
    assert orch_state.visited_nodes.count("existing-node") == 1
    assert "new-node" in orch_state.visited_nodes


# ── Child context tests ─────────────────────────────────────────────


def test_create_child_context_independent_session() -> None:
    """create_child_context creates a child with its own session but shared services."""
    parent_session = AsyncMock()
    child_session = AsyncMock()
    factory = MagicMock(return_value=child_session)

    ctx = AgentContext(
        graph_engine=AsyncMock(),
        provider_registry=AsyncMock(),
        model_gateway=AsyncMock(),
        embedding_service=AsyncMock(),
        session=parent_session,
        session_factory=factory,
    )

    child = ctx.create_child_context()

    # Child has its own session
    assert child.session is child_session
    assert child.session is not ctx.session

    # Stateless services are shared
    assert child.model_gateway is ctx.model_gateway
    assert child.provider_registry is ctx.provider_registry
    assert child.embedding_service is ctx.embedding_service
    assert child.content_fetcher is ctx.content_fetcher

    # Child gets its own GraphEngine (different from parent)
    assert child.graph_engine is not ctx.graph_engine

    # Factory was called once
    factory.assert_called_once()


def test_create_child_context_raises_without_factory() -> None:
    """create_child_context raises RuntimeError when no session_factory is provided."""
    ctx = _make_ctx()  # no session_factory
    with pytest.raises(RuntimeError, match="no session_factory"):
        ctx.create_child_context()


async def test_explore_scope_commits_child_session() -> None:
    """explore_scope_impl commits and closes the child session on success."""
    from kt_worker_orchestrator.agents.tools.explore_scope import explore_scope_impl

    parent_session = AsyncMock()
    child_session = AsyncMock()
    factory = MagicMock(return_value=child_session)

    ctx = AgentContext(
        graph_engine=AsyncMock(),
        provider_registry=AsyncMock(),
        model_gateway=AsyncMock(),
        embedding_service=AsyncMock(),
        session=parent_session,
        session_factory=factory,
    )
    ctx.graph_engine._node_repo = AsyncMock()

    orch_state = _make_orchestrator_state(explore_budget=10, nav_budget=20)

    with patch(
        "kt_worker_orchestrator.agents.tools.explore_scope.build_sub_explorer_graph"
    ) as mock_build:
        mock_graph = MagicMock()
        mock_compiled = MagicMock()
        mock_compiled.ainvoke = AsyncMock(return_value={
            "summary": "Done",
            "created_nodes": ["n1"],
            "created_edges": [],
            "visited_nodes": ["n1"],
            "explore_used": 2,
            "gathered_fact_count": 3,
            "phase": "done",
        })
        mock_graph.compile = MagicMock(return_value=mock_compiled)
        mock_build.return_value = mock_graph

        await explore_scope_impl("test scope", 3, 5, ctx, orch_state)

    # Child session should have been committed and closed
    child_session.commit.assert_awaited_once()
    child_session.close.assert_awaited_once()

    # Parent session should NOT have been used for the sub-explorer graph
    parent_session.commit.assert_not_awaited()


async def test_explore_scope_closes_child_on_error() -> None:
    """explore_scope_impl closes the child session even when the subgraph fails."""
    from kt_worker_orchestrator.agents.tools.explore_scope import explore_scope_impl

    parent_session = AsyncMock()
    child_session = AsyncMock()
    factory = MagicMock(return_value=child_session)

    ctx = AgentContext(
        graph_engine=AsyncMock(),
        provider_registry=AsyncMock(),
        model_gateway=AsyncMock(),
        embedding_service=AsyncMock(),
        session=parent_session,
        session_factory=factory,
    )
    ctx.graph_engine._node_repo = AsyncMock()

    orch_state = _make_orchestrator_state(explore_budget=10, nav_budget=20)

    with patch(
        "kt_worker_orchestrator.agents.tools.explore_scope.build_sub_explorer_graph"
    ) as mock_build:
        mock_graph = MagicMock()
        mock_compiled = MagicMock()
        mock_compiled.ainvoke = AsyncMock(side_effect=RuntimeError("LLM failure"))
        mock_graph.compile = MagicMock(return_value=mock_compiled)
        mock_build.return_value = mock_graph

        await explore_scope_impl("failing scope", 3, 5, ctx, orch_state)

    # Child session should have been closed despite the error
    child_session.close.assert_awaited_once()


async def test_explore_scope_falls_back_without_factory() -> None:
    """explore_scope_impl falls back to parent ctx when no session_factory exists."""
    from kt_worker_orchestrator.agents.tools.explore_scope import explore_scope_impl

    ctx = _make_ctx()  # no session_factory
    orch_state = _make_orchestrator_state(explore_budget=5, nav_budget=10)

    with patch(
        "kt_worker_orchestrator.agents.tools.explore_scope.build_sub_explorer_graph"
    ) as mock_build:
        mock_graph = MagicMock()
        mock_compiled = MagicMock()
        mock_compiled.ainvoke = AsyncMock(return_value={
            "summary": "Done without factory",
            "created_nodes": [],
            "created_edges": [],
            "visited_nodes": [],
            "explore_used": 1,
            "gathered_fact_count": 2,
            "phase": "done",
        })
        mock_graph.compile = MagicMock(return_value=mock_compiled)
        mock_build.return_value = mock_graph

        result = await explore_scope_impl("test", 2, 3, ctx, orch_state)

    # Should succeed with the parent context (no child session created)
    assert result["summary"] == "Done without factory"
    # build_sub_explorer_graph should have been called with the parent ctx
    mock_build.assert_called_once()
    assert mock_build.call_args[0][0] is ctx  # first positional arg is ctx
