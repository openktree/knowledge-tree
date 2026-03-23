import uuid
from dataclasses import dataclass
from unittest.mock import AsyncMock, patch

import pytest

from kt_facts.processing.dedup import deduplicate_facts


@dataclass
class _FakeQdrantResult:
    fact_id: uuid.UUID
    score: float = 0.95


def _make_write_fact_repo() -> AsyncMock:
    """Create a mock WriteFactRepository for write-db."""
    repo = AsyncMock()
    repo.upsert = AsyncMock()
    return repo


@pytest.mark.asyncio
async def test_batch_creates_new_facts():
    """When no similar facts exist in Qdrant, all should be created."""
    fake_embeddings = [[0.1] * 3072, [0.2] * 3072]

    mock_embedding_service = AsyncMock()
    mock_embedding_service.embed_batch.return_value = fake_embeddings

    mock_repo = AsyncMock()
    mock_write_fact_repo = _make_write_fact_repo()

    mock_qdrant_client = AsyncMock()
    mock_qdrant_fact_repo = AsyncMock()
    mock_qdrant_fact_repo.find_most_similar.return_value = None

    items = [
        ("Water boils at 100C", "measurement"),
        ("Ice melts at 0C", "measurement"),
    ]

    with patch("kt_qdrant.repositories.facts.QdrantFactRepository", return_value=mock_qdrant_fact_repo):
        results = await deduplicate_facts(
            items,
            mock_repo,
            mock_embedding_service,
            qdrant_client=mock_qdrant_client,
            write_fact_repo=mock_write_fact_repo,
        )

    assert len(results) == 2
    assert all(is_new for _, is_new in results)

    # Single embed_batch call with both texts
    mock_embedding_service.embed_batch.assert_called_once_with(
        ["Water boils at 100C", "Ice melts at 0C"],
    )
    mock_embedding_service.embed_text.assert_not_called()
    # Qdrant searched per fact
    assert mock_qdrant_fact_repo.find_most_similar.call_count == 2
    # New facts written to write-db, not graph-db
    assert mock_write_fact_repo.upsert.call_count == 2
    mock_repo.create.assert_not_called()


@pytest.mark.asyncio
async def test_batch_finds_existing_fact():
    """Mixed case: one fact matches an existing in Qdrant, one is new."""
    fake_embeddings = [[0.1] * 3072, [0.2] * 3072]
    existing_id = uuid.uuid4()

    mock_embedding_service = AsyncMock()
    mock_embedding_service.embed_batch.return_value = fake_embeddings

    mock_repo = AsyncMock()
    mock_write_fact_repo = _make_write_fact_repo()

    mock_qdrant_client = AsyncMock()
    mock_qdrant_fact_repo = AsyncMock()
    mock_qdrant_fact_repo.find_most_similar.side_effect = [
        _FakeQdrantResult(fact_id=existing_id),
        None,
    ]

    items = [
        ("Water boils at 100C", "measurement"),
        ("Ice melts at 0C", "measurement"),
    ]

    with patch("kt_qdrant.repositories.facts.QdrantFactRepository", return_value=mock_qdrant_fact_repo):
        results = await deduplicate_facts(
            items,
            mock_repo,
            mock_embedding_service,
            qdrant_client=mock_qdrant_client,
            write_fact_repo=mock_write_fact_repo,
        )

    assert results[0] == (existing_id, False)
    assert results[1][1] is True  # new fact
    # upsert only called for the new fact
    mock_write_fact_repo.upsert.assert_called_once()


@pytest.mark.asyncio
async def test_batch_type_aware_thresholds():
    """Compound types use 0.85 threshold, atomic types use 0.92."""
    fake_embeddings = [[0.1] * 3072, [0.2] * 3072]

    mock_embedding_service = AsyncMock()
    mock_embedding_service.embed_batch.return_value = fake_embeddings

    mock_repo = AsyncMock()
    mock_write_fact_repo = _make_write_fact_repo()

    mock_qdrant_client = AsyncMock()
    mock_qdrant_fact_repo = AsyncMock()
    mock_qdrant_fact_repo.find_most_similar.return_value = None

    items = [
        ("A long quote from a book", "quote"),  # compound → 0.85
        ("Water boils at 100C", "measurement"),  # atomic  → 0.92
    ]

    with patch("kt_qdrant.repositories.facts.QdrantFactRepository", return_value=mock_qdrant_fact_repo):
        await deduplicate_facts(
            items,
            mock_repo,
            mock_embedding_service,
            qdrant_client=mock_qdrant_client,
            write_fact_repo=mock_write_fact_repo,
        )

    calls = mock_qdrant_fact_repo.find_most_similar.call_args_list
    assert calls[0].kwargs["score_threshold"] == 0.85  # quote = compound
    assert calls[1].kwargs["score_threshold"] == 0.92  # measurement = atomic


@pytest.mark.asyncio
async def test_batch_empty_input():
    """Empty input returns [] with no API or DB calls."""
    mock_embedding_service = AsyncMock()
    mock_repo = AsyncMock()

    results = await deduplicate_facts([], mock_repo, mock_embedding_service)

    assert results == []
    mock_embedding_service.embed_batch.assert_not_called()
    mock_repo.create.assert_not_called()


@pytest.mark.asyncio
async def test_batch_no_embedding_service():
    """Without embedding service, all facts are created without dedup."""
    mock_repo = AsyncMock()
    mock_write_fact_repo = _make_write_fact_repo()

    items = [
        ("Water boils at 100C", "measurement"),
        ("Ice melts at 0C", "measurement"),
    ]

    results = await deduplicate_facts(
        items,
        mock_repo,
        embedding_service=None,
        write_fact_repo=mock_write_fact_repo,
    )

    assert len(results) == 2
    assert all(is_new for _, is_new in results)
    assert mock_write_fact_repo.upsert.call_count == 2


@pytest.mark.asyncio
async def test_no_qdrant_client_logs_error():
    """Without Qdrant client, facts are created but an error is logged."""
    fake_embeddings = [[0.1] * 3072]

    mock_embedding_service = AsyncMock()
    mock_embedding_service.embed_batch.return_value = fake_embeddings

    mock_repo = AsyncMock()
    mock_write_fact_repo = _make_write_fact_repo()

    items = [("Water boils at 100C", "measurement")]

    results = await deduplicate_facts(
        items,
        mock_repo,
        mock_embedding_service,
        qdrant_client=None,
        write_fact_repo=mock_write_fact_repo,
    )

    # Fact is created (no dedup possible without Qdrant)
    assert len(results) == 1
    assert results[0][1] is True


@pytest.mark.asyncio
async def test_missing_write_fact_repo_raises():
    """Without write_fact_repo, creating new facts raises RuntimeError."""
    fake_embeddings = [[0.1] * 3072]

    mock_embedding_service = AsyncMock()
    mock_embedding_service.embed_batch.return_value = fake_embeddings

    mock_repo = AsyncMock()

    mock_qdrant_client = AsyncMock()
    mock_qdrant_fact_repo = AsyncMock()
    mock_qdrant_fact_repo.find_most_similar.return_value = None

    items = [("Water boils at 100C", "measurement")]

    with patch("kt_qdrant.repositories.facts.QdrantFactRepository", return_value=mock_qdrant_fact_repo):
        with pytest.raises(RuntimeError, match="write_fact_repo is required"):
            await deduplicate_facts(
                items,
                mock_repo,
                mock_embedding_service,
                qdrant_client=mock_qdrant_client,
                write_fact_repo=None,
            )
