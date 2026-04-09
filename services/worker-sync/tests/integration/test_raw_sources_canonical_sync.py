"""Regression test for the canonical_url / doi sync gap.

PR7's review caught that ``_sync_raw_sources`` historically didn't
propagate ``canonical_url`` or ``doi`` from write-db to graph-db, even
though both columns exist on both schemas (added in PR3). The bridge
queries write-db so the gap was invisible to the public-cache flow,
but the API + analytics paths read from graph-db and would see NULLs
forever.

This test pins both the initial insert path AND the on-conflict update
path so a future column-list refactor can't regress them.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from kt_db.keys import uri_to_source_id
from kt_db.models import RawSource
from kt_db.write_models import WriteRawSource


@pytest.mark.asyncio(loop_scope="session")
async def test_sync_raw_source_propagates_canonical_url_and_doi(
    sync_engine, write_session_factory, graph_session_factory
) -> None:
    uri = "https://example.com/article?utm_source=test"
    canonical = "https://example.com/article"
    doi = "10.1234/example.2026"
    source_id = uri_to_source_id(uri)

    # ── Insert path ────────────────────────────────────────────────────
    async with write_session_factory() as ws:
        ws.add(
            WriteRawSource(
                id=source_id,
                uri=uri,
                title="Initial",
                raw_content="body",
                content_hash="a" * 64,
                provider_id="httpx",
                canonical_url=canonical,
                doi=doi,
            )
        )
        await ws.commit()

    # Drain whatever the per-session schema accumulated from prior tests
    # so we can assert the per-row sync count cleanly. ``_sync_raw_sources``
    # is batched, so loop until it returns 0.
    while await sync_engine._sync_raw_sources():
        pass

    async with graph_session_factory() as gs:
        row = (await gs.execute(select(RawSource).where(RawSource.id == source_id))).scalar_one()
        assert row.canonical_url == canonical
        assert row.doi == doi

    # ── On-conflict update path ────────────────────────────────────────
    # Mutating the source on write-db (e.g. a re-fetch picks up a new DOI
    # via meta tags) must propagate the new values, not stick with the
    # original insert.
    async with write_session_factory() as ws:
        target = (await ws.execute(select(WriteRawSource).where(WriteRawSource.id == source_id))).scalar_one()
        target.title = "Updated"
        target.canonical_url = "https://example.com/article-renamed"
        target.doi = "10.1234/example.2026-rev2"
        await ws.commit()

    while await sync_engine._sync_raw_sources():
        pass

    async with graph_session_factory() as gs:
        row = (await gs.execute(select(RawSource).where(RawSource.id == source_id))).scalar_one()
        assert row.canonical_url == "https://example.com/article-renamed"
        assert row.doi == "10.1234/example.2026-rev2"


@pytest.mark.asyncio(loop_scope="session")
async def test_sync_raw_source_handles_null_canonical_keys(
    sync_engine, write_session_factory, graph_session_factory
) -> None:
    """File uploads have ``canonical_url=None`` and ``doi=None`` — the
    sync must propagate the NULLs cleanly without falling back to the
    URI or any synthesised value."""
    uri = f"ingest://upload/{uuid.uuid4()}/file.pdf"
    source_id = uri_to_source_id(uri)

    async with write_session_factory() as ws:
        ws.add(
            WriteRawSource(
                id=source_id,
                uri=uri,
                title="file.pdf",
                raw_content="x",
                content_hash="b" * 64,
                provider_id="ingest_upload",
                canonical_url=None,
                doi=None,
            )
        )
        await ws.commit()

    await sync_engine._sync_raw_sources()

    async with graph_session_factory() as gs:
        row = (await gs.execute(select(RawSource).where(RawSource.id == source_id))).scalar_one()
        assert row.canonical_url is None
        assert row.doi is None
