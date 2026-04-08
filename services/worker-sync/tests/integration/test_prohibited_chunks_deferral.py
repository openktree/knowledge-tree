"""Regression test for the _sync_prohibited_chunks deferral branch.

When a WriteProhibitedChunk references a content_hash whose write_raw_sources
row exists but whose corresponding graph-db raw_sources row hasn't been
synced yet, _sync_prohibited_chunks must defer (pin first_failure_ts) so the
watermark does not advance past the chunk. The next tick, after the
raw_source row lands in graph-db, the chunk should sync cleanly.

This pins the deferral logic added in PR #177 — without it, the chunk would
either (a) get skipped silently, leaving the source's prohibited_chunks
incomplete, or (b) wedge the sync on a FK violation.
"""

from __future__ import annotations

import uuid
from datetime import datetime

import pytest

from kt_db.keys import uri_to_source_id
from kt_db.models import RawSource
from kt_db.write_models import WriteProhibitedChunk, WriteRawSource


@pytest.mark.asyncio(loop_scope="session")
async def test_prohibited_chunks_deferred_when_graph_db_lags(
    sync_engine, write_session_factory, graph_session_factory
) -> None:
    uri = "https://example.com/deferred-chunk-test"
    source_id = uri_to_source_id(uri)
    content_hash = "f" * 64
    chunk_ts = datetime(2026, 4, 8, 12, 0, 0)

    # Seed write-db with both the source AND a prohibited chunk that
    # references its hash. Critically, do NOT seed graph-db with the
    # corresponding RawSource — we want the deferral branch to fire.
    async with write_session_factory() as ws:
        ws.add(
            WriteRawSource(
                id=source_id,
                uri=uri,
                title="Deferred chunk source",
                raw_content="x",
                content_hash=content_hash,
                provider_id="test",
            )
        )
        ws.add(
            WriteProhibitedChunk(
                id=uuid.uuid4(),
                source_content_hash=content_hash,
                chunk_text="rejected text",
                model_id="test-model",
                error_message="safety filter",
                updated_at=chunk_ts,
            )
        )
        await ws.commit()

    synced = await sync_engine._sync_prohibited_chunks()

    async with write_session_factory() as ws:
        watermark_after_first = await sync_engine._get_watermark(ws, "write_prohibited_chunks")

    # Nothing was written, and the watermark is strictly less than the
    # deferred chunk's updated_at — so the next tick's `> watermark` filter
    # will re-fetch it. (Specifically, the pin is behind the chunk by at
    # least one microsecond.)
    assert synced == 0
    assert watermark_after_first < chunk_ts

    async with graph_session_factory() as gs:
        from sqlalchemy import select

        from kt_db.models import ProhibitedChunk

        result = await gs.execute(select(ProhibitedChunk))
        assert result.scalar_one_or_none() is None

    # Now seed the graph-db RawSource (simulating raw_sources sync catching
    # up) and re-run prohibited_chunks sync. The chunk should now land.
    async with graph_session_factory() as gs:
        gs.add(
            RawSource(
                id=source_id,
                uri=uri,
                title="Deferred chunk source",
                content_hash=content_hash,
                provider_id="test",
            )
        )
        await gs.commit()

    synced_after_catchup = await sync_engine._sync_prohibited_chunks()

    assert synced_after_catchup == 1

    async with graph_session_factory() as gs:
        from sqlalchemy import select

        from kt_db.models import ProhibitedChunk

        result = await gs.execute(select(ProhibitedChunk))
        chunks = result.scalars().all()
        assert len(chunks) == 1
        assert chunks[0].chunk_text == "rejected text"
        assert chunks[0].raw_source_id == source_id
