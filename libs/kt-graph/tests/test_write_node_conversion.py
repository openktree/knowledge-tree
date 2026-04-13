"""Unit tests for WriteNode → Node conversion in GraphEngine."""

import uuid
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from kt_graph.worker_engine import WorkerGraphEngine


def _make_write_node(**overrides):
    """Create a mock WriteNode with sensible defaults."""
    defaults = {
        "node_uuid": uuid.uuid4(),
        "concept": "test concept",
        "node_type": "concept",
        "entity_subtype": None,
        "parent_key": None,
        "stale_after": 30,
        "definition": "A test definition",
        "definition_source": "test",
        "metadata_": {"key": "value"},
        "created_at": datetime(2026, 1, 1),
        "updated_at": datetime(2026, 1, 2),
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


class TestWriteNodeToNode:
    def test_basic_conversion(self):
        wn = _make_write_node()
        node = WorkerGraphEngine._write_node_to_node(wn)

        assert node.id == wn.node_uuid
        assert node.concept == wn.concept
        assert node.node_type == wn.node_type
        assert node.entity_subtype == wn.entity_subtype
        assert node.parent_id is None
        assert node.stale_after == wn.stale_after
        assert node.definition == wn.definition
        assert node.definition_source == wn.definition_source
        assert node.metadata_ == wn.metadata_
        assert node.access_count == 0
        assert node.update_count == 0
        assert node.created_at == wn.created_at
        assert node.updated_at == wn.updated_at

    def test_parent_key_resolves_to_uuid(self):
        wn = _make_write_node(parent_key="parent-topic")
        node = WorkerGraphEngine._write_node_to_node(wn)

        assert node.parent_id is not None
        # key_to_uuid is deterministic — same key always produces same UUID
        from kt_db.keys import key_to_uuid

        assert node.parent_id == key_to_uuid("parent-topic")

    def test_overrides_applied(self):
        wn = _make_write_node(metadata_={"original": True})
        node = WorkerGraphEngine._write_node_to_node(wn, metadata_={"overridden": True})

        assert node.metadata_ == {"overridden": True}


class TestSearchNodesFallback:
    @pytest.mark.asyncio
    async def test_uses_write_db(self):
        """search_nodes uses write-db search_by_concept."""
        wn = _make_write_node(concept="sleep")

        mock_write_node_repo = AsyncMock()
        mock_write_node_repo.search_by_concept.return_value = [wn]

        engine = WorkerGraphEngine(write_session=None)
        engine._write_node_repo = mock_write_node_repo

        nodes = await engine.search_nodes("sleep", limit=5)

        mock_write_node_repo.search_by_concept.assert_awaited_once_with("sleep", limit=5, node_type=None)
        assert len(nodes) == 1
        assert nodes[0].concept == "sleep"
        assert nodes[0].id == wn.node_uuid
