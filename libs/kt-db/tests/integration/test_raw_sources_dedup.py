"""Regression tests for the raw_sources content_hash divergence fix.

These tests pin the contract changes that prevent worker-sync from wedging
on a duplicate-hash poison row:

1. graph-db.raw_sources.content_hash is no longer UNIQUE — two rows may
   share a hash, and worker-sync's id-keyed upsert is correct as written.
2. graph-db.raw_sources.id has no Python default — every caller must pass
   the deterministic id from kt_db.keys.uri_to_source_id().
3. WriteSourceRepository.update_content does not mutate content_hash on an
   already-existing row, even when the new content would hash differently.
"""

from __future__ import annotations

import pytest

from kt_db.keys import uri_to_source_id
from kt_db.models import RawSource
from kt_db.repositories.write_sources import WriteSourceRepository


@pytest.mark.asyncio(loop_scope="session")
async def test_raw_sources_allows_duplicate_content_hash(db_session) -> None:
    """Two graph-db RawSource rows may share content_hash after the migration.

    This is the contract that lets worker-sync's `ON CONFLICT (id) DO UPDATE`
    upsert be correct: it never has to reconcile a secondary unique index.
    """
    shared_hash = "deadbeef" * 8  # 64 chars
    a = RawSource(
        id=uri_to_source_id("https://example.com/a"),
        uri="https://example.com/a",
        content_hash=shared_hash,
        provider_id="test",
    )
    b = RawSource(
        id=uri_to_source_id("https://example.com/b"),
        uri="https://example.com/b",
        content_hash=shared_hash,
        provider_id="test",
    )
    db_session.add(a)
    db_session.add(b)
    await db_session.flush()

    # Both rows survive — no IntegrityError on the secondary index.
    assert a.id != b.id
    assert a.content_hash == b.content_hash


@pytest.mark.asyncio(loop_scope="session")
async def test_raw_source_id_has_no_python_default() -> None:
    """RawSource.id must be passed explicitly — no uuid4 default leak.

    The Python default used to silently produce divergent ids whenever a
    caller forgot to pass uri_to_source_id(uri). Removing the default makes
    that mistake impossible at construction time.
    """
    rs = RawSource(
        uri="https://example.com/x",
        content_hash="x" * 64,
        provider_id="test",
    )
    # No Python default → id is None / unset until the caller assigns it
    # or until INSERT executes (which would then fail because PK is NOT NULL).
    assert getattr(rs, "id", None) is None


@pytest.mark.asyncio(loop_scope="session")
async def test_update_content_does_not_mutate_content_hash(write_db_session) -> None:
    """WriteSourceRepository.update_content keeps content_hash immutable.

    Mutating the hash on a row id that worker-sync has already propagated
    is the cross-DB drift vector that wedged sync. The repository now
    refreshes raw_content / is_full_text / content_type only.
    """
    repo = WriteSourceRepository(write_db_session)
    original = await repo.create_or_get(
        uri="https://example.com/immutable",
        title="t",
        raw_content="initial body",
        provider_id="test",
    )
    original_hash = original.content_hash
    original_id = original.id

    updated = await repo.update_content(
        original_id,
        new_content="completely different body that hashes to a new value",
        is_full_text=True,
        content_type="text/plain",
    )
    assert updated is True

    # Re-fetch and verify the hash did not change.
    refreshed = await repo.get_by_id(original_id)
    assert refreshed is not None
    assert refreshed.content_hash == original_hash
    assert refreshed.raw_content == "completely different body that hashes to a new value"
    assert refreshed.is_full_text is True
    assert refreshed.content_type == "text/plain"


@pytest.mark.asyncio(loop_scope="session")
async def test_write_source_create_or_get_uses_deterministic_id(write_db_session) -> None:
    """WriteSourceRepository.create_or_get derives id from URI deterministically.

    This guarantees that sync_engine resolving a fact source by hash
    (`write_repo.get_by_content_hash(...)`) and then using `real_source.id`
    against graph-db lands on the same row that `_sync_raw_sources` produced.
    """
    repo = WriteSourceRepository(write_db_session)
    uri = "https://example.com/deterministic"
    a = await repo.create_or_get(
        uri=uri,
        title="t",
        raw_content="body",
        provider_id="test",
    )
    assert a.id == uri_to_source_id(uri)

    # Calling again returns the same row, same id.
    b = await repo.create_or_get(
        uri=uri,
        title="t2",
        raw_content="body",
        provider_id="test",
    )
    assert b.id == a.id
