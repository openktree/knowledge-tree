"""Unit tests for ``PublicGraphBridge`` and the WorkerGraphEngine pass-throughs.

These tests use ``unittest.mock`` to stub out the GraphSessionResolver,
write-db sessions, and Qdrant client. The bridge's read paths are
exercised against fake session contexts; the import path is checked for
the no-bridge no-op behavior on the engine side and for the
concept-similarity branch on the bridge side.

End-to-end integration with a real write-db schema lands in PR5 when the
bridge is wired into the ingest workflow — at that point the workflow
test can drive a full lookup → import → contribute round-trip.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from kt_graph.public_bridge import (
    CachedRawSourceSnapshot,
    CachedSourceImport,
    PublicGraphBridge,
)
from kt_graph.worker_engine import WorkerGraphEngine

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _settings(**overrides):
    base = {
        "public_bridge_concept_match_threshold": 0.93,
        "public_cache_refresh_after_days": 365,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def _bridge(
    *,
    qdrant_client=None,
    embedding_service=None,
    resolver=None,
    settings=None,
    default_graph_id=None,
) -> PublicGraphBridge:
    return PublicGraphBridge(
        resolver=resolver or MagicMock(),
        qdrant_client=qdrant_client,
        embedding_service=embedding_service,
        default_graph_id=default_graph_id or uuid.uuid4(),
        settings=settings or _settings(),
    )


def _raw_source_row(**overrides):
    defaults = {
        "id": uuid.uuid4(),
        "uri": "https://example.com/article",
        "canonical_url": "https://example.com/article",
        "doi": None,
        "title": "Example",
        "raw_content": "body",
        "content_hash": "deadbeef",
        "content_type": "text/html",
        "provider_id": "httpx",
        "is_full_text": True,
        "is_super_source": False,
        "fact_count": 3,
        # Two years old by default — older than the default refresh
        # window so the staleness path gets exercised in the happy-path
        # test below.
        "updated_at": datetime(2023, 1, 1),
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


# ---------------------------------------------------------------------------
# WorkerGraphEngine pass-through behavior
# ---------------------------------------------------------------------------


class TestEnginePassThroughs:
    """The engine's three bridge methods MUST no-op when bridge is None.

    Workflows call them unconditionally — that's the universal "skip"
    signal that keeps per-workflow code free of self-reference guards.
    """

    @pytest.mark.asyncio
    async def test_lookup_returns_none_when_no_bridge(self):
        engine = WorkerGraphEngine(write_session=AsyncMock())
        result = await engine.lookup_public_cache(canonical_url="x", doi=None)
        assert result is None
        assert engine.has_public_bridge is False

    @pytest.mark.asyncio
    async def test_import_returns_none_when_no_bridge(self):
        engine = WorkerGraphEngine(write_session=AsyncMock())
        snapshot = CachedSourceImport(
            raw_source=_raw_source_snapshot(),
            facts=[],
            fact_sources=[],
            nodes=[],
        )
        result = await engine.import_from_public(snapshot)
        assert result is None

    @pytest.mark.asyncio
    async def test_contribute_no_op_when_no_bridge(self):
        engine = WorkerGraphEngine(write_session=AsyncMock())
        # Must not raise.
        await engine.contribute_to_public(raw_source_id=uuid.uuid4())

    @pytest.mark.asyncio
    async def test_engine_delegates_to_bridge_when_present(self):
        bridge = MagicMock()
        bridge.lookup_cached_source = AsyncMock(return_value="LOOKUP_RESULT")
        bridge.import_cached_source = AsyncMock(return_value="IMPORT_RESULT")
        bridge.contribute_source_and_facts = AsyncMock(return_value=None)

        engine = WorkerGraphEngine(
            write_session=AsyncMock(),
            public_bridge=bridge,
            qdrant_collection_prefix="myslug__",
        )

        assert engine.has_public_bridge is True

        lookup = await engine.lookup_public_cache(canonical_url="u", doi="10.1/x")
        assert lookup == "LOOKUP_RESULT"
        bridge.lookup_cached_source.assert_awaited_once_with(canonical_url="u", doi="10.1/x")

        snapshot = CachedSourceImport(
            raw_source=_raw_source_snapshot(),
            facts=[],
            fact_sources=[],
            nodes=[],
        )
        imp = await engine.import_from_public(snapshot)
        assert imp == "IMPORT_RESULT"
        # Engine must inject its own session + prefix.
        bridge.import_cached_source.assert_awaited_once()
        kwargs = bridge.import_cached_source.await_args.kwargs
        assert kwargs["target_qdrant_prefix"] == "myslug__"
        assert kwargs["target_write_session"] is engine._write_session

        rid = uuid.uuid4()
        await engine.contribute_to_public(raw_source_id=rid)
        bridge.contribute_source_and_facts.assert_awaited_once()
        ckwargs = bridge.contribute_source_and_facts.await_args.kwargs
        assert ckwargs["raw_source_id"] == rid
        assert ckwargs["source_qdrant_prefix"] == "myslug__"


def _raw_source_snapshot(**overrides) -> CachedRawSourceSnapshot:
    base = {
        "id": uuid.uuid4(),
        "uri": "https://example.com/x",
        "canonical_url": "https://example.com/x",
        "doi": None,
        "title": "T",
        "raw_content": "body",
        "content_hash": "h" * 64,
        "content_type": "text/html",
        "provider_id": "httpx",
        "is_full_text": True,
        "is_super_source": False,
        "fact_count": 0,
        "retrieved_at": datetime(2026, 1, 1),
    }
    base.update(overrides)
    return CachedRawSourceSnapshot(**base)


# ---------------------------------------------------------------------------
# Bridge: lookup short-circuits
# ---------------------------------------------------------------------------


class TestLookupShortCircuits:
    @pytest.mark.asyncio
    async def test_lookup_returns_none_when_keys_missing(self):
        bridge = _bridge(qdrant_client=MagicMock())
        result = await bridge.lookup_cached_source(canonical_url=None, doi=None)
        assert result is None

    @pytest.mark.asyncio
    async def test_lookup_returns_none_without_qdrant(self):
        # Without a Qdrant client we can't carry embeddings forward, so
        # the bridge refuses to serve a hit at all (otherwise the import
        # phase would silently lose dedup precision).
        bridge = _bridge(qdrant_client=None)
        result = await bridge.lookup_cached_source(canonical_url="u", doi=None)
        assert result is None

    @pytest.mark.asyncio
    async def test_lookup_returns_none_when_resolver_fails(self):
        resolver = MagicMock()
        resolver.resolve = AsyncMock(side_effect=RuntimeError("default graph down"))
        bridge = _bridge(qdrant_client=MagicMock(), resolver=resolver)

        result = await bridge.lookup_cached_source(canonical_url="u", doi=None)
        assert result is None  # swallowed, never raised


# ---------------------------------------------------------------------------
# Bridge: lookup happy path with mocked sessions
# ---------------------------------------------------------------------------


class _FakeSessionContext:
    """Async context-manager wrapper around a single mock session."""

    def __init__(self, session):
        self._session = session

    async def __aenter__(self):
        return self._session

    async def __aexit__(self, exc_type, exc, tb):
        return False


def _scalar_result(rows):
    """Build a SQLAlchemy-result-shaped mock that returns ``rows`` from ``.scalars().all()``."""
    res = MagicMock()
    res.scalar_one_or_none = MagicMock(return_value=rows[0] if rows else None)
    scalars = MagicMock()
    scalars.all = MagicMock(return_value=rows)
    res.scalars = MagicMock(return_value=scalars)
    return res


class TestLookupHappyPath:
    @pytest.mark.asyncio
    async def test_lookup_returns_snapshot(self):
        # ── arrange ────────────────────────────────────────────────────
        raw_row = _raw_source_row()
        # Build fact / fact_source / node rows that the queries will return.
        fact_id = uuid.uuid4()
        fact_row = SimpleNamespace(
            id=fact_id,
            content="The sky is blue.",
            fact_type="atomic",
            metadata_=None,
        )
        fact_source_row = SimpleNamespace(
            fact_id=fact_id,
            raw_source_uri=raw_row.uri,
            raw_source_title=raw_row.title,
            raw_source_content_hash=raw_row.content_hash,
            raw_source_provider_id=raw_row.provider_id,
            context_snippet="snippet",
            attribution=None,
            author_person=None,
            author_org=None,
        )
        node_uuid = uuid.uuid4()
        node_row = SimpleNamespace(
            key="concept|sky",
            node_uuid=node_uuid,
            concept="sky",
            node_type="concept",
            definition="The space above the earth.",
            fact_ids=[str(fact_id)],
        )

        # Each ``session.execute`` call returns the next staged result.
        # Order: raw source → linked facts → linked fact_sources → linked nodes.
        results = [
            _scalar_result([raw_row]),
            _scalar_result([fact_row]),
            _scalar_result([fact_source_row]),
            _scalar_result([node_row]),
        ]
        session = MagicMock()
        session.execute = AsyncMock(side_effect=results)

        write_sf = MagicMock(return_value=_FakeSessionContext(session))
        gs = SimpleNamespace(
            write_session_factory=write_sf,
            qdrant_collection_prefix="",  # default graph
        )
        resolver = MagicMock()
        resolver.resolve = AsyncMock(return_value=gs)

        qdrant = MagicMock()
        # Pretend Qdrant returns embeddings for both ids.
        qdrant.retrieve = AsyncMock(
            side_effect=[
                [SimpleNamespace(id=str(fact_id), vector=[0.1, 0.2, 0.3])],
                [SimpleNamespace(id=str(node_uuid), vector=[0.4, 0.5, 0.6])],
            ]
        )

        bridge = _bridge(qdrant_client=qdrant, resolver=resolver)

        # ── act ────────────────────────────────────────────────────────
        result = await bridge.lookup_cached_source(canonical_url=raw_row.canonical_url, doi=None)

        # ── assert ─────────────────────────────────────────────────────
        assert result is not None
        assert result.raw_source.canonical_url == raw_row.canonical_url
        assert len(result.facts) == 1
        assert result.facts[0].embedding == [0.1, 0.2, 0.3]
        assert len(result.fact_sources) == 1
        assert len(result.nodes) == 1
        assert result.nodes[0].embedding == [0.4, 0.5, 0.6]
        assert result.nodes[0].fact_ids == [fact_id]
        assert result.is_stale is True  # fixture's updated_at is 2023-01-01, > 365 days old

    @pytest.mark.asyncio
    async def test_lookup_returns_none_when_no_match(self):
        session = MagicMock()
        session.execute = AsyncMock(return_value=_scalar_result([]))
        write_sf = MagicMock(return_value=_FakeSessionContext(session))
        gs = SimpleNamespace(write_session_factory=write_sf, qdrant_collection_prefix="")
        resolver = MagicMock()
        resolver.resolve = AsyncMock(return_value=gs)

        bridge = _bridge(qdrant_client=MagicMock(), resolver=resolver)
        result = await bridge.lookup_cached_source(canonical_url="u", doi=None)
        assert result is None


# ---------------------------------------------------------------------------
# Bridge: staleness threshold
# ---------------------------------------------------------------------------


class TestStaleness:
    def test_zero_disables_staleness(self):
        bridge = _bridge(settings=_settings(public_cache_refresh_after_days=0))
        # An ancient timestamp must NOT be considered stale when refresh
        # is disabled — that's how operators turn the feature off.
        assert bridge._is_stale(datetime(1970, 1, 1)) is False

    def test_recent_is_fresh(self):
        bridge = _bridge(settings=_settings(public_cache_refresh_after_days=365))
        from datetime import UTC

        now_naive = datetime.now(UTC).replace(tzinfo=None)
        assert bridge._is_stale(now_naive) is False

    def test_ancient_is_stale(self):
        bridge = _bridge(settings=_settings(public_cache_refresh_after_days=1))
        assert bridge._is_stale(datetime(2020, 1, 1)) is True

    def test_none_retrieved_at_is_fresh(self):
        bridge = _bridge()
        # Defensive: a missing timestamp must not flag as stale, otherwise
        # we'd queue refreshes for every freshly-imported row.
        assert bridge._is_stale(None) is False
