"""Unit tests for the workflow public-cache helpers.

These cover the seven scenarios from the PR5 plan without spinning up a
Hatchet worker, real database, or Qdrant. The engine is mocked — the
focus here is the *eligibility* logic and *plumbing* to the engine
methods, not the bridge internals (those are covered by
``libs/kt-graph/tests/test_public_bridge*.py``).
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from kt_worker_ingest.ingest.pipeline import ProcessedSource
from kt_worker_ingest.ingest.public_cache import (
    apply_public_cache_lookups,
    contribute_processed_to_public,
)


def _link_source(
    *,
    source_id: str = "src1",
    name: str = "https://example.com/x",
    canonical_url: str | None = "https://example.com/x",
    doi: str | None = None,
    is_public: bool | None = True,
    raw_source_id: str | None = None,
) -> ProcessedSource:
    return ProcessedSource(
        source_id=source_id,
        name=name,
        raw_source_id=raw_source_id or str(uuid.uuid4()),
        canonical_url=canonical_url,
        doi=doi,
        is_public=is_public,
    )


def _file_upload_source(source_id: str = "file1") -> ProcessedSource:
    # File uploads come out of the upload pipeline with no canonical_url,
    # no DOI, and is_public=None — same shape used in production.
    return ProcessedSource(
        source_id=source_id,
        name="resume.pdf",
        raw_source_id=str(uuid.uuid4()),
        canonical_url=None,
        doi=None,
        is_public=None,
    )


def _import_data(*, is_stale: bool = False) -> SimpleNamespace:
    return SimpleNamespace(is_stale=is_stale)


def _import_result(**overrides) -> SimpleNamespace:
    base = {"facts_imported": 5, "facts_deduped": 2, "nodes_matched": 1, "nodes_created": 1}
    base.update(overrides)
    return SimpleNamespace(**base)


# ---------------------------------------------------------------------------
# apply_public_cache_lookups — eligibility short-circuits
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_lookup_no_op_when_toggle_off():
    """``use_public_cache=False`` short-circuits before touching the engine."""
    engine = MagicMock()
    engine.has_public_bridge = True
    engine.lookup_public_cache = AsyncMock(return_value=_import_data())

    selection, hits, summaries = await apply_public_cache_lookups(
        engine,
        [_link_source()],
        use_public_cache=False,
        chunk_selection=None,
    )

    engine.lookup_public_cache.assert_not_called()
    assert hits == set()
    assert summaries == []
    assert selection is None  # caller must keep "process all chunks" semantics


@pytest.mark.asyncio
async def test_lookup_no_op_when_no_bridge():
    """No bridge wired (default graph) — helper short-circuits."""
    engine = MagicMock()
    engine.has_public_bridge = False
    engine.lookup_public_cache = AsyncMock()

    selection, hits, _ = await apply_public_cache_lookups(
        engine,
        [_link_source()],
        use_public_cache=True,
        chunk_selection=None,
    )

    engine.lookup_public_cache.assert_not_called()
    assert hits == set()
    assert selection is None


@pytest.mark.asyncio
async def test_lookup_skips_file_uploads():
    """File uploads have ``is_public=None`` and no canonical_url — must be skipped.

    This is the file-upload-into-private-graph scenario from the PR5 plan:
    the default graph must remain untouched even when the toggle is on.
    """
    engine = MagicMock()
    engine.has_public_bridge = True
    engine.lookup_public_cache = AsyncMock()

    selection, hits, _ = await apply_public_cache_lookups(
        engine,
        [_file_upload_source()],
        use_public_cache=True,
        chunk_selection=None,
    )

    engine.lookup_public_cache.assert_not_called()
    assert hits == set()
    assert selection is None


@pytest.mark.asyncio
async def test_lookup_skips_private_fetcher():
    """``is_public=False`` (e.g. intranet provider) must NOT trigger a lookup."""
    engine = MagicMock()
    engine.has_public_bridge = True
    engine.lookup_public_cache = AsyncMock()

    private = _link_source(is_public=False)
    await apply_public_cache_lookups(engine, [private], use_public_cache=True, chunk_selection=None)

    engine.lookup_public_cache.assert_not_called()


@pytest.mark.asyncio
async def test_lookup_skips_when_no_canonical_key():
    """Public fetcher but no canonical_url AND no DOI — nothing to look up."""
    engine = MagicMock()
    engine.has_public_bridge = True
    engine.lookup_public_cache = AsyncMock()

    no_keys = _link_source(canonical_url=None, doi=None)
    await apply_public_cache_lookups(engine, [no_keys], use_public_cache=True, chunk_selection=None)

    engine.lookup_public_cache.assert_not_called()


# ---------------------------------------------------------------------------
# apply_public_cache_lookups — happy path + miss handling
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_lookup_hit_imports_and_marks_skip():
    engine = MagicMock()
    engine.has_public_bridge = True
    engine.lookup_public_cache = AsyncMock(return_value=_import_data(is_stale=True))
    engine.import_from_public = AsyncMock(return_value=_import_result())

    src = _link_source(source_id="A")
    selection, hits, summaries = await apply_public_cache_lookups(
        engine, [src], use_public_cache=True, chunk_selection=None
    )

    engine.lookup_public_cache.assert_awaited_once_with(canonical_url=src.canonical_url, doi=src.doi)
    engine.import_from_public.assert_awaited_once()
    assert hits == {"A"}
    # selection must now be a dict with the cache-hit source mapped to an
    # empty set — that's the universal "skip" signal decompose_all_sources
    # already understands.
    assert selection == {"A": set()}
    assert len(summaries) == 1
    assert summaries[0]["facts_imported"] == 5
    assert summaries[0]["is_stale"] is True


@pytest.mark.asyncio
async def test_lookup_miss_returns_none_chunk_selection_unchanged():
    engine = MagicMock()
    engine.has_public_bridge = True
    engine.lookup_public_cache = AsyncMock(return_value=None)
    engine.import_from_public = AsyncMock()

    selection, hits, _ = await apply_public_cache_lookups(
        engine, [_link_source()], use_public_cache=True, chunk_selection=None
    )

    engine.import_from_public.assert_not_called()
    assert hits == set()
    # No hits + caller passed None → must stay None to preserve
    # "process every chunk" semantics on the downstream decomposer.
    assert selection is None


@pytest.mark.asyncio
async def test_lookup_preserves_user_chunk_selection_on_miss():
    engine = MagicMock()
    engine.has_public_bridge = True
    engine.lookup_public_cache = AsyncMock(return_value=None)

    user_selection: dict[str, set[int]] = {"src1": {0, 2, 5}}
    selection, hits, _ = await apply_public_cache_lookups(
        engine,
        [_link_source(source_id="src1")],
        use_public_cache=True,
        chunk_selection=user_selection,
    )

    assert hits == set()
    # Helper must NOT mutate the caller's dict and must preserve their
    # selection 1:1 when there are no cache hits.
    assert selection == {"src1": {0, 2, 5}}
    assert user_selection == {"src1": {0, 2, 5}}


@pytest.mark.asyncio
async def test_lookup_overrides_user_chunk_selection_on_hit():
    """A cache hit must force the source to skip even if the user picked chunks for it."""
    engine = MagicMock()
    engine.has_public_bridge = True
    engine.lookup_public_cache = AsyncMock(return_value=_import_data())
    engine.import_from_public = AsyncMock(return_value=_import_result())

    user_selection = {"A": {0, 1}, "B": {3}}
    selection, hits, _ = await apply_public_cache_lookups(
        engine,
        [_link_source(source_id="A"), _link_source(source_id="B", canonical_url=None, doi=None)],
        use_public_cache=True,
        chunk_selection=user_selection,
    )

    assert hits == {"A"}
    # Source A: cache hit → empty set forces skip.
    # Source B: not eligible (no canonical/doi) → user's selection preserved.
    assert selection == {"A": set(), "B": {3}}


@pytest.mark.asyncio
async def test_lookup_swallows_engine_exception():
    """Lookup failures must be treated as a miss — never raise into the workflow."""
    engine = MagicMock()
    engine.has_public_bridge = True
    engine.lookup_public_cache = AsyncMock(side_effect=RuntimeError("default graph down"))
    engine.import_from_public = AsyncMock()

    selection, hits, _ = await apply_public_cache_lookups(
        engine, [_link_source()], use_public_cache=True, chunk_selection=None
    )

    engine.import_from_public.assert_not_called()
    assert hits == set()
    assert selection is None


@pytest.mark.asyncio
async def test_lookup_swallows_import_exception():
    """If lookup hits but import fails, helper must roll back to a miss."""
    engine = MagicMock()
    engine.has_public_bridge = True
    engine.lookup_public_cache = AsyncMock(return_value=_import_data())
    engine.import_from_public = AsyncMock(side_effect=RuntimeError("write conflict"))

    selection, hits, summaries = await apply_public_cache_lookups(
        engine, [_link_source()], use_public_cache=True, chunk_selection=None
    )

    assert hits == set()
    assert summaries == []
    assert selection is None


# ---------------------------------------------------------------------------
# contribute_processed_to_public
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_contribute_no_op_when_toggle_off():
    engine = MagicMock()
    engine.has_public_bridge = True
    engine.contribute_to_public = AsyncMock()

    n = await contribute_processed_to_public(
        engine,
        [_link_source()],
        contribute_to_public=False,
        share_with_public_graph=True,
    )

    assert n == 0
    engine.contribute_to_public.assert_not_called()


@pytest.mark.asyncio
async def test_contribute_no_op_when_per_ingest_opt_out():
    """``share_with_public_graph=False`` on a single ingest must suppress contribute.

    Per the PR5 plan, this still allows the cache lookup to run — but
    that's the workflow's job. The helper just enforces the toggle.
    """
    engine = MagicMock()
    engine.has_public_bridge = True
    engine.contribute_to_public = AsyncMock()

    n = await contribute_processed_to_public(
        engine,
        [_link_source()],
        contribute_to_public=True,
        share_with_public_graph=False,
    )

    assert n == 0
    engine.contribute_to_public.assert_not_called()


@pytest.mark.asyncio
async def test_contribute_no_op_when_no_bridge():
    engine = MagicMock()
    engine.has_public_bridge = False
    engine.contribute_to_public = AsyncMock()

    n = await contribute_processed_to_public(
        engine,
        [_link_source()],
        contribute_to_public=True,
        share_with_public_graph=True,
    )

    assert n == 0
    engine.contribute_to_public.assert_not_called()


@pytest.mark.asyncio
async def test_contribute_skips_file_uploads_and_private_fetchers():
    engine = MagicMock()
    engine.has_public_bridge = True
    engine.contribute_to_public = AsyncMock()

    sources = [
        _file_upload_source(source_id="file1"),
        _link_source(source_id="private", is_public=False),
        _link_source(source_id="public"),
    ]
    n = await contribute_processed_to_public(
        engine,
        sources,
        contribute_to_public=True,
        share_with_public_graph=True,
    )

    assert n == 1
    assert engine.contribute_to_public.await_count == 1


@pytest.mark.asyncio
async def test_contribute_skips_cache_hit_sources():
    """Sources we just imported FROM the cache shouldn't be pushed BACK to it."""
    engine = MagicMock()
    engine.has_public_bridge = True
    engine.contribute_to_public = AsyncMock()

    sources = [_link_source(source_id="A"), _link_source(source_id="B")]
    n = await contribute_processed_to_public(
        engine,
        sources,
        contribute_to_public=True,
        share_with_public_graph=True,
        cache_hit_source_ids={"A"},
    )

    assert n == 1
    args = engine.contribute_to_public.await_args
    # Only B (the non-cache-hit) should have been pushed.
    assert args.kwargs["raw_source_id"] == uuid.UUID(sources[1].raw_source_id)


@pytest.mark.asyncio
async def test_contribute_swallows_engine_exception():
    """A failing contribute must NOT abort the surrounding ingest."""
    engine = MagicMock()
    engine.has_public_bridge = True
    engine.contribute_to_public = AsyncMock(side_effect=RuntimeError("default graph down"))

    n = await contribute_processed_to_public(
        engine,
        [_link_source(source_id="A"), _link_source(source_id="B")],
        contribute_to_public=True,
        share_with_public_graph=True,
    )

    # attempted = 0 because the helper only counts successful awaits.
    # Both sources errored — neither counted, but the function must
    # still return cleanly without re-raising.
    assert n == 0
    assert engine.contribute_to_public.await_count == 2


@pytest.mark.asyncio
async def test_contribute_skips_invalid_raw_source_id():
    engine = MagicMock()
    engine.has_public_bridge = True
    engine.contribute_to_public = AsyncMock()

    bad = ProcessedSource(
        source_id="bad",
        name="https://example.com/y",
        raw_source_id="not-a-uuid",
        canonical_url="https://example.com/y",
        is_public=True,
    )
    n = await contribute_processed_to_public(
        engine,
        [bad],
        contribute_to_public=True,
        share_with_public_graph=True,
    )

    assert n == 0
    engine.contribute_to_public.assert_not_called()
