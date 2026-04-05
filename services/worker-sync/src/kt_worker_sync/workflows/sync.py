"""Hatchet workflows for periodic write-db → graph-db synchronization.

Two workflows:
- ``sync_dispatch_wf`` — cron-triggered dispatcher that fans out one
  ``sync_graph_wf`` per active graph every minute.
- ``sync_graph_wf`` — syncs a single graph, with per-graph concurrency
  (max 1 sync per graph at a time) so high-activity bursts on one graph
  don't starve others.
"""

from __future__ import annotations

import json
import logging
import time
from datetime import timedelta
from typing import cast

from hatchet_sdk import ConcurrencyExpression, ConcurrencyLimitStrategy, Context

from kt_config.settings import get_settings
from kt_hatchet.client import dispatch_workflow, get_hatchet
from kt_hatchet.lifespan import WorkerState

logger = logging.getLogger(__name__)

hatchet = get_hatchet()
_settings = get_settings()

# Read at import time — same pattern as the old sync_wf
sync_task_timeout_minutes = _settings.sync_task_timeout_minutes

# ── Dispatcher: fans out one sync task per active graph ──────────────

sync_dispatch_wf = hatchet.workflow(
    name="sync_dispatch_wf",
    on_crons=["* * * * *"],
    concurrency=ConcurrencyExpression(
        expression="'sync_dispatch'",
        max_runs=1,
        limit_strategy=ConcurrencyLimitStrategy.GROUP_ROUND_ROBIN,
    ),
)


@sync_dispatch_wf.task(
    execution_timeout=timedelta(minutes=2),
    schedule_timeout=timedelta(minutes=2),
)
async def dispatch_sync_tasks(input: dict, ctx: Context) -> dict:
    """Load active graphs and dispatch one sync_graph_wf per graph."""
    worker_state = cast(WorkerState, ctx.lifespan)

    # Always dispatch default graph
    slugs = ["default"]

    resolver = worker_state.graph_resolver
    if resolver is not None:
        try:
            graphs = await resolver.list_active_graphs()
            for g in graphs:
                if not g.is_default:
                    slugs.append(g.slug)
        except Exception:
            logger.warning("Could not load active graphs for sync dispatch", exc_info=True)

    for slug in slugs:
        try:
            await dispatch_workflow("sync_graph_wf", {"graph_slug": slug})
        except Exception:
            logger.error("Failed to dispatch sync for graph '%s'", slug, exc_info=True)

    logger.debug("Dispatched sync for %d graph(s): %s", len(slugs), slugs)
    return {"dispatched": slugs}


# ── Per-graph sync: one concurrent run per graph_slug ────────────────

sync_graph_wf = hatchet.workflow(
    name="sync_graph_wf",
    concurrency=ConcurrencyExpression(
        expression="input.graph_slug",
        max_runs=1,
        limit_strategy=ConcurrencyLimitStrategy.GROUP_ROUND_ROBIN,
    ),
)


@sync_graph_wf.task(
    execution_timeout=timedelta(minutes=sync_task_timeout_minutes),
    schedule_timeout=timedelta(minutes=2),
)
async def sync_graph_task(input: dict, ctx: Context) -> dict:
    """Sync a single graph: poll write-db and push changes to graph-db."""
    from kt_worker_sync.sync_engine import SyncEngine

    worker_state = cast(WorkerState, ctx.lifespan)
    graph_slug = input.get("graph_slug", "default")

    logger.debug("Sync task starting for graph '%s'", graph_slug)
    t0 = time.monotonic()

    try:
        if graph_slug == "default":
            engine = SyncEngine(
                write_session_factory=worker_state.write_session_factory,
                graph_session_factory=worker_state.session_factory,
                embedding_service=worker_state.embedding_service,
                batch_size=worker_state.settings.sync_batch_size,
                qdrant_client=worker_state.qdrant_client,
                graph_slug="default",
            )
        else:
            resolver = worker_state.graph_resolver
            if resolver is None:
                return {"error": "graph_resolver not available"}
            gs = await resolver.resolve_by_slug(graph_slug)
            engine = SyncEngine(
                write_session_factory=gs.write_session_factory,
                graph_session_factory=gs.graph_session_factory,
                embedding_service=worker_state.embedding_service,
                batch_size=worker_state.settings.sync_batch_size,
                qdrant_client=worker_state.qdrant_client,
                graph_slug=graph_slug,
            )

        counts = await engine.sync_cycle()
    except Exception:
        elapsed = time.monotonic() - t0
        logger.error("Sync task FAILED for graph '%s' after %.2fs", graph_slug, elapsed, exc_info=True)
        raise

    elapsed = time.monotonic() - t0
    total = sum(counts.values())
    if total > 0:
        logger.info("Sync '%s': %d records in %.2fs — %s", graph_slug, total, elapsed, counts)
        await ctx.aio_put_stream(json.dumps({"type": "sync_complete", "graph": graph_slug, "counts": counts}))
    else:
        logger.debug("Sync '%s': no changes (%.2fs)", graph_slug, elapsed)

    return {"graph": graph_slug, "synced": counts}
