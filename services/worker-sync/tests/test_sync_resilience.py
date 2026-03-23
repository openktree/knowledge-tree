"""Tests for sync engine resilience: inner savepoints, safe watermarks, DLQ."""

from __future__ import annotations

from kt_db.write_models import SyncFailure


def test_sync_failure_model():
    """SyncFailure model has expected table name and columns."""
    assert SyncFailure.__tablename__ == "sync_failures"
    cols = {c.name for c in SyncFailure.__table__.columns}
    assert "table_name" in cols
    assert "record_key" in cols
    assert "error_message" in cols
    assert "retry_count" in cols
    assert "status" in cols
    assert "next_retry_at" in cols


def test_sync_failure_indexes():
    """SyncFailure has indexes on next_retry_at and status."""
    index_names = {idx.name for idx in SyncFailure.__table__.indexes}
    assert "ix_sync_failures_next_retry_at" in index_names
    assert "ix_sync_failures_status" in index_names


def test_import_new_settings():
    """New settings fields exist with correct defaults."""
    from kt_config.settings import Settings

    s = Settings(
        database_url="postgresql+asyncpg://x:x@localhost/x",
        write_database_url="postgresql+asyncpg://x:x@localhost/x",
    )
    assert s.sync_max_retries == 5
    assert s.sync_retry_base_seconds == 60


def test_sync_engine_imports():
    """SyncEngine can be imported with new DLQ dependencies."""
    from kt_worker_sync.sync_engine import SyncEngine

    assert hasattr(SyncEngine, "_record_failure")
    assert hasattr(SyncEngine, "_clear_failure")
    assert hasattr(SyncEngine, "_retry_failed_syncs")
    assert hasattr(SyncEngine, "_retry_one")
