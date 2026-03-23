"""Tests for QdrantNodeRepository."""

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from kt_qdrant.repositories.nodes import (
    NODES_COLLECTION,
    QdrantNodeRepository,
)


@pytest.fixture
def mock_client() -> AsyncMock:
    return AsyncMock()


@pytest.fixture
def repo(mock_client: AsyncMock) -> QdrantNodeRepository:
    return QdrantNodeRepository(client=mock_client)


def _make_embedding(dim: int = 3072) -> list[float]:
    """Create a dummy embedding vector."""
    return [0.1] * dim


class TestEnsureCollection:
    async def test_creates_when_missing(self, repo: QdrantNodeRepository, mock_client: AsyncMock) -> None:
        mock_collections = MagicMock()
        mock_collections.collections = []
        mock_client.get_collections.return_value = mock_collections

        with patch("kt_qdrant.repositories.nodes.get_settings") as mock_settings:
            mock_settings.return_value.embedding_dimensions = 3072
            await repo.ensure_collection()

        mock_client.create_collection.assert_called_once()
        call_kwargs = mock_client.create_collection.call_args
        assert call_kwargs.kwargs["collection_name"] == NODES_COLLECTION

    async def test_skips_when_exists(self, repo: QdrantNodeRepository, mock_client: AsyncMock) -> None:
        mock_collection = MagicMock()
        mock_collection.name = NODES_COLLECTION
        mock_collections = MagicMock()
        mock_collections.collections = [mock_collection]
        mock_client.get_collections.return_value = mock_collections

        with patch("kt_qdrant.repositories.nodes.get_settings") as mock_settings:
            mock_settings.return_value.embedding_dimensions = 3072
            await repo.ensure_collection()

        mock_client.create_collection.assert_not_called()


class TestUpsert:
    async def test_upsert_single(self, repo: QdrantNodeRepository, mock_client: AsyncMock) -> None:
        node_id = uuid.uuid4()
        embedding = _make_embedding()

        await repo.upsert(node_id=node_id, embedding=embedding, node_type="concept", concept="test node")

        mock_client.upsert.assert_called_once()
        call_kwargs = mock_client.upsert.call_args.kwargs
        assert call_kwargs["collection_name"] == NODES_COLLECTION
        points = call_kwargs["points"]
        assert len(points) == 1
        assert points[0].id == str(node_id)
        assert points[0].payload["node_type"] == "concept"
        assert points[0].payload["concept"] == "test node"

    async def test_upsert_minimal_payload(self, repo: QdrantNodeRepository, mock_client: AsyncMock) -> None:
        node_id = uuid.uuid4()
        embedding = _make_embedding()

        await repo.upsert(node_id=node_id, embedding=embedding)

        points = mock_client.upsert.call_args.kwargs["points"]
        assert points[0].payload == {}


class TestUpsertBatch:
    async def test_batch_upsert(self, repo: QdrantNodeRepository, mock_client: AsyncMock) -> None:
        nodes = [
            (uuid.uuid4(), _make_embedding(), "concept", "node A"),
            (uuid.uuid4(), _make_embedding(), "entity", "node B"),
            (uuid.uuid4(), _make_embedding(), None, None),
        ]

        await repo.upsert_batch(nodes)

        mock_client.upsert.assert_called_once()
        points = mock_client.upsert.call_args.kwargs["points"]
        assert len(points) == 3
        assert points[0].payload["node_type"] == "concept"
        assert points[0].payload["concept"] == "node A"
        assert points[2].payload == {}

    async def test_empty_batch(self, repo: QdrantNodeRepository, mock_client: AsyncMock) -> None:
        await repo.upsert_batch([])
        mock_client.upsert.assert_not_called()


class TestSearchSimilar:
    async def test_basic_search(self, repo: QdrantNodeRepository, mock_client: AsyncMock) -> None:
        node_id = uuid.uuid4()
        mock_point = MagicMock()
        mock_point.id = str(node_id)
        mock_point.score = 0.85
        mock_point.payload = {"node_type": "concept", "concept": "test"}

        mock_result = MagicMock()
        mock_result.points = [mock_point]
        mock_client.query_points.return_value = mock_result

        results = await repo.search_similar(embedding=_make_embedding(), limit=5)

        assert len(results) == 1
        assert results[0].node_id == node_id
        assert results[0].score == 0.85
        assert results[0].node_type == "concept"
        assert results[0].concept == "test"

    async def test_search_with_type_filter(self, repo: QdrantNodeRepository, mock_client: AsyncMock) -> None:
        mock_result = MagicMock()
        mock_result.points = []
        mock_client.query_points.return_value = mock_result

        await repo.search_similar(embedding=_make_embedding(), node_type="entity")

        call_kwargs = mock_client.query_points.call_args.kwargs
        assert call_kwargs["query_filter"] is not None

    async def test_search_with_exclude_ids(self, repo: QdrantNodeRepository, mock_client: AsyncMock) -> None:
        mock_result = MagicMock()
        mock_result.points = []
        mock_client.query_points.return_value = mock_result

        exclude = [uuid.uuid4()]
        await repo.search_similar(embedding=_make_embedding(), exclude_ids=exclude)

        call_kwargs = mock_client.query_points.call_args.kwargs
        assert call_kwargs["query_filter"] is not None

    async def test_empty_results(self, repo: QdrantNodeRepository, mock_client: AsyncMock) -> None:
        mock_result = MagicMock()
        mock_result.points = []
        mock_client.query_points.return_value = mock_result

        results = await repo.search_similar(embedding=_make_embedding())
        assert results == []


class TestDelete:
    async def test_delete_single(self, repo: QdrantNodeRepository, mock_client: AsyncMock) -> None:
        node_id = uuid.uuid4()
        await repo.delete(node_id)

        mock_client.delete.assert_called_once()
        call_kwargs = mock_client.delete.call_args.kwargs
        assert call_kwargs["collection_name"] == NODES_COLLECTION
        assert str(node_id) in call_kwargs["points_selector"]

    async def test_delete_batch(self, repo: QdrantNodeRepository, mock_client: AsyncMock) -> None:
        ids = [uuid.uuid4(), uuid.uuid4()]
        await repo.delete_batch(ids)

        mock_client.delete.assert_called_once()
        selector = mock_client.delete.call_args.kwargs["points_selector"]
        assert len(selector) == 2

    async def test_delete_batch_empty(self, repo: QdrantNodeRepository, mock_client: AsyncMock) -> None:
        await repo.delete_batch([])
        mock_client.delete.assert_not_called()


class TestCount:
    async def test_count(self, repo: QdrantNodeRepository, mock_client: AsyncMock) -> None:
        mock_info = MagicMock()
        mock_info.points_count = 42
        mock_client.get_collection.return_value = mock_info

        count = await repo.count()
        assert count == 42


class TestBuildFilter:
    def test_no_filters(self, repo: QdrantNodeRepository) -> None:
        result = repo._build_filter()
        assert result is None

    def test_node_type_only(self, repo: QdrantNodeRepository) -> None:
        result = repo._build_filter(node_type="concept")
        assert result is not None
        assert result.must is not None

    def test_exclude_ids_only(self, repo: QdrantNodeRepository) -> None:
        result = repo._build_filter(exclude_ids=[uuid.uuid4()])
        assert result is not None
        assert result.must_not is not None

    def test_both_filters(self, repo: QdrantNodeRepository) -> None:
        result = repo._build_filter(node_type="concept", exclude_ids=[uuid.uuid4()])
        assert result is not None
        assert result.must is not None
        assert result.must_not is not None
