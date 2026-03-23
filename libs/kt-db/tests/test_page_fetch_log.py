"""Unit tests for PageFetchLogRepository."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from kt_db.models import PageFetchLog
from kt_db.repositories.page_fetch_log import PageFetchLogRepository


def _make_entry(
    url: str = "https://example.com",
    fetched_at: datetime | None = None,
) -> PageFetchLog:
    """Create a PageFetchLog object for testing."""
    entry = PageFetchLog(
        id=uuid.uuid4(),
        url=url,
        fetched_at=fetched_at or datetime.now(UTC).replace(tzinfo=None),
        created_at=datetime.now(UTC).replace(tzinfo=None),
    )
    return entry


@pytest.mark.asyncio
async def test_is_fresh_no_entry():
    """URL not in log is not fresh."""
    session = MagicMock()
    result_mock = MagicMock()
    result_mock.scalar_one_or_none.return_value = None
    session.execute = AsyncMock(return_value=result_mock)

    repo = PageFetchLogRepository(session)
    assert await repo.is_fresh("https://unknown.com") is False


@pytest.mark.asyncio
async def test_is_fresh_recent_entry():
    """URL fetched recently is fresh."""
    entry = _make_entry(fetched_at=datetime.now(UTC).replace(tzinfo=None))

    session = MagicMock()
    result_mock = MagicMock()
    result_mock.scalar_one_or_none.return_value = entry
    session.execute = AsyncMock(return_value=result_mock)

    repo = PageFetchLogRepository(session)
    assert await repo.is_fresh("https://example.com", stale_days=30) is True


@pytest.mark.asyncio
async def test_is_fresh_stale_entry():
    """URL fetched long ago is stale (not fresh)."""
    old_date = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=60)
    entry = _make_entry(fetched_at=old_date)

    session = MagicMock()
    result_mock = MagicMock()
    result_mock.scalar_one_or_none.return_value = entry
    session.execute = AsyncMock(return_value=result_mock)

    repo = PageFetchLogRepository(session)
    assert await repo.is_fresh("https://example.com", stale_days=30) is False


@pytest.mark.asyncio
async def test_check_urls_freshness_mixed():
    """Batch check returns correct freshness for mixed URLs."""
    now = datetime.now(UTC).replace(tzinfo=None)
    old = now - timedelta(days=60)

    session = MagicMock()
    # Simulate returning two rows: one fresh, one stale
    row_fresh = MagicMock()
    row_fresh.url = "https://fresh.com"
    row_fresh.fetched_at = now

    row_stale = MagicMock()
    row_stale.url = "https://stale.com"
    row_stale.fetched_at = old

    result_mock = MagicMock()
    result_mock.all.return_value = [row_fresh, row_stale]
    session.execute = AsyncMock(return_value=result_mock)

    repo = PageFetchLogRepository(session)
    freshness = await repo.check_urls_freshness(
        ["https://fresh.com", "https://stale.com", "https://unknown.com"],
        stale_days=30,
    )

    assert freshness["https://fresh.com"] is True
    assert freshness["https://stale.com"] is False
    assert freshness["https://unknown.com"] is False


@pytest.mark.asyncio
async def test_check_urls_freshness_empty():
    """Empty URL list returns empty dict."""
    session = MagicMock()
    repo = PageFetchLogRepository(session)
    result = await repo.check_urls_freshness([], stale_days=30)
    assert result == {}
