"""Tests for QdrantFactRepository."""

import uuid
from collections.abc import AsyncGenerator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from kt_qdrant.repositories.facts import (
    FACTS_COLLECTION,
    FactSearchResult,
    QdrantFactRepository,
)


@pytest.fixture
def mock_client() -> AsyncMock:
    return AsyncMock()


@pytest.fixture
def repo(mock_client: AsyncMock) -> QdrantFactRepository:
    return QdrantFactRepository(client=mock_client)


def _make_embedding(dim: int = 3072) -> list[float]:
    """Create a dummy embedding vector."""
    return [0.1] * dim


class TestEnsureCollection:
    async def test_creates_when_missing(self, repo: QdrantFactRepository, mock_client: AsyncMock) -> None:
        mock_collections = MagicMock()
        mock_collections.collections = []
        mock_client.get_collections.return_value = mock_collections

        with patch("kt_qdrant.repositories.facts.get_settings") as mock_settings:
            mock_settings.return_value.embedding_dimensions = 3072
            await repo.ensure_collection()

        mock_client.create_collection.assert_called_once()
        call_kwargs = mock_client.create_collection.call_args
        assert call_kwargs.kwargs["collection_name"] == FACTS_COLLECTION

    async def test_skips_when_exists(self, repo: QdrantFactRepository, mock_client: AsyncMock) -> None:
        mock_collection = MagicMock()
        mock_collection.name = FACTS_COLLECTION
        mock_collections = MagicMock()
        mock_collections.collections = [mock_collection]
        mock_client.get_collections.return_value = mock_collections

        with patch("kt_qdrant.repositories.facts.get_settings") as mock_settings:
            mock_settings.return_value.embedding_dimensions = 3072
            await repo.ensure_collection()

        mock_client.create_collection.assert_not_called()


class TestUpsert:
    async def test_upsert_single(self, repo: QdrantFactRepository, mock_client: AsyncMock) -> None:
        fact_id = uuid.uuid4()
        embedding = _make_embedding()

        await repo.upsert(fact_id=fact_id, embedding=embedding, fact_type="claim")

        mock_client.upsert.assert_called_once()
        call_kwargs = mock_client.upsert.call_args.kwargs
        assert call_kwargs["collection_name"] == FACTS_COLLECTION
        points = call_kwargs["points"]
        assert len(points) == 1
        assert points[0].id == str(fact_id)
        assert points[0].payload["fact_type"] == "claim"

    async def test_upsert_with_node_ids(self, repo: QdrantFactRepository, mock_client: AsyncMock) -> None:
        fact_id = uuid.uuid4()
        node_id = uuid.uuid4()
        embedding = _make_embedding()

        await repo.upsert(fact_id=fact_id, embedding=embedding, node_ids=[node_id])

        points = mock_client.upsert.call_args.kwargs["points"]
        assert points[0].payload["node_ids"] == [str(node_id)]

    async def test_upsert_minimal_payload(self, repo: QdrantFactRepository, mock_client: AsyncMock) -> None:
        fact_id = uuid.uuid4()
        embedding = _make_embedding()

        await repo.upsert(fact_id=fact_id, embedding=embedding)

        points = mock_client.upsert.call_args.kwargs["points"]
        assert points[0].payload == {}


class TestUpsertBatch:
    async def test_batch_upsert(self, repo: QdrantFactRepository, mock_client: AsyncMock) -> None:
        facts = [
            (uuid.uuid4(), _make_embedding(), "claim"),
            (uuid.uuid4(), _make_embedding(), "evidence"),
            (uuid.uuid4(), _make_embedding(), None),
        ]

        await repo.upsert_batch(facts)

        mock_client.upsert.assert_called_once()
        points = mock_client.upsert.call_args.kwargs["points"]
        assert len(points) == 3
        assert points[0].payload["fact_type"] == "claim"
        assert points[2].payload == {}

    async def test_empty_batch(self, repo: QdrantFactRepository, mock_client: AsyncMock) -> None:
        await repo.upsert_batch([])
        mock_client.upsert.assert_not_called()


class TestSearchSimilar:
    async def test_basic_search(self, repo: QdrantFactRepository, mock_client: AsyncMock) -> None:
        fact_id = uuid.uuid4()
        mock_point = MagicMock()
        mock_point.id = str(fact_id)
        mock_point.score = 0.95
        mock_point.payload = {"fact_type": "claim"}

        mock_result = MagicMock()
        mock_result.points = [mock_point]
        mock_client.query_points.return_value = mock_result

        results = await repo.search_similar(embedding=_make_embedding(), limit=5)

        assert len(results) == 1
        assert results[0].fact_id == fact_id
        assert results[0].score == 0.95
        assert results[0].fact_type == "claim"

    async def test_search_with_type_filter(self, repo: QdrantFactRepository, mock_client: AsyncMock) -> None:
        mock_result = MagicMock()
        mock_result.points = []
        mock_client.query_points.return_value = mock_result

        await repo.search_similar(embedding=_make_embedding(), fact_type="evidence")

        call_kwargs = mock_client.query_points.call_args.kwargs
        assert call_kwargs["query_filter"] is not None

    async def test_search_with_exclude_ids(self, repo: QdrantFactRepository, mock_client: AsyncMock) -> None:
        mock_result = MagicMock()
        mock_result.points = []
        mock_client.query_points.return_value = mock_result

        exclude = [uuid.uuid4()]
        await repo.search_similar(embedding=_make_embedding(), exclude_ids=exclude)

        call_kwargs = mock_client.query_points.call_args.kwargs
        assert call_kwargs["query_filter"] is not None

    async def test_empty_results(self, repo: QdrantFactRepository, mock_client: AsyncMock) -> None:
        mock_result = MagicMock()
        mock_result.points = []
        mock_client.query_points.return_value = mock_result

        results = await repo.search_similar(embedding=_make_embedding())
        assert results == []


class TestFindMostSimilar:
    async def test_returns_best_match(self, repo: QdrantFactRepository, mock_client: AsyncMock) -> None:
        fact_id = uuid.uuid4()
        mock_point = MagicMock()
        mock_point.id = str(fact_id)
        mock_point.score = 0.95
        mock_point.payload = {}

        mock_result = MagicMock()
        mock_result.points = [mock_point]
        mock_client.query_points.return_value = mock_result

        result = await repo.find_most_similar(embedding=_make_embedding())
        assert result is not None
        assert result.fact_id == fact_id

    async def test_returns_none_when_no_match(self, repo: QdrantFactRepository, mock_client: AsyncMock) -> None:
        mock_result = MagicMock()
        mock_result.points = []
        mock_client.query_points.return_value = mock_result

        result = await repo.find_most_similar(embedding=_make_embedding())
        assert result is None


class TestDelete:
    async def test_delete_single(self, repo: QdrantFactRepository, mock_client: AsyncMock) -> None:
        fact_id = uuid.uuid4()
        await repo.delete(fact_id)

        mock_client.delete.assert_called_once()
        call_kwargs = mock_client.delete.call_args.kwargs
        assert call_kwargs["collection_name"] == FACTS_COLLECTION
        assert str(fact_id) in call_kwargs["points_selector"]

    async def test_delete_batch(self, repo: QdrantFactRepository, mock_client: AsyncMock) -> None:
        ids = [uuid.uuid4(), uuid.uuid4()]
        await repo.delete_batch(ids)

        mock_client.delete.assert_called_once()
        selector = mock_client.delete.call_args.kwargs["points_selector"]
        assert len(selector) == 2

    async def test_delete_batch_empty(self, repo: QdrantFactRepository, mock_client: AsyncMock) -> None:
        await repo.delete_batch([])
        mock_client.delete.assert_not_called()


class TestUpdateNodeIds:
    async def test_update_payload(self, repo: QdrantFactRepository, mock_client: AsyncMock) -> None:
        fact_id = uuid.uuid4()
        node_ids = [uuid.uuid4(), uuid.uuid4()]

        await repo.update_node_ids(fact_id, node_ids)

        mock_client.set_payload.assert_called_once()
        call_kwargs = mock_client.set_payload.call_args.kwargs
        assert call_kwargs["collection_name"] == FACTS_COLLECTION
        assert len(call_kwargs["payload"]["node_ids"]) == 2


class TestCount:
    async def test_count(self, repo: QdrantFactRepository, mock_client: AsyncMock) -> None:
        mock_info = MagicMock()
        mock_info.points_count = 42
        mock_client.get_collection.return_value = mock_info

        count = await repo.count()
        assert count == 42


class TestSearchByNode:
    async def test_filters_by_node_id(self, repo: QdrantFactRepository, mock_client: AsyncMock) -> None:
        node_id = uuid.uuid4()
        mock_result = MagicMock()
        mock_result.points = []
        mock_client.query_points.return_value = mock_result

        await repo.search_by_node(embedding=_make_embedding(), node_id=node_id)

        call_kwargs = mock_client.query_points.call_args.kwargs
        assert call_kwargs["query_filter"] is not None


class TestBuildFilter:
    def test_no_filters(self, repo: QdrantFactRepository) -> None:
        result = repo._build_filter()
        assert result is None

    def test_fact_type_only(self, repo: QdrantFactRepository) -> None:
        result = repo._build_filter(fact_type="claim")
        assert result is not None
        assert result.must is not None

    def test_exclude_ids_only(self, repo: QdrantFactRepository) -> None:
        result = repo._build_filter(exclude_ids=[uuid.uuid4()])
        assert result is not None
        assert result.must_not is not None

    def test_both_filters(self, repo: QdrantFactRepository) -> None:
        result = repo._build_filter(fact_type="claim", exclude_ids=[uuid.uuid4()])
        assert result is not None
        assert result.must is not None
        assert result.must_not is not None
