"""Integration tests for SourceRepository insights methods."""

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from kt_db.models import RawSource
from kt_db.repositories.sources import SourceRepository

pytestmark = pytest.mark.asyncio


def _make_source(
    *,
    uri: str = "https://example.com/page",
    is_full_text: bool = True,
    fetch_attempted: bool = False,
    fetch_error: str | None = None,
    is_super_source: bool = False,
    retrieved_at: datetime | None = None,
) -> RawSource:
    """Helper to create a RawSource with sensible defaults."""
    return RawSource(
        id=uuid.uuid4(),
        uri=uri,
        title="Test",
        raw_content="content",
        content_hash=uuid.uuid4().hex,
        provider_id="test",
        is_full_text=is_full_text,
        fetch_attempted=fetch_attempted,
        fetch_error=fetch_error,
        is_super_source=is_super_source,
        retrieved_at=retrieved_at or datetime.now(timezone.utc).replace(tzinfo=None),
    )


# ── get_insights_summary ────────────────────────────────────────────


async def test_insights_summary_empty(db_session):
    """Summary on a fresh DB returns zeroes (or at least valid counts)."""
    repo = SourceRepository(db_session)
    # Use a future timestamp so existing test data is excluded
    future = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=3650)
    result = await repo.get_insights_summary(since=future)
    assert result["total_count"] == 0
    assert result["failed_count"] == 0
    assert result["pending_super_count"] == 0


async def test_insights_summary_counts(db_session):
    """Summary correctly counts failed, pending-super, and total sources."""
    repo = SourceRepository(db_session)
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    # Successful fetch
    db_session.add(_make_source(uri="https://a.com/ok", is_full_text=True, retrieved_at=now))
    # Failed fetch
    db_session.add(
        _make_source(
            uri="https://b.com/fail",
            is_full_text=False,
            fetch_attempted=True,
            fetch_error="timeout",
            retrieved_at=now,
        )
    )
    # Pending super source
    db_session.add(
        _make_source(
            uri="https://c.com/super",
            is_super_source=True,
            is_full_text=False,
            fetch_attempted=False,
            retrieved_at=now,
        )
    )
    await db_session.flush()

    result = await repo.get_insights_summary(since=now - timedelta(seconds=5))
    assert result["total_count"] >= 3
    assert result["failed_count"] >= 1
    assert result["pending_super_count"] >= 1


# ── get_top_failed_domains ──────────────────────────────────────────


async def test_top_failed_domains(db_session):
    """Failed domains are extracted and ranked by failure count."""
    repo = SourceRepository(db_session)
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    # Add 3 failures for domain-a.com
    for i in range(3):
        db_session.add(
            _make_source(
                uri=f"https://domain-a.com/page{i}",
                is_full_text=False,
                fetch_attempted=True,
                fetch_error="403",
                retrieved_at=now,
            )
        )
    # Add 1 failure for domain-b.com
    db_session.add(
        _make_source(
            uri="https://domain-b.com/page",
            is_full_text=False,
            fetch_attempted=True,
            fetch_error="timeout",
            retrieved_at=now,
        )
    )
    await db_session.flush()

    result = await repo.get_top_failed_domains(since=now - timedelta(seconds=5))
    domains = {r["domain"]: r["failure_count"] for r in result}
    assert "domain-a.com" in domains
    assert domains["domain-a.com"] >= 3
    assert "domain-b.com" in domains


async def test_top_failed_domains_empty(db_session):
    """No failures returns an empty list."""
    repo = SourceRepository(db_session)
    future = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=3650)
    result = await repo.get_top_failed_domains(since=future)
    assert result == []


# ── get_common_fetch_errors ─────────────────────────────────────────


async def test_common_fetch_errors(db_session):
    """Errors are grouped and counted correctly."""
    repo = SourceRepository(db_session)
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    for i in range(4):
        db_session.add(
            _make_source(
                uri=f"https://err.com/common{i}",
                is_full_text=False,
                fetch_attempted=True,
                fetch_error="Connection refused",
                retrieved_at=now,
            )
        )
    db_session.add(
        _make_source(
            uri="https://err.com/rare",
            is_full_text=False,
            fetch_attempted=True,
            fetch_error="DNS resolution failed",
            retrieved_at=now,
        )
    )
    await db_session.flush()

    result = await repo.get_common_fetch_errors(since=now - timedelta(seconds=5))
    groups = {r["error_group"]: r["count"] for r in result}
    assert "Connection refused" in groups
    assert groups["Connection refused"] >= 4


async def test_common_fetch_errors_empty(db_session):
    """No errors returns an empty list."""
    repo = SourceRepository(db_session)
    future = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=3650)
    result = await repo.get_common_fetch_errors(since=future)
    assert result == []


# ── get_failures_per_day ────────────────────────────────────────────


async def test_failures_per_day(db_session):
    """Daily failure counts are aggregated correctly."""
    repo = SourceRepository(db_session)
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    for i in range(2):
        db_session.add(
            _make_source(
                uri=f"https://daily.com/today{i}",
                is_full_text=False,
                fetch_attempted=True,
                fetch_error="500",
                retrieved_at=now,
            )
        )
    await db_session.flush()

    result = await repo.get_failures_per_day(since=now - timedelta(seconds=5))
    assert len(result) >= 1
    today_str = now.strftime("%Y-%m-%d")
    days = {r["day"]: r["failure_count"] for r in result}
    assert today_str in days
    assert days[today_str] >= 2


async def test_failures_per_day_empty(db_session):
    """No failures returns an empty list."""
    repo = SourceRepository(db_session)
    future = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=3650)
    result = await repo.get_failures_per_day(since=future)
    assert result == []


# ── since filter ────────────────────────────────────────────────────


async def test_since_filter_excludes_old_data(db_session):
    """The since parameter correctly excludes old sources."""
    repo = SourceRepository(db_session)
    old = datetime(2020, 1, 1)
    recent = datetime.now(timezone.utc).replace(tzinfo=None)

    db_session.add(
        _make_source(
            uri="https://old.com/page",
            is_full_text=False,
            fetch_attempted=True,
            fetch_error="old error",
            retrieved_at=old,
        )
    )
    await db_session.flush()

    # With a recent since, old data should be excluded
    summary = await repo.get_insights_summary(since=recent - timedelta(seconds=5))
    domains = await repo.get_top_failed_domains(since=recent - timedelta(seconds=5))
    errors = await repo.get_common_fetch_errors(since=recent - timedelta(seconds=5))
    daily = await repo.get_failures_per_day(since=recent - timedelta(seconds=5))

    # The old source should not show up with domain "old.com"
    old_domains = [d["domain"] for d in domains]
    assert "old.com" not in old_domains

    old_errors = [e["error_group"] for e in errors]
    assert "old error" not in old_errors

    old_days = [d["day"] for d in daily]
    assert "2020-01-01" not in old_days
