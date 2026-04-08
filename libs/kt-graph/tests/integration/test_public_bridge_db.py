"""Integration tests for the SQL paths in ``PublicGraphBridge``.

The unit tests in ``tests/test_public_bridge.py`` mock out the session to
exercise the snapshot/import logic without a database. These tests do the
opposite: they spin up a real write-db schema, drive the bridge against
it, and verify the actual SQL — most importantly the
``write_nodes.fact_ids && ARRAY[...]`` overlap query, which can't be
unit-tested.

Scope here is deliberately narrow:

* ``_load_linked_facts`` finds facts via ``write_fact_sources``.
* ``_load_linked_fact_sources`` returns matching provenance rows.
* ``_load_linked_nodes`` filters by ``node_type IN (concept, entity)``
  AND uses the array-overlap operator correctly.
* ``_upsert_raw_source`` returns the local row id on both insert and
  ON CONFLICT no-op paths.
* ``_match_or_create_node`` reports ``created=False`` and reuses the
  local node_uuid when the deterministic key already exists locally.

The Qdrant side stays mocked — concept similarity matching is exercised
by the unit tests.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.dialects.postgresql import insert as pg_insert

from kt_db.keys import key_to_uuid, make_node_key
from kt_db.write_models import WriteFact, WriteFactSource, WriteNode, WriteRawSource
from kt_graph.public_bridge import (
    CachedFactSourceSnapshot,
    CachedNodeSnapshot,
    CachedRawSourceSnapshot,
    PublicGraphBridge,
)


def _make_bridge() -> PublicGraphBridge:
    from types import SimpleNamespace
    from unittest.mock import MagicMock

    settings = SimpleNamespace(
        public_bridge_concept_match_threshold=0.93,
        public_cache_refresh_after_days=365,
    )
    return PublicGraphBridge(
        resolver=MagicMock(),
        qdrant_client=None,
        embedding_service=None,
        default_graph_id=uuid.uuid4(),
        settings=settings,
    )


async def _insert_raw_source(session, **overrides):
    rid = overrides.pop("id", uuid.uuid4())
    content_hash = overrides.pop("content_hash", uuid.uuid4().hex)
    stmt = pg_insert(WriteRawSource).values(
        id=rid,
        uri=overrides.get("uri", "https://example.com/x"),
        canonical_url=overrides.get("canonical_url", "https://example.com/x"),
        doi=overrides.get("doi"),
        title=overrides.get("title", "T"),
        raw_content=overrides.get("raw_content", "body"),
        content_hash=content_hash,
        provider_id=overrides.get("provider_id", "httpx"),
    )
    await session.execute(stmt)
    return rid, content_hash


async def _insert_fact(session, *, content="A claim.", fact_type="atomic"):
    fid = uuid.uuid4()
    stmt = pg_insert(WriteFact).values(id=fid, content=content, fact_type=fact_type)
    await session.execute(stmt)
    return fid


async def _link_fact_to_source(session, *, fact_id, content_hash, uri="https://example.com/x"):
    fsid = uuid.uuid4()
    stmt = pg_insert(WriteFactSource).values(
        id=fsid,
        fact_id=fact_id,
        raw_source_uri=uri,
        raw_source_content_hash=content_hash,
        raw_source_provider_id="httpx",
    )
    await session.execute(stmt)
    return fsid


async def _insert_node(session, *, concept, node_type, fact_ids: list[uuid.UUID]):
    key = make_node_key(node_type, concept)
    node_uuid = key_to_uuid(key)
    stmt = pg_insert(WriteNode).values(
        key=key,
        node_uuid=node_uuid,
        concept=concept,
        node_type=node_type,
        fact_ids=[str(f) for f in fact_ids] or None,
    )
    await session.execute(stmt)
    return key, node_uuid


# ---------------------------------------------------------------------------
# _load_linked_facts / _load_linked_fact_sources
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_load_linked_facts_returns_facts_for_source(write_db_session):
    bridge = _make_bridge()
    rid, content_hash = await _insert_raw_source(write_db_session)
    fact_a = await _insert_fact(write_db_session, content="A")
    fact_b = await _insert_fact(write_db_session, content="B")
    await _link_fact_to_source(write_db_session, fact_id=fact_a, content_hash=content_hash)
    await _link_fact_to_source(write_db_session, fact_id=fact_b, content_hash=content_hash)
    # Unrelated fact tied to a different source — must NOT come back.
    other_rid, other_hash = await _insert_raw_source(write_db_session, uri="https://other.example/y")
    fact_c = await _insert_fact(write_db_session, content="C")
    await _link_fact_to_source(write_db_session, fact_id=fact_c, content_hash=other_hash)

    source_row = await write_db_session.get(WriteRawSource, rid)
    facts = await bridge._load_linked_facts(write_db_session, source_row)
    assert {f.id for f in facts} == {fact_a, fact_b}


@pytest.mark.asyncio
async def test_load_linked_fact_sources_returns_provenance(write_db_session):
    bridge = _make_bridge()
    rid, content_hash = await _insert_raw_source(write_db_session)
    fact_a = await _insert_fact(write_db_session)
    await _link_fact_to_source(write_db_session, fact_id=fact_a, content_hash=content_hash)

    source_row = await write_db_session.get(WriteRawSource, rid)
    rows = await bridge._load_linked_fact_sources(write_db_session, source_row)
    assert len(rows) == 1
    assert rows[0].fact_id == fact_a
    assert rows[0].raw_source_content_hash == content_hash


# ---------------------------------------------------------------------------
# _load_linked_nodes — the highest-risk SQL in the PR
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_load_linked_nodes_array_overlap(write_db_session):
    """Concept/entity nodes whose ``fact_ids`` array overlaps the input."""
    bridge = _make_bridge()
    fact_a = await _insert_fact(write_db_session, content="A")
    fact_b = await _insert_fact(write_db_session, content="B")
    fact_c = await _insert_fact(write_db_session, content="C")

    # Concept linked via fact_a — should match.
    _, concept_uuid = await _insert_node(write_db_session, concept="alpha", node_type="concept", fact_ids=[fact_a])
    # Entity linked via fact_b — should match.
    _, entity_uuid = await _insert_node(write_db_session, concept="beta", node_type="entity", fact_ids=[fact_b])
    # Perspective linked via fact_a — type filter must exclude this.
    _, persp_uuid = await _insert_node(write_db_session, concept="gamma", node_type="perspective", fact_ids=[fact_a])
    # Concept linked only via fact_c — must NOT match the [a, b] query.
    _, isolated_uuid = await _insert_node(write_db_session, concept="delta", node_type="concept", fact_ids=[fact_c])

    rows = await bridge._load_linked_nodes(write_db_session, [fact_a, fact_b])
    matched = {n.node_uuid for n in rows}
    assert concept_uuid in matched
    assert entity_uuid in matched
    assert persp_uuid not in matched
    assert isolated_uuid not in matched


@pytest.mark.asyncio
async def test_load_linked_nodes_empty_input(write_db_session):
    bridge = _make_bridge()
    assert await bridge._load_linked_nodes(write_db_session, []) == []


# ---------------------------------------------------------------------------
# _upsert_raw_source — must return the LOCAL id on both branches
# ---------------------------------------------------------------------------


def _snapshot(rid, content_hash) -> CachedRawSourceSnapshot:
    return CachedRawSourceSnapshot(
        id=rid,
        uri="https://example.com/x",
        canonical_url="https://example.com/x",
        doi=None,
        title="T",
        raw_content="body",
        content_hash=content_hash,
        content_type="text/html",
        provider_id="httpx",
        is_full_text=True,
        is_super_source=False,
        fact_count=0,
        retrieved_at=None,
    )


@pytest.mark.asyncio
async def test_upsert_raw_source_returns_id_on_insert(write_db_session):
    bridge = _make_bridge()
    snapshot = _snapshot(uuid.uuid4(), "hash_insert")
    returned = await bridge._upsert_raw_source(write_db_session, snapshot)
    assert returned == snapshot.id


@pytest.mark.asyncio
async def test_upsert_raw_source_returns_local_id_on_conflict(write_db_session):
    bridge = _make_bridge()
    # Land a row directly under one id.
    local_id, content_hash = await _insert_raw_source(write_db_session, content_hash="hash_conflict")
    # Now try to upsert a snapshot with a *different* remote id but the
    # same content_hash. The bridge must report the local id, not the
    # remote one — otherwise PR5 would attach fact_sources to a stale id.
    remote_id = uuid.uuid4()
    assert remote_id != local_id
    snapshot = _snapshot(remote_id, content_hash)
    returned = await bridge._upsert_raw_source(write_db_session, snapshot)
    assert returned == local_id


# ---------------------------------------------------------------------------
# _match_or_create_node — ON CONFLICT no-op detection
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_match_or_create_node_reports_match_when_key_exists(write_db_session):
    """If a node with the same deterministic key already exists locally,
    the bridge must reuse its node_uuid and report ``created=False`` — even
    when Qdrant similarity search would have missed (no client here)."""
    bridge = _make_bridge()
    # Pre-create a local node with the same (concept, type) as the cached one.
    local_key, local_uuid = await _insert_node(write_db_session, concept="quark", node_type="concept", fact_ids=[])

    cached = CachedNodeSnapshot(
        key="ignored — bridge derives the key locally",
        node_uuid=uuid.uuid4(),  # remote uuid, must NOT leak through
        concept="quark",
        node_type="concept",
        definition="A fundamental particle.",
        embedding=None,  # forces the create branch
        fact_ids=[],
    )
    outcome = await bridge._match_or_create_node(
        write_db_session,
        cached,
        collection="ignored__nodes",
        local_fact_id_by_remote={},
    )
    assert outcome.created is False
    assert outcome.local_node_id == local_uuid


@pytest.mark.asyncio
async def test_match_or_create_node_creates_when_key_missing(write_db_session):
    bridge = _make_bridge()
    cached = CachedNodeSnapshot(
        key="ignored",
        node_uuid=uuid.uuid4(),  # remote uuid, must NOT be trusted
        concept="boson",
        node_type="concept",
        definition="A force carrier.",
        embedding=None,
        fact_ids=[],
    )
    outcome = await bridge._match_or_create_node(
        write_db_session,
        cached,
        collection="ignored__nodes",
        local_fact_id_by_remote={},
    )
    assert outcome.created is True
    expected_uuid = key_to_uuid(make_node_key("concept", "boson"))
    # Local uuid must be derived deterministically — NOT the remote uuid.
    assert outcome.local_node_id == expected_uuid
    assert outcome.local_node_id != cached.node_uuid


# ---------------------------------------------------------------------------
# _upsert_fact_source — deterministic id makes re-imports idempotent
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_upsert_fact_source_is_idempotent(write_db_session):
    bridge = _make_bridge()
    fid = await _insert_fact(write_db_session)
    fs = CachedFactSourceSnapshot(
        fact_id=uuid.uuid4(),  # ignored — bridge uses local_fact_id
        raw_source_uri="https://example.com/x",
        raw_source_title="T",
        raw_source_content_hash="hash_dedup",
        raw_source_provider_id="httpx",
        context_snippet=None,
        attribution=None,
        author_person=None,
        author_org=None,
    )
    # Insert twice with the same (local_fact_id, content_hash) — must
    # produce exactly one row, not two.
    await bridge._upsert_fact_source(write_db_session, fs, fid)
    await bridge._upsert_fact_source(write_db_session, fs, fid)

    from sqlalchemy import select

    rows = (
        (await write_db_session.execute(select(WriteFactSource).where(WriteFactSource.fact_id == fid))).scalars().all()
    )
    assert len(rows) == 1
