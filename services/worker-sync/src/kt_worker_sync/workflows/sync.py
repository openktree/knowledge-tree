"""Hatchet workflow for periodic write-db → graph-db synchronization."""

from __future__ import annotations

import json
import logging
import time
from datetime import timedelta
from typing import cast

from hatchet_sdk import ConcurrencyExpression, ConcurrencyLimitStrategy, Context

from kt_hatchet.client import get_hatchet
from kt_hatchet.lifespan import WorkerState

logger = logging.getLogger(__name__)

hatchet = get_hatchet()

sync_wf = hatchet.workflow(
    name="sync_wf",
    on_crons=["* * * * *"],  # Every minute
    concurrency=ConcurrencyExpression(
        expression="'sync'",
        max_runs=1,
        limit_strategy=ConcurrencyLimitStrategy.GROUP_ROUND_ROBIN,
    ),
)


@sync_wf.task(execution_timeout=timedelta(minutes=5), schedule_timeout=timedelta(minutes=2))
async def sync_task(input: dict, ctx: Context) -> dict:
    """Single sync cycle: poll write-db and push changes to graph-db."""
    from kt_worker_sync.sync_engine import SyncEngine

    worker_state = cast(WorkerState, ctx.lifespan)

    logger.debug("Sync task starting")
    t0 = time.monotonic()

    try:
        engine = SyncEngine(
            write_session_factory=worker_state.write_session_factory,
            graph_session_factory=worker_state.session_factory,
            embedding_service=worker_state.embedding_service,
            batch_size=worker_state.settings.sync_batch_size,
            qdrant_client=worker_state.qdrant_client,
        )

        counts = await engine.sync_cycle()
    except Exception:
        elapsed = time.monotonic() - t0
        logger.error(
            "Sync task FAILED after %.2fs",
            elapsed,
            exc_info=True,
        )
        raise

    elapsed = time.monotonic() - t0
    total = sum(counts.values())
    if total > 0:
        logger.info("Sync task completed: %d records in %.2fs — %s", total, elapsed, counts)
        await ctx.aio_put_stream(json.dumps({"type": "sync_complete", "counts": counts}))
    else:
        logger.debug("Sync task completed: no changes (%.2fs)", elapsed)

    return {"synced": counts}
