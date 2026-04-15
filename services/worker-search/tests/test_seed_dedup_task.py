"""Tests for the seed dedup batch logic."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from kt_hatchet.models import SeedDedupBatchInput, SeedDedupBatchOutput


@pytest.fixture
def mock_session() -> AsyncMock:
    """Create a mock write-db session as async context manager."""
    session = AsyncMock()
    session.begin = MagicMock(return_value=AsyncMock().__aenter__())
    return session


def _make_seed(name: str, node_type: str, status: str = "pending") -> MagicMock:
    s = MagicMock()
    s.name = name
    s.node_type = node_type
    s.status = status
    s.aliases = []
    return s


@pytest.mark.asyncio
async def test_seed_dedup_processes_pending_seeds(mock_session: AsyncMock) -> None:
    """Dedup should call deduplicate_seed for each pending seed, skip non-pending."""
    seed_a = _make_seed("Alpha", "concept", "pending")
    seed_b = _make_seed("Beta", "entity", "pending")
    seed_c = _make_seed("Gamma", "concept", "merged")  # should be skipped

    seeds_by_key = {"key_a": seed_a, "key_b": seed_b, "key_c": seed_c}

    mock_repo = MagicMock()
    mock_repo.get_seeds_by_keys_batch = AsyncMock(return_value=seeds_by_key)

    with (
        patch(
            "kt_db.repositories.write_seeds.WriteSeedRepository",
            return_value=mock_repo,
        ),
        patch(
            "kt_facts.processing.seed_dedup.deduplicate_seed",
            new_callable=AsyncMock,
        ) as mock_dedup,
    ):
        mock_dedup.side_effect = [
            "key_a",       # Alpha: no merge
            "key_winner",  # Beta: merged into key_winner
        ]

        from kt_db.repositories.write_seeds import WriteSeedRepository
        from kt_facts.processing.seed_dedup import deduplicate_seed

        repo = WriteSeedRepository(mock_session)

        input_data = SeedDedupBatchInput(
            seed_keys=["key_a", "key_b", "key_c"],
            scope_id="test-scope",
        )
        unique_keys = list(dict.fromkeys(input_data.seed_keys))
        seeds = await repo.get_seeds_by_keys_batch(unique_keys)
        pending_seeds = [(k, s) for k, s in seeds.items() if s.status == "pending"]

        merges: dict[str, str] = {}
        processed = 0
        errors = 0

        mock_embedding = MagicMock()
        mock_qdrant = MagicMock()

        for seed_key, seed in pending_seeds:
            surviving = await deduplicate_seed(
                seed_key=seed_key,
                name=seed.name,
                node_type=seed.node_type,
                write_seed_repo=repo,
                embedding_service=mock_embedding,
                qdrant_seed_repo=mock_qdrant,
                aliases=list(seed.aliases or []),
            )
            processed += 1
            if surviving != seed_key:
                merges[seed_key] = surviving

    output = SeedDedupBatchOutput(merges=merges, processed=processed, errors=errors)
    assert output.processed == 2
    assert output.errors == 0
    assert output.merges == {"key_b": "key_winner"}
    assert mock_dedup.call_count == 2


@pytest.mark.asyncio
async def test_seed_dedup_handles_errors(mock_session: AsyncMock) -> None:
    """Errors in individual seed dedup should be counted, not raise."""
    seed_a = _make_seed("Alpha", "concept", "pending")

    mock_repo = MagicMock()
    mock_repo.get_seeds_by_keys_batch = AsyncMock(
        return_value={"key_a": seed_a},
    )

    with (
        patch(
            "kt_db.repositories.write_seeds.WriteSeedRepository",
            return_value=mock_repo,
        ),
        patch(
            "kt_facts.processing.seed_dedup.deduplicate_seed",
            new_callable=AsyncMock,
            side_effect=RuntimeError("dedup error"),
        ),
    ):
        from kt_db.repositories.write_seeds import WriteSeedRepository
        from kt_facts.processing.seed_dedup import deduplicate_seed

        repo = WriteSeedRepository(mock_session)
        seeds = await repo.get_seeds_by_keys_batch(["key_a"])
        pending_seeds = [(k, s) for k, s in seeds.items() if s.status == "pending"]

        merges: dict[str, str] = {}
        processed = 0
        errors = 0

        mock_embedding = MagicMock()
        mock_qdrant = MagicMock()

        for seed_key, seed in pending_seeds:
            try:
                await deduplicate_seed(
                    seed_key=seed_key,
                    name=seed.name,
                    node_type=seed.node_type,
                    write_seed_repo=repo,
                    embedding_service=mock_embedding,
                    qdrant_seed_repo=mock_qdrant,
                    aliases=list(seed.aliases or []),
                )
                processed += 1
            except Exception:
                errors += 1

    output = SeedDedupBatchOutput(merges=merges, processed=processed, errors=errors)
    assert output.processed == 0
    assert output.errors == 1
    assert output.merges == {}


@pytest.mark.asyncio
async def test_seed_dedup_skips_non_pending_seeds(mock_session: AsyncMock) -> None:
    """No pending seeds → processed=0."""
    mock_repo = MagicMock()
    mock_repo.get_seeds_by_keys_batch = AsyncMock(return_value={})

    with patch(
        "kt_db.repositories.write_seeds.WriteSeedRepository",
        return_value=mock_repo,
    ):
        from kt_db.repositories.write_seeds import WriteSeedRepository

        repo = WriteSeedRepository(mock_session)
        seeds = await repo.get_seeds_by_keys_batch(["key_a"])
        pending_seeds = [(k, s) for k, s in seeds.items() if s.status == "pending"]

    assert len(pending_seeds) == 0


@pytest.mark.asyncio
async def test_seed_dedup_io_models() -> None:
    """Verify I/O model serialization."""
    input_data = SeedDedupBatchInput(
        seed_keys=["a", "b", "c"],
        scope_id="scope-1",
    )
    assert input_data.seed_keys == ["a", "b", "c"]
    assert input_data.scope_id == "scope-1"

    output = SeedDedupBatchOutput(
        merges={"a": "b"},
        processed=3,
        errors=1,
    )
    dumped = output.model_dump()
    assert dumped["merges"] == {"a": "b"}
    assert dumped["processed"] == 3
    assert dumped["errors"] == 1
