"""Unit tests for WorkerGraphEngine Qdrant node_id updates in link/unlink."""

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from kt_graph.worker_engine import WorkerGraphEngine


@pytest.fixture
def mock_qdrant_fact_repo() -> AsyncMock:
    return AsyncMock()


class _FakeNestedTransaction:
    """Fake async context manager mimicking session.begin_nested()."""

    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, *args: object) -> bool:
        return False


@pytest.fixture
def mock_session() -> AsyncMock:
    """Minimal mock for graph-db AsyncSession."""
    session = AsyncMock()
    session.begin_nested = MagicMock(return_value=_FakeNestedTransaction())
    return session


@pytest.fixture
def mock_write_session() -> AsyncMock:
    """Minimal mock for write-db AsyncSession."""
    session = AsyncMock()
    return session


class TestLinkFactToNodeQdrant:
    """Test that link_fact_to_node calls Qdrant append_node_id."""

    async def test_link_via_graph_db_updates_qdrant(
        self, mock_session: AsyncMock, mock_qdrant_fact_repo: AsyncMock
    ) -> None:
        """When no write session, link goes through graph-db and updates Qdrant."""
        engine = WorkerGraphEngine(write_session=mock_session)
        engine._qdrant_fact_repo = mock_qdrant_fact_repo

        # Mock the fact_repo.link_to_node call
        node_fact = MagicMock()
        engine._fact_repo = AsyncMock()
        engine._fact_repo.link_to_node = AsyncMock(return_value=node_fact)

        node_id = uuid.uuid4()
        fact_id = uuid.uuid4()
        result = await engine.link_fact_to_node(node_id, fact_id)

        assert result is node_fact
        mock_qdrant_fact_repo.append_node_id.assert_awaited_once_with(fact_id, node_id)

    async def test_link_via_write_db_updates_qdrant(
        self,
        mock_session: AsyncMock,
        mock_write_session: AsyncMock,
        mock_qdrant_fact_repo: AsyncMock,
    ) -> None:
        """When write session exists and node is in cache, updates Qdrant."""
        engine = WorkerGraphEngine(write_session=mock_write_session)
        engine._qdrant_fact_repo = mock_qdrant_fact_repo

        node_id = uuid.uuid4()
        fact_id = uuid.uuid4()

        # Put a node in the cache
        mock_node = MagicMock()
        mock_node.node_type = "concept"
        mock_node.concept = "test_concept"
        engine._node_cache[node_id] = mock_node

        await engine.link_fact_to_node(node_id, fact_id)

        mock_qdrant_fact_repo.append_node_id.assert_awaited_once_with(fact_id, node_id)

    async def test_link_qdrant_failure_does_not_raise(
        self, mock_session: AsyncMock, mock_qdrant_fact_repo: AsyncMock
    ) -> None:
        """Qdrant failure is logged but does not break the link operation."""
        engine = WorkerGraphEngine(write_session=mock_session)
        engine._qdrant_fact_repo = mock_qdrant_fact_repo
        mock_qdrant_fact_repo.append_node_id.side_effect = ConnectionError("Qdrant down")

        node_fact = MagicMock()
        engine._fact_repo = AsyncMock()
        engine._fact_repo.link_to_node = AsyncMock(return_value=node_fact)

        node_id = uuid.uuid4()
        fact_id = uuid.uuid4()
        # Should not raise
        result = await engine.link_fact_to_node(node_id, fact_id)
        assert result is node_fact

    async def test_link_no_qdrant_skips_update(self, mock_session: AsyncMock) -> None:
        """When no Qdrant repo, link works without Qdrant update."""
        engine = WorkerGraphEngine(write_session=mock_session)
        assert engine._qdrant_fact_repo is None

        node_fact = MagicMock()
        engine._fact_repo = AsyncMock()
        engine._fact_repo.link_to_node = AsyncMock(return_value=node_fact)

        node_id = uuid.uuid4()
        fact_id = uuid.uuid4()
        result = await engine.link_fact_to_node(node_id, fact_id)
        assert result is node_fact


class TestUnlinkFactFromNodeQdrant:
    """Test that unlink_fact_from_node calls Qdrant remove_node_id."""

    async def test_unlink_via_graph_db_updates_qdrant(
        self, mock_session: AsyncMock, mock_qdrant_fact_repo: AsyncMock
    ) -> None:
        engine = WorkerGraphEngine(write_session=mock_session)
        engine._qdrant_fact_repo = mock_qdrant_fact_repo

        engine._fact_repo = AsyncMock()
        engine._fact_repo.unlink_from_node = AsyncMock(return_value=True)

        node_id = uuid.uuid4()
        fact_id = uuid.uuid4()
        result = await engine.unlink_fact_from_node(node_id, fact_id)

        assert result is True
        mock_qdrant_fact_repo.remove_node_id.assert_awaited_once_with(fact_id, node_id)

    async def test_unlink_via_write_db_updates_qdrant(
        self,
        mock_session: AsyncMock,
        mock_write_session: AsyncMock,
        mock_qdrant_fact_repo: AsyncMock,
    ) -> None:
        engine = WorkerGraphEngine(write_session=mock_write_session)
        engine._qdrant_fact_repo = mock_qdrant_fact_repo

        node_id = uuid.uuid4()
        fact_id = uuid.uuid4()

        mock_node = MagicMock()
        mock_node.node_type = "concept"
        mock_node.concept = "test_concept"
        engine._node_cache[node_id] = mock_node

        result = await engine.unlink_fact_from_node(node_id, fact_id)

        assert result is True
        mock_qdrant_fact_repo.remove_node_id.assert_awaited_once_with(fact_id, node_id)

    async def test_unlink_skips_qdrant_when_link_did_not_exist(
        self, mock_session: AsyncMock, mock_qdrant_fact_repo: AsyncMock
    ) -> None:
        """When unlink_from_node returns False, Qdrant should not be updated."""
        engine = WorkerGraphEngine(write_session=mock_session)
        engine._qdrant_fact_repo = mock_qdrant_fact_repo

        engine._fact_repo = AsyncMock()
        engine._fact_repo.unlink_from_node = AsyncMock(return_value=False)

        node_id = uuid.uuid4()
        fact_id = uuid.uuid4()
        result = await engine.unlink_fact_from_node(node_id, fact_id)

        assert result is False
        mock_qdrant_fact_repo.remove_node_id.assert_not_called()

    async def test_unlink_qdrant_failure_does_not_raise(
        self, mock_session: AsyncMock, mock_qdrant_fact_repo: AsyncMock
    ) -> None:
        engine = WorkerGraphEngine(write_session=mock_session)
        engine._qdrant_fact_repo = mock_qdrant_fact_repo
        mock_qdrant_fact_repo.remove_node_id.side_effect = ConnectionError("Qdrant down")

        engine._fact_repo = AsyncMock()
        engine._fact_repo.unlink_from_node = AsyncMock(return_value=True)

        node_id = uuid.uuid4()
        fact_id = uuid.uuid4()
        # Should not raise
        result = await engine.unlink_fact_from_node(node_id, fact_id)
        assert result is True
