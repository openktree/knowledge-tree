"""Unit tests for WorkerGraphEngine Qdrant node_id updates in link/unlink."""

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from kt_graph.worker_engine import WorkerGraphEngine


@pytest.fixture
def mock_qdrant_fact_repo() -> AsyncMock:
    return AsyncMock()


@pytest.fixture
def mock_write_session() -> AsyncMock:
    """Minimal mock for write-db AsyncSession."""
    return AsyncMock()


def _make_engine(write_session: AsyncMock, qdrant_fact_repo: AsyncMock | None = None) -> WorkerGraphEngine:
    """Create a WorkerGraphEngine with mocked repos."""
    engine = WorkerGraphEngine(write_session=write_session)
    if qdrant_fact_repo is not None:
        engine._qdrant_fact_repo = qdrant_fact_repo
    return engine


class TestLinkFactToNodeQdrant:
    """Test that link_fact_to_node calls Qdrant append_node_id."""

    async def test_link_via_cache_updates_qdrant(
        self, mock_write_session: AsyncMock, mock_qdrant_fact_repo: AsyncMock
    ) -> None:
        """When node is in cache, link goes through write-db and updates Qdrant."""
        engine = _make_engine(mock_write_session, mock_qdrant_fact_repo)

        node_id = uuid.uuid4()
        fact_id = uuid.uuid4()

        # Put a node in the cache
        mock_node = MagicMock()
        mock_node.node_type = "concept"
        mock_node.concept = "test_concept"
        engine._node_cache[node_id] = mock_node

        await engine.link_fact_to_node(node_id, fact_id)

        mock_qdrant_fact_repo.append_node_id.assert_awaited_once_with(fact_id, node_id)

    async def test_link_via_write_db_lookup_updates_qdrant(
        self, mock_write_session: AsyncMock, mock_qdrant_fact_repo: AsyncMock
    ) -> None:
        """When node found in write-db (not cache), updates Qdrant."""
        engine = _make_engine(mock_write_session, mock_qdrant_fact_repo)

        node_id = uuid.uuid4()
        fact_id = uuid.uuid4()

        # Mock write_node_repo to return a WriteNode
        mock_wn = MagicMock()
        mock_wn.key = "concept:test-concept"
        engine._write_node_repo.get_by_uuid = AsyncMock(return_value=mock_wn)

        await engine.link_fact_to_node(node_id, fact_id)

        engine._write_node_repo.append_fact_id.assert_awaited_once()
        mock_qdrant_fact_repo.append_node_id.assert_awaited_once_with(fact_id, node_id)

    async def test_link_node_not_found_skips(
        self, mock_write_session: AsyncMock, mock_qdrant_fact_repo: AsyncMock
    ) -> None:
        """When node not found in write-db or cache, logs warning and returns."""
        engine = _make_engine(mock_write_session, mock_qdrant_fact_repo)

        node_id = uuid.uuid4()
        fact_id = uuid.uuid4()

        engine._write_node_repo.get_by_uuid = AsyncMock(return_value=None)

        await engine.link_fact_to_node(node_id, fact_id)

        mock_qdrant_fact_repo.append_node_id.assert_not_called()

    async def test_link_qdrant_failure_does_not_raise(
        self, mock_write_session: AsyncMock, mock_qdrant_fact_repo: AsyncMock
    ) -> None:
        """Qdrant failure is logged but does not break the link operation."""
        engine = _make_engine(mock_write_session, mock_qdrant_fact_repo)
        mock_qdrant_fact_repo.append_node_id.side_effect = ConnectionError("Qdrant down")

        node_id = uuid.uuid4()
        fact_id = uuid.uuid4()

        mock_node = MagicMock()
        mock_node.node_type = "concept"
        mock_node.concept = "test_concept"
        engine._node_cache[node_id] = mock_node

        # Should not raise
        await engine.link_fact_to_node(node_id, fact_id)

    async def test_link_no_qdrant_skips_update(self, mock_write_session: AsyncMock) -> None:
        """When no Qdrant repo, link works without Qdrant update."""
        engine = _make_engine(mock_write_session)
        assert engine._qdrant_fact_repo is None

        node_id = uuid.uuid4()
        fact_id = uuid.uuid4()

        mock_node = MagicMock()
        mock_node.node_type = "concept"
        mock_node.concept = "test_concept"
        engine._node_cache[node_id] = mock_node

        await engine.link_fact_to_node(node_id, fact_id)


class TestUnlinkFactFromNodeQdrant:
    """Test that unlink_fact_from_node calls Qdrant remove_node_id."""

    async def test_unlink_via_cache_updates_qdrant(
        self, mock_write_session: AsyncMock, mock_qdrant_fact_repo: AsyncMock
    ) -> None:
        engine = _make_engine(mock_write_session, mock_qdrant_fact_repo)

        node_id = uuid.uuid4()
        fact_id = uuid.uuid4()

        mock_node = MagicMock()
        mock_node.node_type = "concept"
        mock_node.concept = "test_concept"
        engine._node_cache[node_id] = mock_node

        result = await engine.unlink_fact_from_node(node_id, fact_id)

        assert result is True
        mock_qdrant_fact_repo.remove_node_id.assert_awaited_once_with(fact_id, node_id)

    async def test_unlink_via_write_db_lookup_updates_qdrant(
        self, mock_write_session: AsyncMock, mock_qdrant_fact_repo: AsyncMock
    ) -> None:
        engine = _make_engine(mock_write_session, mock_qdrant_fact_repo)

        node_id = uuid.uuid4()
        fact_id = uuid.uuid4()

        mock_wn = MagicMock()
        mock_wn.key = "concept:test-concept"
        engine._write_node_repo.get_by_uuid = AsyncMock(return_value=mock_wn)

        result = await engine.unlink_fact_from_node(node_id, fact_id)

        assert result is True
        mock_qdrant_fact_repo.remove_node_id.assert_awaited_once_with(fact_id, node_id)

    async def test_unlink_node_not_found_returns_false(
        self, mock_write_session: AsyncMock, mock_qdrant_fact_repo: AsyncMock
    ) -> None:
        """When node not found, returns False without updating Qdrant."""
        engine = _make_engine(mock_write_session, mock_qdrant_fact_repo)

        node_id = uuid.uuid4()
        fact_id = uuid.uuid4()

        engine._write_node_repo.get_by_uuid = AsyncMock(return_value=None)

        result = await engine.unlink_fact_from_node(node_id, fact_id)

        assert result is False
        mock_qdrant_fact_repo.remove_node_id.assert_not_called()

    async def test_unlink_qdrant_failure_does_not_raise(
        self, mock_write_session: AsyncMock, mock_qdrant_fact_repo: AsyncMock
    ) -> None:
        engine = _make_engine(mock_write_session, mock_qdrant_fact_repo)
        mock_qdrant_fact_repo.remove_node_id.side_effect = ConnectionError("Qdrant down")

        node_id = uuid.uuid4()
        fact_id = uuid.uuid4()

        mock_node = MagicMock()
        mock_node.node_type = "concept"
        mock_node.concept = "test_concept"
        engine._node_cache[node_id] = mock_node

        # Should not raise
        result = await engine.unlink_fact_from_node(node_id, fact_id)
        assert result is True
