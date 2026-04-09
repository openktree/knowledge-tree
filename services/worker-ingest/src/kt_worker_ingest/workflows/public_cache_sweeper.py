"""Cron-driven retry sweeper for the multigraph public-cache contribute hook.

The bridge's normal contribute path runs at the end of every ingest
workflow. When that path fails — default graph unreachable, mid-flight
crash, the upstream Qdrant collection wasn't ready, opted-in after the
fact — the source row's ``contributed_to_public_at`` watermark stays
``NULL``. This workflow finds those stragglers and retries.

Design constraints:

* **Best-effort.** Failures here log + count. They never raise.
* **Bounded per sweep.** ``Settings.public_contribute_retry_batch_size``
  caps the number of candidates touched per graph per run. The cron
  schedule (``*/15 * * * *``) re-queues the rest on the next tick.
* **Min age guard.** Only rows older than
  ``Settings.public_contribute_retry_min_age_minutes`` are eligible —
  prevents racing with the in-flight ingest workflow that wrote the row.
* **Per-graph bridge wiring.** Reuses ``WorkerState.make_worker_engine``
  so the bridge gets the right session + Qdrant prefix without
  duplicating any plumbing.
* **Default graph never participates.** The factory already returns a
  bridge-less engine for the default graph; on top of that we explicitly
  skip ``is_default`` graphs in the dispatch loop because the default
  graph has no upstream to contribute *to*.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import cast

from hatchet_sdk import ConcurrencyExpression, ConcurrencyLimitStrategy, Context

from kt_hatchet.client import get_hatchet
from kt_hatchet.lifespan import WorkerState
from kt_worker_ingest.ingest.contribute_sweeper import sweep_one_graph

logger = logging.getLogger(__name__)

hatchet = get_hatchet()


public_cache_sweep_wf = hatchet.workflow(
    name="public_cache_sweep_wf",
    on_crons=["*/15 * * * *"],
    concurrency=ConcurrencyExpression(
        # Single global runner — there's no benefit to parallel sweeps
        # against the same graphs, and the per-graph bridge already
        # serialises against the upstream default graph's write-db.
        expression="'public_cache_sweep'",
        max_runs=1,
        limit_strategy=ConcurrencyLimitStrategy.GROUP_ROUND_ROBIN,
    ),
)


@public_cache_sweep_wf.task(
    execution_timeout=timedelta(minutes=10),
    schedule_timeout=timedelta(minutes=2),
)
async def sweep_public_contribute(input: dict, ctx: Context) -> dict:
    """Iterate active non-default graphs and retry stuck contribute rows."""
    state = cast(WorkerState, ctx.lifespan)

    if state.graph_resolver is None or state.default_graph_id is None:
        logger.debug("public_cache_sweep: resolver/default_graph_id missing — skipping")
        return {"graphs_swept": 0, "retried": 0, "succeeded": 0, "failed": 0}

    settings = state.settings
    min_age = timedelta(minutes=settings.public_contribute_retry_min_age_minutes)
    cutoff = datetime.now(UTC).replace(tzinfo=None) - min_age
    batch_size = settings.public_contribute_retry_batch_size

    try:
        graphs = await state.graph_resolver.list_active_graphs()
    except Exception:
        logger.warning("public_cache_sweep: list_active_graphs failed", exc_info=True)
        return {"graphs_swept": 0, "retried": 0, "succeeded": 0, "failed": 0}

    totals = {"graphs_swept": 0, "retried": 0, "succeeded": 0, "failed": 0}

    for graph in graphs:
        # Skip the default graph itself — there's no upstream to push to.
        # The bridge factory would build a bridge-less engine here anyway,
        # so contribute calls would be silent no-ops, but doing the query
        # at all wastes a roundtrip.
        if graph.is_default:
            continue
        if not graph.contribute_to_public:
            # Graph has explicitly opted out of contributing — respect it.
            # If they flip it back on later, those rows become eligible
            # again automatically (the column is still NULL).
            continue

        try:
            gs = await state.graph_resolver.resolve(graph.id)
        except Exception:
            logger.warning(
                "public_cache_sweep: cannot resolve graph %s — skipping",
                graph.slug,
                exc_info=True,
            )
            continue

        succeeded, failed = await sweep_one_graph(
            state,
            graph_id=graph.id,
            graph_slug=graph.slug,
            write_session_factory=gs.write_session_factory,
            qdrant_collection_prefix=gs.qdrant_collection_prefix,
            cutoff=cutoff,
            batch_size=batch_size,
        )
        totals["graphs_swept"] += 1
        totals["retried"] += succeeded + failed
        totals["succeeded"] += succeeded
        totals["failed"] += failed

    if totals["retried"]:
        logger.info(
            "public_cache.sweep_done graphs=%d retried=%d ok=%d fail=%d",
            totals["graphs_swept"],
            totals["retried"],
            totals["succeeded"],
            totals["failed"],
        )
    else:
        logger.debug("public_cache.sweep_done no candidates")

    return totals
