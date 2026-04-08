"""Workflow helpers that wire ``WorkerGraphEngine`` cache hooks into ingest.

These functions are deliberately small and side-effect-light so they can
be unit-tested with mocked engines and processed-source dataclasses
*without* spinning up a Hatchet worker, a real database, or Qdrant.

Both helpers degrade to no-ops when:

* the engine has no public bridge wired (the engine factory in
  ``kt_hatchet.lifespan`` already returns a bridge-less engine for the
  default graph itself), OR
* the per-graph toggle is off, OR
* the source isn't eligible (file upload / private fetcher / no
  canonical key).

The cache lookup result mutates a ``ChunkSelection`` dict so the
follow-on ``decompose_all_sources`` call skips cache-hit sources without
needing a new parameter on its API.
"""

from __future__ import annotations

import logging
import uuid
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from kt_graph.worker_engine import WorkerGraphEngine
    from kt_worker_ingest.ingest.pipeline import ChunkSelection, ProcessedSource

logger = logging.getLogger(__name__)


def _is_link_eligible(ps: ProcessedSource) -> bool:
    """Eligible = link source from a public fetcher with a stable identity key.

    File uploads carry ``is_public=None`` and never have a canonical_url,
    so they fail this check naturally — no special-case branch.
    """
    if ps.is_image:
        # Images may still be eligible for cache, but PR5 v1 only handles
        # text sources to keep the diff focused. Image cache hits would
        # also need vision-model embedding parity which is out of scope.
        return False
    if not ps.is_public:
        return False
    return bool(ps.canonical_url or ps.doi)


async def apply_public_cache_lookups(
    engine: WorkerGraphEngine,
    processed: list[ProcessedSource],
    *,
    use_public_cache: bool,
    chunk_selection: ChunkSelection,
    emit: Any | None = None,
) -> tuple[ChunkSelection, set[str], list[dict]]:
    """Look up each eligible source in the public graph and import on hit.

    Returns ``(updated_chunk_selection, cache_hit_source_ids, summaries)``.

    * ``updated_chunk_selection`` — for every cache-hit source, sets the
      entry to an empty set so ``decompose_all_sources`` skips it. The
      caller passes this back into ``decompose_all_sources``.
    * ``cache_hit_source_ids`` — set of ProcessedSource.source_id values
      that were served from cache. The caller can exclude these from
      contribute hooks (no point pushing back something we just imported).
    * ``summaries`` — per-hit dict suitable for streaming to the UI.

    Failures during lookup or import are logged and treated as misses —
    the source falls through to normal decomposition. The cache is an
    optimisation, never a load-bearing path.
    """
    if not use_public_cache or not engine.has_public_bridge:
        return chunk_selection, set(), []

    cache_hits: set[str] = set()
    summaries: list[dict] = []
    # Materialise into a mutable dict so we can stamp empty-set entries
    # for cache hits without disturbing user-supplied chunk selections.
    selection: dict[str, set[int]] = dict(chunk_selection) if chunk_selection is not None else {}

    for ps in processed:
        if not _is_link_eligible(ps):
            continue

        try:
            import_data = await engine.lookup_public_cache(canonical_url=ps.canonical_url, doi=ps.doi)
        except Exception:
            logger.warning("public cache lookup failed for %s", ps.name, exc_info=True)
            continue

        if import_data is None:
            continue

        try:
            result = await engine.import_from_public(import_data)
        except Exception:
            logger.warning("public cache import failed for %s", ps.name, exc_info=True)
            continue

        if result is None:
            # Engine has no bridge after all (race / mis-wiring) — treat as miss.
            continue

        cache_hits.add(ps.source_id)
        # Force decompose_all_sources to skip this source by stamping an
        # empty selected-indices set under its source_id. The decompose
        # loop already treats ``len(selected_indices) == 0`` as "skip".
        selection[ps.source_id] = set()

        summary = {
            "source_id": ps.source_id,
            "name": ps.name,
            "canonical_url": ps.canonical_url,
            "doi": ps.doi,
            "facts_imported": result.facts_imported,
            "facts_deduped": result.facts_deduped,
            "nodes_matched": result.nodes_matched,
            "nodes_created": result.nodes_created,
            "is_stale": import_data.is_stale,
        }
        summaries.append(summary)

        if emit is not None:
            try:
                await emit(
                    "pipeline_phase",
                    scope_id="ingest-public-cache",
                    phase="public_cache",
                    status="cached",
                    detail=(
                        f"Public cache hit for {ps.name}: "
                        f"{result.facts_imported} new facts, "
                        f"{result.facts_deduped} deduped, "
                        f"{result.nodes_matched + result.nodes_created} nodes"
                    ),
                    source_id=ps.source_id,
                    facts_imported=result.facts_imported,
                    facts_deduped=result.facts_deduped,
                    nodes_matched=result.nodes_matched,
                    nodes_created=result.nodes_created,
                )
            except Exception:
                logger.debug("Failed to emit pipeline_phase for cache hit", exc_info=True)

    # If the user passed an explicit chunk_selection, the dict copy
    # already preserves their entries (including empty sets that mean
    # "skip"). If chunk_selection was None and we have no cache hits, we
    # must return None to preserve "process every chunk" semantics for
    # the rest of decompose_all_sources.
    if chunk_selection is None and not cache_hits:
        return None, cache_hits, summaries
    return selection, cache_hits, summaries


async def contribute_processed_to_public(
    engine: WorkerGraphEngine,
    processed: list[ProcessedSource],
    *,
    contribute_to_public: bool,
    share_with_public_graph: bool,
    cache_hit_source_ids: set[str] | None = None,
) -> int:
    """Push freshly-decomposed eligible sources upstream to the public graph.

    Returns the number of sources for which a contribute call **succeeded**
    (failures are caught and logged at WARNING). Contribute is best-effort
    and must never abort the surrounding ingest pipeline.

    Sources that were just served from the public cache are skipped: there
    is nothing new to push back, and a contribute call would just churn
    against the upstream dedup pool.
    """
    if not contribute_to_public or not share_with_public_graph or not engine.has_public_bridge:
        return 0

    skip = cache_hit_source_ids or set()
    succeeded = 0

    for ps in processed:
        if ps.source_id in skip:
            continue
        if not _is_link_eligible(ps):
            continue
        if not ps.raw_source_id:
            continue

        try:
            raw_uuid = uuid.UUID(ps.raw_source_id)
        except (TypeError, ValueError):
            logger.debug("contribute: invalid raw_source_id %r on %s", ps.raw_source_id, ps.name)
            continue

        try:
            await engine.contribute_to_public(raw_source_id=raw_uuid)
        except Exception:
            logger.warning("contribute_to_public failed for %s", ps.name, exc_info=True)
            continue
        succeeded += 1

    return succeeded
