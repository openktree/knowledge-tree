"""Integration test: DefinitionPipeline skips crystallized nodes.

Moved from libs/kt-ontology/tests/integration/test_crystallization.py to enforce
the rule: no test in libs/*/tests/ imports from services/*.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from kt_db.models import Node
from kt_db.repositories.nodes import NodeRepository
from kt_graph.engine import GraphEngine
from kt_worker_nodes.pipelines.definitions.pipeline import DefinitionPipeline


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
class TestDefinitionPreservedForCrystallized:
    async def test_definition_preserved_for_crystallized(
        self,
        db_session: AsyncSession,
    ) -> None:
        """DefinitionPipeline should skip crystallized nodes."""
        parent = await _create_node(
            db_session,
            "preserved-def-parent",
            metadata_={"ontology_stable": True},
            definition="Preserved crystallized definition",
        )

        mock_gw = MagicMock()
        mock_gw.definition_model = "test-model"
        mock_gw.definition_thinking_level = ""
        mock_gw.generate = AsyncMock(return_value="NEW definition that should NOT be used")

        ctx = _make_agent_ctx(db_session, mock_gw)
        def_pipeline = DefinitionPipeline(ctx)
        result = await def_pipeline.generate_definition(parent.id, parent.concept)

        assert result == "Preserved crystallized definition"
        mock_gw.generate.assert_not_awaited()
