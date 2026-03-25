"""Integration tests for ontology crystallization with a real DB."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from kt_db.models import Node
from kt_db.repositories.nodes import NodeRepository
from kt_graph.engine import GraphEngine
from kt_ontology.crystallization import (
    CrystallizationPipeline,
    _is_crystallized,
    _needs_recrystallization,
)


@pytest.fixture
def mock_model_gateway() -> MagicMock:
    gw = MagicMock()
    gw.crystallization_model = "test-model"
    gw.crystallization_thinking_level = ""
    gw.generate = AsyncMock(return_value="An authoritative crystallized definition.")
    return gw


@pytest.fixture
def mock_embedding_service() -> MagicMock:
    svc = MagicMock()
    svc.embed_text = AsyncMock(return_value=[0.1] * 3072)
    return svc


def _make_agent_ctx(
    session: AsyncSession,
    model_gateway: MagicMock,
    embedding_service: MagicMock | None = None,
) -> MagicMock:
    """Build a mock AgentContext backed by a real GraphEngine."""
    from kt_agents_core.state import AgentContext

    graph_engine = GraphEngine(session, embedding_service)
    ctx = MagicMock(spec=AgentContext)
    ctx.graph_engine = graph_engine
    ctx.model_gateway = model_gateway
    ctx.embedding_service = embedding_service
    ctx.emit = AsyncMock()
    return ctx


async def _create_node(
    session: AsyncSession,
    concept: str,
    parent_id: uuid.UUID | None = None,
    node_type: str = "concept",
    metadata_: dict | None = None,
    definition: str | None = None,
) -> Node:
    """Create a node directly in the DB."""
    repo = NodeRepository(session)
    node = await repo.create(
        concept=concept,
        parent_id=parent_id,
        node_type=node_type,
        metadata_=metadata_,
    )
    if definition:
        await repo.update_fields(node.id, definition=definition)
        await session.refresh(node)
    return node


@pytest.mark.asyncio
class TestCountChildren:
    async def test_count_zero(self, db_session: AsyncSession) -> None:
        parent = await _create_node(db_session, "parent-no-children")
        repo = NodeRepository(db_session)
        count = await repo.count_children(parent.id)
        assert count == 0

    async def test_count_multiple(self, db_session: AsyncSession) -> None:
        parent = await _create_node(db_session, "parent-with-children")
        for i in range(5):
            await _create_node(db_session, f"child-{i}", parent_id=parent.id)

        repo = NodeRepository(db_session)
        count = await repo.count_children(parent.id)
        assert count == 5

    async def test_count_via_graph_engine(self, db_session: AsyncSession) -> None:
        parent = await _create_node(db_session, "parent-via-engine")
        for i in range(3):
            await _create_node(db_session, f"child-engine-{i}", parent_id=parent.id)

        engine = GraphEngine(db_session)
        count = await engine.count_children(parent.id)
        assert count == 3


@pytest.mark.asyncio
class TestGetChildren:
    async def test_get_children(self, db_session: AsyncSession) -> None:
        parent = await _create_node(db_session, "parent-get-children")
        child1 = await _create_node(db_session, "child-gc-1", parent_id=parent.id)
        child2 = await _create_node(db_session, "child-gc-2", parent_id=parent.id)

        engine = GraphEngine(db_session)
        children = await engine.get_children(parent.id)
        child_ids = {c.id for c in children}
        assert child1.id in child_ids
        assert child2.id in child_ids


@pytest.mark.asyncio
class TestCrystallizationHelpers:
    async def test_is_crystallized_db_node(self, db_session: AsyncSession) -> None:
        node = await _create_node(
            db_session,
            "crystallized-node",
            metadata_={"ontology_stable": True, "crystallized_at": datetime.now(timezone.utc).isoformat()},
        )
        assert _is_crystallized(node) is True

    async def test_is_not_crystallized_db_node(self, db_session: AsyncSession) -> None:
        node = await _create_node(db_session, "not-crystallized-node")
        assert _is_crystallized(node) is False

    async def test_needs_recrystallization_with_db_children(
        self,
        db_session: AsyncSession,
    ) -> None:
        crystallized_at = datetime.now(timezone.utc) - timedelta(hours=2)
        parent = await _create_node(
            db_session,
            "recryst-parent",
            metadata_={
                "ontology_stable": True,
                "crystallized_at": crystallized_at.isoformat(),
            },
        )
        # Create children — they'll have updated_at = now (after crystallized_at)
        children = []
        for i in range(10):
            child = await _create_node(db_session, f"recryst-child-{i}", parent_id=parent.id)
            children.append(child)

        assert _needs_recrystallization(parent, children) is True


@pytest.mark.asyncio
class TestCrystallizationPipelineIntegration:
    async def test_full_crystallization(
        self,
        db_session: AsyncSession,
        mock_model_gateway: MagicMock,
    ) -> None:
        """Full pipeline: create parent + 10 children, then crystallize."""
        parent = await _create_node(
            db_session,
            "full-cryst-parent",
            definition="A simple definition",
        )
        for i in range(10):
            await _create_node(
                db_session,
                f"full-cryst-child-{i}",
                parent_id=parent.id,
                definition=f"Child {i} definition",
            )

        ctx = _make_agent_ctx(db_session, mock_model_gateway)
        pipeline = CrystallizationPipeline(ctx)

        result = await pipeline.check_and_crystallize(parent.id)
        assert result is True

        # Verify the LLM was called
        mock_model_gateway.generate.assert_awaited_once()

        # Verify the node was updated in DB
        await db_session.refresh(parent)
        assert parent.definition == "An authoritative crystallized definition."
        assert parent.metadata_ is not None
        assert parent.metadata_["ontology_stable"] is True
        assert "crystallized_at" in parent.metadata_
        assert parent.metadata_["crystallized_child_count"] == 10

    async def test_below_threshold_no_crystallization(
        self,
        db_session: AsyncSession,
        mock_model_gateway: MagicMock,
    ) -> None:
        parent = await _create_node(db_session, "low-child-parent")
        for i in range(5):
            await _create_node(
                db_session,
                f"low-child-{i}",
                parent_id=parent.id,
            )

        ctx = _make_agent_ctx(db_session, mock_model_gateway)
        pipeline = CrystallizationPipeline(ctx)

        result = await pipeline.check_and_crystallize(parent.id)
        assert result is False
        mock_model_gateway.generate.assert_not_awaited()

