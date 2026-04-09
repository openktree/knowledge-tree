"""Unit tests for the post-job fact insert path.

Under the new design, ``insert_facts_pending`` just writes each fact
to the write-db with ``dedup_status='pending'``. Deduplication is done
later by the ``dedup_pending_facts_wf`` Hatchet workflow — the tests
for that live next to the workflow itself and in
``libs/kt-facts/tests/test_dedup_workflow_support.py``.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock

import pytest

from kt_facts.processing.dedup import (
    InsertFactsPendingResult,
    _threshold_for_type,
    insert_facts_pending,
)


def _make_write_fact_repo() -> AsyncMock:
    """Create a mock :class:`WriteFactRepository`."""
    repo = AsyncMock()
    repo.upsert = AsyncMock()
    return repo


@pytest.mark.asyncio
async def test_insert_pending_no_qdrant_writes() -> None:
    """``insert_facts_pending`` only touches write-db — no Qdrant, no embeds."""
    mock_write_fact_repo = _make_write_fact_repo()

    items = [
        ("Water boils at 100C", "measurement"),
        ("Ice melts at 0C", "measurement"),
    ]

    result = await insert_facts_pending(items, write_fact_repo=mock_write_fact_repo)

    assert isinstance(result, InsertFactsPendingResult)
    assert len(result) == 2
    assert result.new_qdrant_ids == []
    assert all(isinstance(fid, uuid.UUID) for fid in result.fact_ids)
    assert mock_write_fact_repo.upsert.call_count == 2

    # Each upsert should carry the matching content/fact_type.
    call_contents = {call.kwargs["content"] for call in mock_write_fact_repo.upsert.call_args_list}
    assert call_contents == {"Water boils at 100C", "Ice melts at 0C"}


@pytest.mark.asyncio
async def test_insert_pending_preserves_input_order() -> None:
    mock_write_fact_repo = _make_write_fact_repo()

    items = [(f"content-{i}", "claim") for i in range(5)]

    result = await insert_facts_pending(items, write_fact_repo=mock_write_fact_repo)

    assert len(result.fact_ids) == 5
    # Correspondence between input position and upsert calls.
    upsert_contents = [call.kwargs["content"] for call in mock_write_fact_repo.upsert.call_args_list]
    assert upsert_contents == [f"content-{i}" for i in range(5)]

    # Each fact_id returned should match the id passed to upsert at the same
    # positional index.
    upsert_ids = [call.kwargs["fact_id"] for call in mock_write_fact_repo.upsert.call_args_list]
    assert upsert_ids == result.fact_ids


@pytest.mark.asyncio
async def test_insert_pending_empty_input() -> None:
    mock_write_fact_repo = _make_write_fact_repo()

    result = await insert_facts_pending([], write_fact_repo=mock_write_fact_repo)

    assert len(result) == 0
    assert isinstance(result, InsertFactsPendingResult)
    mock_write_fact_repo.upsert.assert_not_called()


@pytest.mark.asyncio
async def test_insert_pending_requires_write_fact_repo() -> None:
    with pytest.raises(RuntimeError, match="write_fact_repo is required"):
        await insert_facts_pending(
            [("content", "claim")],
            write_fact_repo=None,
        )


def test_threshold_for_type_atomic_vs_compound() -> None:
    # Compound (quote/procedure/reference/code/account) → 0.85
    assert _threshold_for_type("quote") == 0.85
    # Atomic default → 0.92
    assert _threshold_for_type("measurement") == 0.92
    assert _threshold_for_type("claim") == 0.92
