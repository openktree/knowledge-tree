"""Seed deduplication — standalone Hatchet task.

Receives a batch of seed keys, fetches active seeds, and runs
``deduplicate_seed()`` on each.  Designed to be fanned out via
``aio_run_many()`` after seeds are committed during fact decomposition.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import cast

from hatchet_sdk import Context

from kt_config.settings import get_settings
from kt_hatchet.client import get_hatchet
from kt_hatchet.lifespan import WorkerState
from kt_hatchet.models import SeedDedupBatchInput, SeedDedupBatchOutput

logger = logging.getLogger(__name__)

hatchet = get_hatchet()
_schedule_timeout = timedelta(minutes=get_settings().hatchet_schedule_timeout_minutes)


@hatchet.task(
    name="seed_dedup_batch",
    input_validator=SeedDedupBatchInput,
    execution_timeout=timedelta(minutes=15),
    schedule_timeout=_schedule_timeout,
)
async def seed_dedup_task(input: SeedDedupBatchInput, ctx: Context) -> dict:
    """Deduplicate a batch of seeds in an independent session."""
    state = cast(WorkerState, ctx.lifespan)

    from kt_db.repositories.write_seeds import WriteSeedRepository
    from kt_facts.processing.seed_dedup import deduplicate_seed

    merges: dict[str, str] = {}
    processed = 0
    errors = 0

    async with (await state.resolve_sessions(input.graph_id))[1]() as session:
        repo = WriteSeedRepository(session)

        # Batch-fetch all seeds, filter to active only
        unique_keys = list(dict.fromkeys(input.seed_keys))
        seeds_by_key = await repo.get_seeds_by_keys_batch(unique_keys)
        active_seeds = [(k, s) for k, s in seeds_by_key.items() if s.status == "active"]

        # Build Qdrant seed repo — required for embedding dedup
        if state.qdrant_client is None:
            raise RuntimeError("Qdrant client is required for seed dedup but was not available on WorkerState")

        from kt_qdrant.repositories.seeds import QdrantSeedRepository

        qdrant_seed_repo = QdrantSeedRepository(state.qdrant_client)

        from kt_db.repositories.write_facts import WriteFactRepository

        write_fact_repo = WriteFactRepository(session)

        for seed_key, seed in active_seeds:
            try:
                async with session.begin_nested():
                    surviving = await deduplicate_seed(
                        seed_key=seed_key,
                        name=seed.name,
                        node_type=seed.node_type,
                        write_seed_repo=repo,
                        embedding_service=state.embedding_service,
                        qdrant_seed_repo=qdrant_seed_repo,
                        model_gateway=state.model_gateway,
                        write_fact_repo=write_fact_repo,
                    )
                processed += 1
                if surviving != seed_key:
                    merges[seed_key] = surviving
            except Exception:
                logger.exception("Dedup failed for seed '%s'", seed_key)
                errors += 1

        # Commit all successful savepoints. If an unhandled exception
        # escapes the loop this is skipped, but that matches the previous
        # begin()-per-seed semantics (all-or-nothing on unexpected errors).
        await session.commit()

    logger.info(
        "seed_dedup_batch: processed=%d merges=%d errors=%d scope=%s",
        processed,
        len(merges),
        errors,
        input.scope_id,
    )

    return SeedDedupBatchOutput(
        merges=merges,
        processed=processed,
        errors=errors,
    ).model_dump()
