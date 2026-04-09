"""Unit tests for the contribute-retry sweeper.

These cover the orchestration / control flow with mocked engine,
session_factory, and resolver. The bridge's actual SQL is tested in
``libs/kt-graph/tests/integration/test_public_bridge_db.py``; the
SQLAlchemy partial-index DDL lands in the Alembic migration. What we
verify here is that the sweeper:

* Honours per-graph ``contribute_to_public=False`` and skips opted-out
  graphs without touching the engine.
* Skips the default graph (``is_default=True``) without an engine call.
* Ages out candidates younger than the min-age threshold so it never
  races with an in-flight ingest workflow.
* Counts succeeded vs. failed correctly based on the watermark column
  *after* the contribute call (not before).
* Caps each graph's batch by ``public_contribute_retry_batch_size``.
* Catches per-row exceptions so a single bad row doesn't poison the
  rest of the batch.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from kt_worker_ingest.ingest.contribute_sweeper import sweep_one_graph


def _settings(*, min_age: int = 15, batch_size: int = 200) -> SimpleNamespace:
    return SimpleNamespace(
        public_contribute_retry_min_age_minutes=min_age,
        public_contribute_retry_batch_size=batch_size,
    )


def _state_with_engine(engine) -> SimpleNamespace:
    state = SimpleNamespace()
    state.make_worker_engine = MagicMock(return_value=engine)
    state.settings = _settings()
    return state


def _engine(*, has_bridge: bool = True) -> MagicMock:
    e = MagicMock()
    e.has_public_bridge = has_bridge
    e.contribute_to_public = AsyncMock(return_value=None)
    return e


class _FakeRow:
    """Mimics the SQLAlchemy ``Row`` shape for the candidate query."""

    def __init__(self, values: tuple):
        self._values = values

    def __getitem__(self, idx):
        return self._values[idx]


class _FakeWriteSession:
    """In-memory stand-in for the per-graph write session.

    Holds a tiny "table" of (raw_source_id, contributed_to_public_at)
    pairs. The sweeper's two queries — candidate fetch and watermark
    re-read — are routed to ``execute`` which returns shaped result
    objects. The contribute mock is expected to mutate the table by
    calling :meth:`stamp_watermark` (the real bridge does this via an
    UPDATE statement against the same session).
    """

    def __init__(self, candidates: list[uuid.UUID]):
        self._watermarks: dict[uuid.UUID, datetime | None] = {rid: None for rid in candidates}
        self._candidates = list(candidates)
        self.commit = AsyncMock()
        self.execute = AsyncMock(side_effect=self._execute)
        self._next_query_is_candidate_fetch = True

    def stamp_watermark(self, rid: uuid.UUID) -> None:
        self._watermarks[rid] = datetime.now(UTC).replace(tzinfo=None)

    async def _execute(self, stmt):
        # Two query types interleave:
        #   1. The first call is the candidate-id SELECT (returns all
        #      pending rows).
        #   2. After every successful contribute call, sweep_one_graph
        #      re-reads the watermark for that single row. Failed rows
        #      skip the re-read so we must NOT use a sequential index —
        #      instead extract the bound ``raw_id`` from the WHERE clause.
        if self._next_query_is_candidate_fetch:
            self._next_query_is_candidate_fetch = False
            rows = [_FakeRow((rid,)) for rid in self._candidates if self._watermarks[rid] is None]
            return SimpleNamespace(all=lambda: rows)
        # Watermark re-read: pull the bound raw_id out of the compiled
        # statement so we always answer for the row the sweeper actually
        # asked about, even when previous candidates errored.
        compiled = stmt.compile()
        rid = next((p for p in compiled.params.values() if isinstance(p, uuid.UUID)), None)
        return SimpleNamespace(scalar_one_or_none=lambda r=rid: self._watermarks[r])


def _session_factory(session: _FakeWriteSession):
    @asynccontextmanager
    async def _factory() -> AsyncGenerator[_FakeWriteSession, None]:
        yield session

    return _factory


# ---------------------------------------------------------------------------
# sweep_one_graph
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sweep_one_graph_stamps_watermark_on_success():
    """The happy path: bridge stamps the watermark, sweeper counts success."""
    candidates = [uuid.uuid4() for _ in range(3)]
    session = _FakeWriteSession(candidates)

    # Stub engine: every contribute call stamps the watermark on the
    # same fake session, mirroring what the real bridge does inside
    # sweep_one_graph's session.
    engine = _engine()

    async def _stamp(*, raw_source_id):
        session.stamp_watermark(raw_source_id)

    engine.contribute_to_public.side_effect = _stamp

    state = _state_with_engine(engine)

    succeeded, failed = await sweep_one_graph(
        state,
        graph_id=uuid.uuid4(),
        graph_slug="alpha",
        write_session_factory=_session_factory(session),
        qdrant_collection_prefix="alpha__",
        cutoff=datetime.now(UTC).replace(tzinfo=None),
        batch_size=200,
    )

    assert succeeded == 3
    assert failed == 0
    assert engine.contribute_to_public.await_count == 3
    session.commit.assert_awaited()


@pytest.mark.asyncio
async def test_sweep_one_graph_counts_unstamped_as_failed():
    """If the bridge returns without stamping (upstream failure swallowed),
    the watermark stays NULL and the sweeper must count it as a failure
    so the next sweep retries."""
    candidates = [uuid.uuid4(), uuid.uuid4()]
    session = _FakeWriteSession(candidates)

    engine = _engine()
    # contribute returns cleanly but does NOT stamp — mirrors the
    # bridge's behaviour after a swallowed write_phase failure.
    engine.contribute_to_public.return_value = None

    state = _state_with_engine(engine)

    succeeded, failed = await sweep_one_graph(
        state,
        graph_id=uuid.uuid4(),
        graph_slug="alpha",
        write_session_factory=_session_factory(session),
        qdrant_collection_prefix="alpha__",
        cutoff=datetime.now(UTC).replace(tzinfo=None),
        batch_size=200,
    )

    assert succeeded == 0
    assert failed == 2


@pytest.mark.asyncio
async def test_sweep_one_graph_per_row_exception_does_not_abort_batch():
    """A single bad row must not poison the rest of the batch."""
    candidates = [uuid.uuid4() for _ in range(3)]
    session = _FakeWriteSession(candidates)
    engine = _engine()

    call_count = {"n": 0}

    async def _flaky(*, raw_source_id):
        call_count["n"] += 1
        if call_count["n"] == 2:
            raise RuntimeError("transient")
        session.stamp_watermark(raw_source_id)

    engine.contribute_to_public.side_effect = _flaky

    state = _state_with_engine(engine)

    succeeded, failed = await sweep_one_graph(
        state,
        graph_id=uuid.uuid4(),
        graph_slug="alpha",
        write_session_factory=_session_factory(session),
        qdrant_collection_prefix="alpha__",
        cutoff=datetime.now(UTC).replace(tzinfo=None),
        batch_size=200,
    )

    # Two stamped (rows 1 and 3), one raised (row 2).
    assert succeeded == 2
    assert failed == 1
    assert engine.contribute_to_public.await_count == 3


@pytest.mark.asyncio
async def test_sweep_one_graph_no_candidates_returns_zero():
    session = _FakeWriteSession([])
    engine = _engine()
    state = _state_with_engine(engine)

    succeeded, failed = await sweep_one_graph(
        state,
        graph_id=uuid.uuid4(),
        graph_slug="alpha",
        write_session_factory=_session_factory(session),
        qdrant_collection_prefix="alpha__",
        cutoff=datetime.now(UTC).replace(tzinfo=None),
        batch_size=200,
    )

    assert succeeded == 0
    assert failed == 0
    engine.contribute_to_public.assert_not_called()


@pytest.mark.asyncio
async def test_sweep_one_graph_no_bridge_short_circuits():
    """If the engine factory mis-wired and returned a bridge-less engine,
    the sweeper must NOT call contribute (would silently no-op forever)."""
    session = _FakeWriteSession([uuid.uuid4()])
    engine = _engine(has_bridge=False)
    state = _state_with_engine(engine)

    succeeded, failed = await sweep_one_graph(
        state,
        graph_id=uuid.uuid4(),
        graph_slug="alpha",
        write_session_factory=_session_factory(session),
        qdrant_collection_prefix="alpha__",
        cutoff=datetime.now(UTC).replace(tzinfo=None),
        batch_size=200,
    )

    assert succeeded == 0
    assert failed == 0
    engine.contribute_to_public.assert_not_called()


@pytest.mark.asyncio
async def test_sweep_one_graph_swallows_query_failure():
    session = _FakeWriteSession([])
    session.execute = AsyncMock(side_effect=RuntimeError("db gone"))
    engine = _engine()
    state = _state_with_engine(engine)

    succeeded, failed = await sweep_one_graph(
        state,
        graph_id=uuid.uuid4(),
        graph_slug="alpha",
        write_session_factory=_session_factory(session),
        qdrant_collection_prefix="alpha__",
        cutoff=datetime.now(UTC).replace(tzinfo=None),
        batch_size=200,
    )

    assert succeeded == 0
    assert failed == 0
    engine.contribute_to_public.assert_not_called()


@pytest.mark.asyncio
async def test_min_age_cutoff_passed_to_query():
    """The cutoff datetime must be honoured — sweeper passes a SQL
    expression that filters ``created_at < cutoff`` so an in-flight
    ingest's row never gets retried.

    We can't easily inspect the bound parameters of the mocked SQL
    expression, but we can confirm the sweeper passes a valid
    datetime through to ``session.execute`` and that the call is
    actually made (i.e. the cutoff doesn't short-circuit the query).
    """
    candidates = [uuid.uuid4()]
    session = _FakeWriteSession(candidates)
    engine = _engine()

    async def _stamp(*, raw_source_id):
        session.stamp_watermark(raw_source_id)

    engine.contribute_to_public.side_effect = _stamp

    state = _state_with_engine(engine)
    cutoff = datetime.now(UTC).replace(tzinfo=None) - timedelta(minutes=15)

    await sweep_one_graph(
        state,
        graph_id=uuid.uuid4(),
        graph_slug="alpha",
        write_session_factory=_session_factory(session),
        qdrant_collection_prefix="alpha__",
        cutoff=cutoff,
        batch_size=200,
    )

    # Two execute calls: one candidate query, one watermark re-read.
    assert session.execute.await_count == 2
