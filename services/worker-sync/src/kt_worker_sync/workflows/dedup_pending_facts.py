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
2. **Snapshot (T1)** — atomically flip the requested ``fact_ids`` from
   ``pending`` to ``in_progress`` and return ``(id, content, fact_type)``
   for the claimed rows. Already-claimed or already-``ready`` rows are
   skipped.
3. **Embed + partition (T2)** — embed the snapshot in a single
   ``embed_batch`` call, then brute-force pairwise cosine similarity at
   the per-type threshold (max of both facts' thresholds) and union-find
   the result into connected components.
4. **Dedup partition (T3)** — one task per component:

   * Query Qdrant ``find_most_similar`` against the global ``ready``
     population using the representative embedding.
   * If a global hit exists, canonical = hit.fact_id.
   * Otherwise canonical = the snapshot fact with the smallest UUID.
   * Call :func:`merge_into_fast` for every non-canonical member.
   * If canonical is itself a snapshot fact, upsert its embedding into
     Qdrant so future runs dedup against it.

5. **Finalize (T4)** — mark the surviving canonical rows as ``ready``.
"""

from __future__ import annotations

import logging
import uuid
from datetime import timedelta
from typing import cast

from hatchet_sdk import ConcurrencyExpression, ConcurrencyLimitStrategy, Context
from pydantic import BaseModel
from sqlalchemy import text

from kt_facts.processing.dedup import threshold_for_type
from kt_facts.processing.merge import merge_into_fast
from kt_hatchet.client import get_hatchet
from kt_hatchet.lifespan import WorkerState
from kt_worker_sync.workflows.dedup_partition import cosine, union_find_components

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
#
# Pure helpers (``cosine``, ``union_find_components``) live in the
# sibling ``dedup_partition`` module so they can be unit-tested
# without importing the Hatchet client.


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


# ── Single task implementation ───────────────────────────────────────
#
# The whole pipeline (recovery → snapshot → partition → merge → finalize)
# runs in one Hatchet task. We pay one extra task's worth of latency by
# not fanning out across components here, but in exchange we get a much
# simpler I/O story: no state needs to round-trip through Hatchet as
# per-task return payloads, and the snapshot / merge / finalize all
# share the same write-db session — which matters because the ``ready``
# transition is the commit point that makes the work visible to the
# sync worker and to autograph.
#
# Fan-out across components is a latency optimisation that can be added
# later if snapshots grow to the point where per-component Qdrant
# searches dominate wall time. At current scale (a few hundred facts per
# snapshot) the sequential path is already sub-second.


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

    if not input.fact_ids:
        return {"claimed": 0, "merged": 0, "ready": 0}

    (
        write_session_factory,
        graph_session_factory,
        qdrant_collection,
    ) = await _resolve_sessions_and_collection(state, graph_slug, input.graph_id)

    # ── 0. Recovery: reclaim abandoned in_progress rows ───────────────
    async with write_session_factory() as recovery_session:  # type: ignore[misc]
        await recovery_session.execute(
            text(
                """
                UPDATE write_facts
                   SET dedup_status = 'pending'
                 WHERE dedup_status = 'in_progress'
                   AND updated_at < (now() AT TIME ZONE 'utc') - :age
                """
            ),
            {"age": _IN_PROGRESS_RECOVERY_AGE},
        )
        await recovery_session.commit()

    # ── 1. Snapshot: claim the requested fact_ids ─────────────────────
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
        logger.debug(
            "dedup_pending_facts_wf(%s): nothing to claim for %d fact_ids",
            graph_slug,
            len(input.fact_ids),
        )
        return {"claimed": 0, "merged": 0, "ready": 0}

    snapshot: list[tuple[uuid.UUID, str, str]] = [
        (row[0] if isinstance(row[0], uuid.UUID) else uuid.UUID(str(row[0])), row[1], row[2]) for row in claimed_rows
    ]
    snapshot_ids_set: set[uuid.UUID] = {s[0] for s in snapshot}

    # ── 2. Embed + partition ──────────────────────────────────────────
    embedding_service = state.embedding_service
    contents = [s[1] for s in snapshot]
    embeddings = await embedding_service.embed_batch(contents)

    n = len(snapshot)
    edges: list[tuple[int, int]] = []
    # TODO: O(n²) brute-force pairwise cosine. Fine at current snapshot
    # sizes (a few hundred facts). If snapshots grow past ~2k, switch to
    # batched numpy dot-product or an ANN index over the snapshot.
    for i in range(n):
        for j in range(i + 1, n):
            thr = max(
                threshold_for_type(snapshot[i][2]),
                threshold_for_type(snapshot[j][2]),
            )
            if cosine(embeddings[i], embeddings[j]) >= thr:
                edges.append((i, j))
    components = union_find_components(n, edges)

    # ── 3. Dedup per component ────────────────────────────────────────
    qdrant_client = state.qdrant_client
    qdrant_fact_repo = None
    if qdrant_client is not None:
        from kt_qdrant.repositories.facts import QdrantFactRepository

        qdrant_fact_repo = QdrantFactRepository(qdrant_client, collection_name=qdrant_collection)

    surviving_canonicals: list[uuid.UUID] = []
    merged_count = 0

    # We open one write-db transaction per component to keep the merge
    # atomic yet bounded. Graph-db is not touched in fast mode.
    for component in components:
        member_ids = [snapshot[i][0] for i in component]
        rep_idx = component[0]
        rep_embedding = embeddings[rep_idx]
        rep_fact_type = snapshot[rep_idx][2]

        # 3a. Global Qdrant lookup (ready population)
        canonical: uuid.UUID | None = None
        if qdrant_fact_repo is not None:
            try:
                hit = await qdrant_fact_repo.find_most_similar(
                    rep_embedding,
                    score_threshold=threshold_for_type(rep_fact_type),
                )
                if hit is not None and hit.fact_id not in snapshot_ids_set:
                    canonical = hit.fact_id
            except Exception:
                logger.warning(
                    "dedup_pending_facts_wf(%s): Qdrant find_most_similar failed for component of size %d",
                    graph_slug,
                    len(component),
                    exc_info=True,
                )

        if canonical is None:
            # Deterministic tiebreaker: smallest UUID in the component.
            canonical = min(member_ids)

        # 3b. Merge all non-canonical members into canonical.
        async with write_session_factory() as merge_session:  # type: ignore[misc]
            for member_id in member_ids:
                if member_id == canonical:
                    continue
                await merge_into_fast(merge_session, member_id, canonical)
                merged_count += 1
            await merge_session.commit()

        # 3c. If canonical is itself a snapshot fact (no global hit),
        # upsert its embedding into Qdrant so the ``ready`` population
        # grows.
        if canonical in snapshot_ids_set and qdrant_fact_repo is not None:
            canonical_idx = next(
                (component[k] for k, mid in enumerate(member_ids) if mid == canonical),
                rep_idx,
            )
            try:
                await qdrant_fact_repo.upsert(
                    fact_id=canonical,
                    embedding=embeddings[canonical_idx],
                    fact_type=snapshot[canonical_idx][2],
                    content=snapshot[canonical_idx][1],
                )
            except Exception:
                logger.warning(
                    "dedup_pending_facts_wf(%s): Qdrant upsert failed for canonical %s",
                    graph_slug,
                    canonical,
                    exc_info=True,
                )

        if canonical in snapshot_ids_set:
            surviving_canonicals.append(canonical)

    # ── 4. Finalize: flip survivors to 'ready' ────────────────────────
    # Losers were deleted by merge_into_fast. Surviving canonicals that
    # are themselves snapshot rows get marked ready; canonicals that
    # came from a global Qdrant hit are already ready (we didn't touch
    # them). If a canonical came from Qdrant its component's snapshot
    # rows were merged *into* it, so nothing in this snapshot survives
    # under that canonical.
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
    # fast-mode dedup does not touch graph-db. Heavy-mode repair does.
    del graph_session_factory

    return {
        "claimed": len(snapshot),
        "merged": merged_count,
        "ready": len(surviving_canonicals),
        "components": len(components),
    }
