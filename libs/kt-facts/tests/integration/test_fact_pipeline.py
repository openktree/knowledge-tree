import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from kt_db.models import RawSource
from kt_facts.pipeline import DecompositionPipeline

pytestmark = pytest.mark.asyncio


async def test_full_pipeline_with_mocked_gateway(db_session, write_session):
    """Feed sample text through the single-call pipeline, verify facts are stored."""
    # Create a RawSource in the DB
    raw_source = RawSource(
        id=uuid.uuid4(),
        uri="https://example.com/pipeline-test",
        title="Pipeline Test",
        raw_content="Water boils at 100 degrees Celsius at sea level.\n\nIce melts at 0 degrees Celsius.",
        content_hash="pipeline_test_hash_" + str(uuid.uuid4()),
        provider_id="test",
    )
    db_session.add(raw_source)
    await db_session.flush()

    # Mock the ModelGateway
    mock_gateway = MagicMock()
    mock_gateway.decomposition_model = "test-model"
    mock_gateway.decomposition_thinking_level = None

    # generate_json returns facts with attribution in a single call
    mock_gateway.generate_json = AsyncMock(
        return_value={
            "facts": [
                {
                    "content": "Water boils at 100 degrees Celsius at sea level.",
                    "fact_type": "measurement",
                    "who": None,
                    "where": "example.com",
                    "when": None,
                    "context": "Science article",
                },
                {
                    "content": "Ice melts at 0 degrees Celsius.",
                    "fact_type": "measurement",
                    "who": None,
                    "where": "example.com",
                    "when": None,
                    "context": "Science article",
                },
            ]
        }
    )

    # Mock the embedding service
    mock_embedding_service = AsyncMock()
    fake_embeddings = [[float(i) / 3072] * 3072 for i in range(10)]

    async def mock_embed_batch(texts: list[str]) -> list[list[float]]:
        return fake_embeddings[: len(texts)]

    mock_embedding_service.embed_batch = mock_embed_batch

    pipeline = DecompositionPipeline(mock_gateway)
    result = await pipeline.decompose(
        raw_sources=[raw_source],
        concept="water properties",
        session=db_session,
        embedding_service=mock_embedding_service,
        write_session=write_session,
    )

    assert len(result.facts) >= 1
    # Verify facts were written to write-db
    from kt_db.repositories.write_facts import WriteFactRepository

    write_repo = WriteFactRepository(write_session)
    for fact in result.facts:
        db_fact = await write_repo.get_by_id(fact.id)
        assert db_fact is not None

    # Verify a small number of LLM calls (decomposition + entity extraction, not 50+)
    assert mock_gateway.generate_json.call_count <= 5


async def test_pipeline_skips_empty_sources(db_session):
    """Sources with no raw_content should be skipped."""
    raw_source = RawSource(
        id=uuid.uuid4(),
        uri="https://example.com/empty",
        title="Empty Source",
        raw_content=None,
        content_hash="empty_hash_" + str(uuid.uuid4()),
        provider_id="test",
    )
    db_session.add(raw_source)
    await db_session.flush()

    mock_gateway = MagicMock()
    mock_gateway.decomposition_model = "test-model"
    mock_gateway.decomposition_thinking_level = None

    pipeline = DecompositionPipeline(mock_gateway)
    result = await pipeline.decompose(
        raw_sources=[raw_source],
        concept="test",
        session=db_session,
    )
    assert result.facts == []


async def test_pipeline_handles_empty_source_list(db_session):
    """Empty source list should return empty facts."""
    mock_gateway = MagicMock()
    mock_gateway.decomposition_model = "test-model"
    mock_gateway.decomposition_thinking_level = None

    pipeline = DecompositionPipeline(mock_gateway)
    result = await pipeline.decompose(
        raw_sources=[],
        concept="test",
        session=db_session,
    )
    assert result.facts == []


async def test_pipeline_multiple_sources(db_session, write_session):
    """Multiple sources should each get one LLM call and produce facts."""
    sources = []
    for i in range(3):
        source = RawSource(
            id=uuid.uuid4(),
            uri=f"https://example.com/source-{i}",
            title=f"Source {i}",
            raw_content=f"Fact {i} about testing.",
            content_hash=f"hash_{i}_" + str(uuid.uuid4()),
            provider_id="test",
        )
        db_session.add(source)
        sources.append(source)
    await db_session.flush()

    mock_gateway = MagicMock()
    mock_gateway.decomposition_model = "test-model"
    mock_gateway.decomposition_thinking_level = None

    call_idx = 0

    async def mock_generate_json(**kwargs):  # type: ignore[no-untyped-def]
        nonlocal call_idx
        call_idx += 1
        return {
            "facts": [
                {
                    "content": f"Test fact from call {call_idx}.",
                    "fact_type": "claim",
                    "who": None,
                    "where": None,
                    "when": None,
                    "context": None,
                }
            ]
        }

    mock_gateway.generate_json = AsyncMock(side_effect=mock_generate_json)

    mock_embedding_service = AsyncMock()
    fake_embeddings = [[float(i) / 3072] * 3072 for i in range(10)]

    async def mock_embed_batch(texts: list[str]) -> list[list[float]]:
        return fake_embeddings[: len(texts)]

    mock_embedding_service.embed_batch = mock_embed_batch

    pipeline = DecompositionPipeline(mock_gateway)
    result = await pipeline.decompose(
        raw_sources=sources,
        concept="testing",
        session=db_session,
        embedding_service=mock_embedding_service,
        write_session=write_session,
    )

    # One LLM call per source (3 total, not 30+)
    # generate_json is called for extraction too, so ≥ 3
    assert mock_gateway.generate_json.call_count >= 3
    assert len(result.facts) >= 1
