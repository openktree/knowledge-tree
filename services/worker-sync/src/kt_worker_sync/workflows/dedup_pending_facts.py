"""Post-job fact deduplication workflow.

``dedup_pending_facts_wf`` is dispatched as a child workflow by
``bottom_up_wf`` and ``ingest_build_wf`` between fact insertion and
node creation. It is NOT cron-driven — the parent workflow awaits its
result before proceeding to autograph.

Pipeline shape
--------------

1. **Recovery** — any ``write_facts`` row whose ``dedup_status`` is
   ``in_progress`` and older than the workflow timeout is reset to
   ``pending`` so a crashed previous run doesn't strand its snapshot.
2. **Snapshot** — atomically flip the requested ``fact_ids`` from
   ``pending`` to ``in_progress`` and return ``(id, content, fact_type)``
   for the claimed rows.
3. **Embed** — embed the snapshot in a single ``embed_batch`` call.
4. **Upsert into Qdrant** — insert all pending facts so they are
   discoverable by subsequent searches (both within-batch and cross-batch).
5. **Search-based dedup** — for each fact, query Qdrant for similar
   facts above threshold. Build edges from hits, union-find into
   components. Searches run in parallel batches (configurable via
   ``settings.dedup_search_batch_size``).
6. **Merge** — for each component, pick canonical (existing ready
   fact or smallest UUID), merge losers via ``merge_into_fast``.
7. **Cleanup** — delete losers from Qdrant, mark survivors as ``ready``.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import timedelta
from typing import cast

from hatchet_sdk import ConcurrencyExpression, ConcurrencyLimitStrategy, Context
from pydantic import BaseModel
from sqlalchemy import text

from kt_config.settings import get_settings
from kt_facts.processing.dedup import search_threshold_for_type, threshold_for_type
from kt_facts.processing.merge import merge_into_fast
from kt_hatchet.client import get_hatchet
from kt_hatchet.lifespan import WorkerState
from kt_worker_sync.workflows.dedup_partition import union_find_components

logger = logging.getLogger(__name__)

hatchet = get_hatchet()


# ── I/O models ────────────────────────────────────────────────────────


class DedupPendingFactsInput(BaseModel):
    """Input for the dedup workflow.

    One of ``graph_slug`` or ``graph_id`` selects the write-db / graph-db
    / Qdrant collection triple — ``graph_id`` wins when both are set.
    ``fact_ids`` is the explicit list of just-inserted facts to
    snapshot; restricting to this list keeps latency predictable
    regardless of any pre-existing pending backlog.
    """

    graph_slug: str = "default"
    graph_id: str | None = None
    fact_ids: list[uuid.UUID]


# ── Workflow declaration ──────────────────────────────────────────────


# Runs that target the same graph_slug serialize on a single concurrency
# key — this prevents two ``bottom_up_wf`` runs against the same graph
# from claiming overlapping snapshots. Different graphs are independent.
dedup_pending_facts_wf = hatchet.workflow(
    name="dedup_pending_facts_wf",
    input_validator=DedupPendingFactsInput,
    concurrency=ConcurrencyExpression(
        expression="input.graph_slug",
        max_runs=1,
        limit_strategy=ConcurrencyLimitStrategy.GROUP_ROUND_ROBIN,
    ),
)


# How long an ``in_progress`` row may sit before the recovery step
# reclaims it. Must be >= the workflow's worst-case execution time.
_IN_PROGRESS_RECOVERY_AGE = timedelta(minutes=30)


# ── Helpers ──────────────────────────────────────────────────────────


async def _resolve_sessions_and_collection(
    state: WorkerState,
    graph_slug: str,
    graph_id: str | None,
) -> tuple[object, object, str]:
    """Return ``(write_session_factory, graph_session_factory, qdrant_collection)``
    for the given graph.

    For the ``default`` graph we use the worker's system-level factories
    and the ``"facts"`` collection. For non-default graphs we resolve via
    the graph resolver (by id when provided, otherwise by slug) to get
    the per-graph factories and collection prefix.
    """
    if graph_id is not None:
        resolver = state.graph_resolver
        if resolver is None:
            raise RuntimeError(
                f"dedup_pending_facts_wf: graph_resolver unavailable — cannot dedup graph_id '{graph_id}'"
            )
        gs = await resolver.resolve(uuid.UUID(graph_id))
        collection = f"{gs.qdrant_collection_prefix}facts" if gs.qdrant_collection_prefix else "facts"
        return gs.write_session_factory, gs.graph_session_factory, collection

    if graph_slug == "default":
        return state.write_session_factory, state.session_factory, "facts"

    resolver = state.graph_resolver
    if resolver is None:
        raise RuntimeError(f"dedup_pending_facts_wf: graph_resolver unavailable — cannot dedup graph '{graph_slug}'")
    gs = await resolver.resolve_by_slug(graph_slug)
    collection = f"{gs.qdrant_collection_prefix}facts" if gs.qdrant_collection_prefix else "facts"
    return gs.write_session_factory, gs.graph_session_factory, collection


# ── Task implementation ─────────────────────────────────────────────


@dedup_pending_facts_wf.task(
    execution_timeout=_IN_PROGRESS_RECOVERY_AGE,
    schedule_timeout=timedelta(minutes=5),
)
async def dedup_pending_facts(
    input: DedupPendingFactsInput,
    ctx: Context,
) -> dict:
    state = cast(WorkerState, ctx.lifespan)
    graph_slug = input.graph_slug
    settings = get_settings()

    if not input.fact_ids:
        return {"claimed": 0, "merged": 0, "ready": 0}

    ctx.log(f"dedup({graph_slug}): starting with {len(input.fact_ids)} fact_ids")

    (
        write_session_factory,
        graph_session_factory,
        qdrant_collection,
    ) = await _resolve_sessions_and_collection(state, graph_slug, input.graph_id)

    # Qdrant is required for dedup — fail fast if unavailable.
    qdrant_client = state.qdrant_client
    if qdrant_client is None:
        raise RuntimeError("dedup_pending_facts_wf: Qdrant client unavailable")

    from kt_qdrant.repositories.facts import QdrantFactRepository

    qdrant_fact_repo = QdrantFactRepository(qdrant_client, collection_name=qdrant_collection)

    # ── 0. Recovery: reclaim abandoned in_progress rows ───────────────
    ctx.log(f"dedup({graph_slug}): recovering abandoned in_progress rows")
    async with write_session_factory() as recovery_session:  # type: ignore[misc]
        await recovery_session.execute(
            text(
                """
                UPDATE write_facts
                   SET dedup_status = 'pending'
                 WHERE dedup_status = 'in_progress'
                   AND updated_at < (now() AT TIME ZONE 'utc') - INTERVAL '30 minutes'
                """
            ),
        )
        await recovery_session.commit()

    # ── 1. Snapshot: claim the requested fact_ids ─────────────────────
    ctx.log(f"dedup({graph_slug}): claiming {len(input.fact_ids)} facts")
    async with write_session_factory() as snapshot_session:  # type: ignore[misc]
        claim_result = await snapshot_session.execute(
            text(
                """
                UPDATE write_facts
                   SET dedup_status = 'in_progress'
                 WHERE id = ANY(:fact_ids)
                   AND dedup_status = 'pending'
             RETURNING id, content, fact_type
                """
            ),
            {"fact_ids": [str(fid) for fid in input.fact_ids]},
        )
        claimed_rows = claim_result.all()
        await snapshot_session.commit()

    if not claimed_rows:
        ctx.log(f"dedup({graph_slug}): nothing to claim, done")
        return {"claimed": 0, "merged": 0, "ready": 0}

    ctx.log(f"dedup({graph_slug}): claimed {len(claimed_rows)} facts")

    snapshot: list[tuple[uuid.UUID, str, str]] = [
        (row[0] if isinstance(row[0], uuid.UUID) else uuid.UUID(str(row[0])), row[1], row[2]) for row in claimed_rows
    ]
    snapshot_ids_set: set[uuid.UUID] = {s[0] for s in snapshot}
    n = len(snapshot)

    # ── 2. Embed ──────────────────────────────────────────────────────
    ctx.log(f"dedup({graph_slug}): embedding {n} facts")
    embedding_service = state.embedding_service
    contents = [s[1] for s in snapshot]
    embeddings = await embedding_service.embed_batch(contents)
    ctx.log(f"dedup({graph_slug}): embedding complete")

    # ── 3. Upsert pending facts into Qdrant ──────────────────────────
    # All claimed facts go into the index so they can find each other
    # during the search phase, as well as be found by future dedup runs.
    upsert_tuples = [(snapshot[i][0], embeddings[i], snapshot[i][2], snapshot[i][1]) for i in range(n)]
    await qdrant_fact_repo.upsert_batch(upsert_tuples)
    ctx.log(f"dedup({graph_slug}): upserted {n} facts into Qdrant")

    # ── 4. Search-based duplicate discovery ──────────────────────────
    # For each fact, query Qdrant for similar facts (both within this
    # batch and from the existing ready population). Build edges for
    # union-find, and track matches against existing ready facts.
    id_to_idx: dict[uuid.UUID, int] = {snapshot[i][0]: i for i in range(n)}
    edges: list[tuple[int, int]] = []
    ready_matches: dict[int, uuid.UUID] = {}  # snapshot_idx -> ready_fact_id

    batch_size = settings.dedup_search_batch_size

    async def _search_one(i: int) -> list[tuple[int, int, uuid.UUID | None]]:
        """Search for duplicates of snapshot[i]. Returns (edge_pairs, ready_match)."""
        fact_id = snapshot[i][0]
        fact_type = snapshot[i][2]
        search_thr = search_threshold_for_type(fact_type)
        merge_thr = threshold_for_type(fact_type)

        try:
            hits = await qdrant_fact_repo.search_similar(
                embedding=embeddings[i],
                limit=20,
                score_threshold=search_thr,
                exclude_ids=[fact_id],
            )
        except Exception:
            logger.warning("dedup(%s): Qdrant search failed for fact %s", graph_slug, fact_id, exc_info=True)
            return []

        results: list[tuple[int, int, uuid.UUID | None]] = []
        best_ready_score = 0.0
        best_ready_id: uuid.UUID | None = None

        for hit in hits:
            if hit.score < merge_thr:
                continue
            if hit.fact_id in id_to_idx:
                # Intra-batch duplicate — add edge
                j = id_to_idx[hit.fact_id]
                pair_thr = max(merge_thr, threshold_for_type(snapshot[j][2]))
                if hit.score >= pair_thr:
                    results.append((i, j, None))
            elif hit.score > best_ready_score:
                # Match against existing ready fact — track best
                best_ready_score = hit.score
                best_ready_id = hit.fact_id

        if best_ready_id is not None:
            results.append((i, -1, best_ready_id))

        return results

    # Run searches in parallel batches
    for batch_start in range(0, n, batch_size):
        batch_end = min(batch_start + batch_size, n)
        batch_results = await asyncio.gather(*[_search_one(i) for i in range(batch_start, batch_end)])

        for result_list in batch_results:
            for i_idx, j_idx, ready_id in result_list:
                if ready_id is not None:
                    ready_matches[i_idx] = ready_id
                else:
                    edges.append((i_idx, j_idx))

    components = union_find_components(n, edges)
    ctx.log(f"dedup({graph_slug}): search done — {len(edges)} edges, {len(components)} components")

    # ── 5. Merge per component ───────────────────────────────────────
    surviving_canonicals: list[uuid.UUID] = []
    merged_count = 0
    loser_ids: list[uuid.UUID] = []

    for component in components:
        member_ids = [snapshot[i][0] for i in component]

        # Check if any member matched an existing ready fact
        canonical: uuid.UUID | None = None
        for idx in component:
            if idx in ready_matches:
                canonical = ready_matches[idx]
                break

        if canonical is None:
            # Deterministic tiebreaker: smallest UUID in the component.
            canonical = min(member_ids)

        # Merge all non-canonical members
        async with write_session_factory() as merge_session:  # type: ignore[misc]
            for member_id in member_ids:
                if member_id == canonical:
                    continue
                await merge_into_fast(merge_session, member_id, canonical)
                loser_ids.append(member_id)
                merged_count += 1
            await merge_session.commit()

        if canonical in snapshot_ids_set:
            surviving_canonicals.append(canonical)

    ctx.log(f"dedup({graph_slug}): merged {merged_count}, {len(surviving_canonicals)} surviving")

    # ── 6. Cleanup losers from Qdrant ────────────────────────────────
    if loser_ids:
        try:
            await qdrant_fact_repo.delete_batch(loser_ids)
            ctx.log(f"dedup({graph_slug}): deleted {len(loser_ids)} losers from Qdrant")
        except Exception:
            logger.warning(
                "dedup(%s): Qdrant delete_batch failed for %d losers",
                graph_slug,
                len(loser_ids),
                exc_info=True,
            )

    # ── 7. Finalize: flip survivors to 'ready' ──────────────────────
    if surviving_canonicals:
        async with write_session_factory() as finalize_session:  # type: ignore[misc]
            await finalize_session.execute(
                text(
                    """
                    UPDATE write_facts
                       SET dedup_status = 'ready'
                     WHERE id = ANY(:ids)
                       AND dedup_status = 'in_progress'
                    """
                ),
                {"ids": [str(fid) for fid in surviving_canonicals]},
            )
            await finalize_session.commit()

    logger.info(
        "dedup_pending_facts_wf(%s): claimed=%d merged=%d ready=%d components=%d",
        graph_slug,
        len(snapshot),
        merged_count,
        len(surviving_canonicals),
        len(components),
    )

    # Silence unused-variable warning for the graph session factory —
    # fast-mode dedup does not touch graph-db.
    del graph_session_factory

    return {
        "claimed": len(snapshot),
        "merged": merged_count,
        "ready": len(surviving_canonicals),
        "components": len(components),
    }
