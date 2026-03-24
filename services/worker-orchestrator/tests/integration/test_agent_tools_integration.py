"""Integration tests for agent tools using real DB."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from kt_agents_core.state import AgentContext, PipelineState
from kt_graph.engine import GraphEngine

pytestmark = pytest.mark.asyncio


def _make_ctx(db_session: object) -> AgentContext:
    """Create AgentContext with real GraphEngine and mocked external services."""
    graph_engine = GraphEngine(db_session)  # type: ignore[arg-type]
    return AgentContext(
        graph_engine=graph_engine,
        provider_registry=AsyncMock(),
        model_gateway=AsyncMock(),
        embedding_service=None,
        session=db_session,  # type: ignore[arg-type]
    )


def _make_state(**kwargs: object) -> PipelineState:
    """Create an PipelineState with defaults."""
    defaults: dict[str, object] = {"query": "test query", "nav_budget": 10, "explore_budget": 5}
    defaults.update(kwargs)
    return PipelineState(**defaults)  # type: ignore[arg-type]


async def test_build_node_integration(db_session: object) -> None:
    """Test build_node_unified creates a node in real DB."""
    from kt_worker_nodes.pipelines.building.unified import UnifiedNodeBuilder

    ctx = _make_ctx(db_session)
    state = _make_state()

    # Mock external services but use real DB
    ctx.provider_registry.search_all = AsyncMock(return_value=[])
    ctx.model_gateway.generate_json = AsyncMock(return_value={"components": []})
    ctx.model_gateway.dimension_model = "test-model"

    result = await UnifiedNodeBuilder(ctx).build("build_concept_integration_unique_xyz", "concept", ctx, state)
    # With no embedding service and no search results, should skip (no facts in pool)
    assert result["action"] == "skipped"
