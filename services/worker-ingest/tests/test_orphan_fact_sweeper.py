"""Unit tests for the orphan fact sweeper.

Tests the orphan detection query helper. The Hatchet task wrapper
(sweep_orphan_facts) requires a real Hatchet context and is tested
via integration / manual e2e.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from kt_worker_ingest.workflows.orphan_fact_sweeper import _find_orphan_fact_ids


@pytest.mark.asyncio
async def test_find_orphan_fact_ids_returns_uuids():
    """Query returns UUID list from result rows."""
    id1, id2 = uuid.uuid4(), uuid.uuid4()
    mock_result = MagicMock()
    mock_result.fetchall.return_value = [(id1,), (id2,)]

    session = AsyncMock()
    session.execute.return_value = mock_result

    cutoff = datetime.now(UTC).replace(tzinfo=None) - timedelta(minutes=5)
    result = await _find_orphan_fact_ids(session, cutoff, batch_size=100)

    assert result == [id1, id2]
    session.execute.assert_awaited_once()


@pytest.mark.asyncio
async def test_find_orphan_fact_ids_empty():
    """No orphans returns empty list."""
    mock_result = MagicMock()
    mock_result.fetchall.return_value = []

    session = AsyncMock()
    session.execute.return_value = mock_result

    result = await _find_orphan_fact_ids(session, datetime.now(UTC).replace(tzinfo=None), batch_size=100)
    assert result == []


@pytest.mark.asyncio
async def test_find_orphan_fact_ids_respects_batch_size():
    """Batch size is passed to the SQL query."""
    mock_result = MagicMock()
    mock_result.fetchall.return_value = []

    session = AsyncMock()
    session.execute.return_value = mock_result

    await _find_orphan_fact_ids(session, datetime.now(UTC).replace(tzinfo=None), batch_size=42)

    call_args = session.execute.call_args
    params = call_args[0][1] if len(call_args[0]) > 1 else call_args[1].get("params", {})
    assert params["batch_size"] == 42
