"""Hatchet workflow for periodic write-db → graph-db synchronization."""

from __future__ import annotations

import json
import logging
import time
from datetime import timedelta
from typing import cast

from hatchet_sdk import ConcurrencyExpression, ConcurrencyLimitStrategy, Context

from kt_config.settings import get_settings
from kt_hatchet.client import get_hatchet
from kt_hatchet.lifespan import WorkerState

logger = logging.getLogger(__name__)

hatchet = get_hatchet()
_settings = get_settings()

sync_wf = hatchet.workflow(
    name="sync_wf",
    on_crons=["* * * * *"],  # Every minute
    concurrency=ConcurrencyExpression(
        expression="'sync'",
        max_runs=1,
        limit_strategy=ConcurrencyLimitStrategy.GROUP_ROUND_ROBIN,
    ),
)


@sync_wf.task(
    execution_timeout=timedelta(minutes=_settings.sync_task_timeout_minutes),
    schedule_timeout=timedelta(minutes=2),
)
async def sync_task(input: dict, ctx: Context) -> dict:
    """Single sync cycle: poll write-db and push changes to graph-db for all active graphs."""
    from kt_worker_sync.sync_engine import SyncEngine

    worker_state = cast(WorkerState, ctx.lifespan)

    logger.debug("Sync task starting")
    t0 = time.monotonic()

    all_counts: dict[str, dict[str, int]] = {}

    try:
        # Always sync the default graph using the default session factories
        default_engine = SyncEngine(
            write_session_factory=worker_state.write_session_factory,
            graph_session_factory=worker_state.session_factory,
            embedding_service=worker_state.embedding_service,
            batch_size=worker_state.settings.sync_batch_size,
            qdrant_client=worker_state.qdrant_client,
        )
        default_counts = await default_engine.sync_cycle()
        if sum(default_counts.values()) > 0:
            all_counts["default"] = default_counts

        # Sync non-default active graphs
        resolver = worker_state.graph_resolver
        if resolver is not None:
            try:
                graphs = await resolver.list_active_graphs()
            except Exception:
                logger.warning("Could not load active graphs for multi-graph sync", exc_info=True)
                graphs = []

            for graph in graphs:
                if graph.is_default:
                    continue
                try:
                    gs = await resolver.resolve(graph.id)
                    graph_engine = SyncEngine(
                        write_session_factory=gs.write_session_factory,
                        graph_session_factory=gs.graph_session_factory,
                        embedding_service=worker_state.embedding_service,
                        batch_size=worker_state.settings.sync_batch_size,
                        qdrant_client=worker_state.qdrant_client,
                    )
                    graph_counts = await graph_engine.sync_cycle()
                    if sum(graph_counts.values()) > 0:
                        all_counts[graph.slug] = graph_counts
                except Exception:
                    logger.error("Sync failed for graph '%s'", graph.slug, exc_info=True)
    except Exception:
        elapsed = time.monotonic() - t0
        logger.error(
            "Sync task FAILED after %.2fs",
            elapsed,
            exc_info=True,
        )
        raise

    elapsed = time.monotonic() - t0
    total = sum(sum(c.values()) for c in all_counts.values())
    if total > 0:
        logger.info("Sync task completed: %d records in %.2fs — %s", total, elapsed, all_counts)
        await ctx.aio_put_stream(json.dumps({"type": "sync_complete", "counts": all_counts}))
    else:
        logger.debug("Sync task completed: no changes (%.2fs)", elapsed)

    return {"synced": all_counts}
