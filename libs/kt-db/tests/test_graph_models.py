"""Unit tests for multi-graph models and GraphSessionResolver."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from kt_db.graph_sessions import GraphInfo, GraphSessionResolver, GraphSessions


class TestGraphInfo:
    def test_from_orm(self):
        graph = MagicMock()
        graph.id = uuid.uuid4()
        graph.slug = "test-graph"
        graph.name = "Test Graph"
        graph.schema_name = "graph_test_graph"
        graph.storage_mode = "schema"
        graph.is_default = False
        graph.database_connection_id = None
        graph.status = "active"

        info = GraphInfo.from_orm(graph)
        assert info.id == graph.id
        assert info.slug == "test-graph"
        assert info.schema_name == "graph_test_graph"
        assert info.is_default is False

    def test_from_orm_default(self):
        graph = MagicMock()
        graph.id = uuid.uuid4()
        graph.slug = "default"
        graph.name = "Default Graph"
        graph.schema_name = "public"
        graph.storage_mode = "schema"
        graph.is_default = True
        graph.database_connection_id = None
        graph.status = "active"

        info = GraphInfo.from_orm(graph)
        assert info.is_default is True
        assert info.schema_name == "public"

    def test_frozen(self):
        info = GraphInfo(
            id=uuid.uuid4(),
            slug="test",
            name="Test",
            schema_name="graph_test",
            storage_mode="schema",
            is_default=False,
            database_connection_id=None,
            status="active",
        )
        with pytest.raises(AttributeError):
            info.slug = "changed"  # type: ignore[misc]


class TestGraphSessions:
    def test_frozen(self):
        gs = GraphSessions(
            graph=GraphInfo(
                id=uuid.uuid4(),
                slug="test",
                name="Test",
                schema_name="graph_test",
                storage_mode="schema",
                is_default=False,
                database_connection_id=None,
                status="active",
            ),
            graph_session_factory=MagicMock(),
            write_session_factory=MagicMock(),
            qdrant_collection_prefix="test__",
        )
        assert gs.qdrant_collection_prefix == "test__"
        with pytest.raises(AttributeError):
            gs.qdrant_collection_prefix = "changed"  # type: ignore[misc]


class TestGraphSessionResolver:
    def test_init_with_default_factories(self):
        control_sf = MagicMock()
        graph_sf = MagicMock()
        write_sf = MagicMock()
        resolver = GraphSessionResolver(
            control_sf,
            default_graph_session_factory=graph_sf,
            default_write_session_factory=write_sf,
        )
        assert resolver._default_graph_sf is graph_sf
        assert resolver._default_write_sf is write_sf

    def test_init_without_default_factories(self):
        control_sf = MagicMock()
        resolver = GraphSessionResolver(control_sf)
        assert resolver._default_graph_sf is None
        assert resolver._default_write_sf is None
        assert len(resolver._cache) == 0

    @pytest.mark.asyncio
    async def test_resolve_not_found(self):
        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_session.execute = AsyncMock(return_value=mock_result)

        control_sf = MagicMock(return_value=mock_session)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        resolver = GraphSessionResolver(control_sf)
        with pytest.raises(ValueError, match="not found"):
            await resolver.resolve(uuid.uuid4())

    @pytest.mark.asyncio
    async def test_resolve_by_slug_not_found(self):
        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_session.execute = AsyncMock(return_value=mock_result)

        control_sf = MagicMock(return_value=mock_session)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        resolver = GraphSessionResolver(control_sf)
        with pytest.raises(ValueError, match="not found"):
            await resolver.resolve_by_slug("nonexistent")
