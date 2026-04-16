"""Cron-driven sweeper that processes orphan facts in the default graph.

When non-default graphs contribute facts via the public-cache bridge,
raw sources and facts land in the default graph's write-db but no
downstream processing runs (entity extraction, seed creation, node
pipeline). This workflow finds those orphan facts every 10 minutes
and dispatches the same task chain that normal ingest uses.

Orphan = WriteFact with dedup_status='ready' and no matching
WriteSeedFact row (never seen by entity extraction).
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, cast

from hatchet_sdk import ConcurrencyExpression, ConcurrencyLimitStrategy, Context
from sqlalchemy import text

from kt_config.settings import get_settings
from kt_hatchet.client import get_hatchet
from kt_hatchet.lifespan import WorkerState

logger = logging.getLogger(__name__)

hatchet = get_hatchet()


orphan_fact_sweep_wf = hatchet.workflow(
    name="orphan_fact_sweep_wf",
    on_crons=["*/10 * * * *"],
    concurrency=ConcurrencyExpression(
        expression="'orphan_fact_sweep'",
        max_runs=1,
        limit_strategy=ConcurrencyLimitStrategy.GROUP_ROUND_ROBIN,
    ),
)


async def _find_orphan_fact_ids(
    write_session: Any,
    cutoff: datetime,
    batch_size: int,
) -> list[uuid.UUID]:
    """Find WriteFact rows with no seed-fact link (never entity-extracted)."""
    result = await write_session.execute(
        text("""
            SELECT wf.id
            FROM write_facts wf
            WHERE wf.dedup_status = 'ready'
              AND wf.created_at < :cutoff
              AND NOT EXISTS (
                SELECT 1 FROM write_seed_facts wsf WHERE wsf.fact_id = wf.id
              )
            ORDER BY wf.created_at
            LIMIT :batch_size
        """),
        {"cutoff": cutoff, "batch_size": batch_size},
    )
    return [row[0] for row in result.fetchall()]


@orphan_fact_sweep_wf.task(
    execution_timeout=timedelta(minutes=30),
    schedule_timeout=timedelta(minutes=2),
)
async def sweep_orphan_facts(input: dict, ctx: Context) -> dict:
    """Find orphan facts in the default graph and run entity extraction → seeds → nodes."""
    state = cast(WorkerState, ctx.lifespan)

    if state.default_graph_id is None:
        logger.debug("orphan_fact_sweep: no default_graph_id — skipping")
        return {"orphan_facts": 0, "seeds_created": 0, "nodes_created": 0}

    settings = get_settings()
    min_age = timedelta(minutes=settings.orphan_fact_sweep_min_age_minutes)
    cutoff = datetime.now(UTC).replace(tzinfo=None) - min_age
    batch_size = settings.orphan_fact_sweep_batch_size

    graph_id_str = str(state.default_graph_id)
    _, write_sf = await state.resolve_sessions(graph_id_str)

    # ── Find orphan facts ─────────────────────────────────────────
    write_session = write_sf()
    try:
        orphan_ids = await _find_orphan_fact_ids(write_session, cutoff, batch_size)
    finally:
        await write_session.close()

    if not orphan_ids:
        logger.debug("orphan_fact_sweep: no orphan facts found")
        return {"orphan_facts": 0, "seeds_created": 0, "nodes_created": 0}

    logger.info("orphan_fact_sweep: found %d orphan facts", len(orphan_ids))
    fact_id_strs = [str(fid) for fid in orphan_ids]

    # ── Entity extraction ─────────────────────────────────────────
    from kt_hatchet.models import EntityExtractionInput, EntityExtractionOutput
    from kt_worker_search.workflows.decompose import entity_extraction_task

    chunk_size = 1000
    fact_chunks = [fact_id_strs[i : i + chunk_size] for i in range(0, len(fact_id_strs), chunk_size)]

    all_extracted_nodes: list[dict[str, Any]] = []

    if len(fact_chunks) == 1:
        entity_result = await entity_extraction_task.aio_run(
            EntityExtractionInput(
                fact_ids=fact_id_strs,
                concept="orphan fact processing",
                graph_id=graph_id_str,
            ),
        )
        entity_output = EntityExtractionOutput.model_validate(entity_result)
        all_extracted_nodes = entity_output.extracted_nodes
    else:
        bulk_items = [
            entity_extraction_task.create_bulk_run_item(
                input=EntityExtractionInput(
                    fact_ids=chunk,
                    concept="orphan fact processing",
                    graph_id=graph_id_str,
                ),
            )
            for chunk in fact_chunks
        ]
        chunk_results = await entity_extraction_task.aio_run_many(bulk_items)
        for result in chunk_results:
            entity_output = EntityExtractionOutput.model_validate(result)
            all_extracted_nodes.extend(entity_output.extracted_nodes)

    if not all_extracted_nodes:
        logger.info("orphan_fact_sweep: entity extraction returned no nodes")
        return {"orphan_facts": len(orphan_ids), "seeds_created": 0, "nodes_created": 0}

    logger.info("orphan_fact_sweep: extracted %d entities", len(all_extracted_nodes))

    # ── Seed storage (single-writer, mirrors decompose_sources) ───
    all_referenced_fact_ids: set[str] = set()
    for node in all_extracted_nodes:
        all_referenced_fact_ids.update(node.get("fact_ids", []))

    seed_keys: list[str] = []
    write_session = write_sf()
    try:
        from kt_db.repositories.write_facts import WriteFactRepository
        from kt_db.repositories.write_seeds import WriteSeedRepository
        from kt_facts.processing.seed_extraction import store_seeds_from_extracted_nodes
        from kt_models.embeddings import EmbeddingService
        from kt_qdrant.repositories.seeds import QdrantSeedRepository

        embedding_service = cast(EmbeddingService, state.embedding_service)

        write_fact_repo = WriteFactRepository(write_session)
        fact_uuids = [uuid.UUID(fid) for fid in all_referenced_fact_ids]
        write_facts = await write_fact_repo.get_by_ids(fact_uuids)

        fact_id_to_pos = {str(f.id): i + 1 for i, f in enumerate(write_facts)}
        for node in all_extracted_nodes:
            node["fact_indices"] = [fact_id_to_pos[fid] for fid in node.get("fact_ids", []) if fid in fact_id_to_pos]

        write_seed_repo = WriteSeedRepository(write_session)

        if state.qdrant_client is None:
            raise RuntimeError("Qdrant client required for seed extraction")
        qdrant_seed_repo = QdrantSeedRepository(state.qdrant_client)

        _link_count, seed_keys = await store_seeds_from_extracted_nodes(
            all_extracted_nodes,
            write_facts,
            write_seed_repo,
            embedding_service=embedding_service,
            qdrant_seed_repo=qdrant_seed_repo,
        )

        await write_session.commit()
    except Exception:
        logger.exception("orphan_fact_sweep: seed storage failed")
        try:
            await write_session.rollback()
        except Exception:
            pass
        seed_keys = []
    finally:
        await write_session.close()

    logger.info("orphan_fact_sweep: created %d seed keys", len(seed_keys))

    # ── Seed dedup ────────────────────────────────────────────────
    if seed_keys:
        try:
            from kt_hatchet.client import run_workflow

            await run_workflow(
                "seed_dedup_batch",
                {
                    "seed_keys": list(seed_keys),
                    "scope_id": "orphan_fact_sweep",
                    "graph_id": graph_id_str,
                },
            )
        except Exception:
            logger.warning("orphan_fact_sweep: seed_dedup_batch dispatch failed", exc_info=True)

    # ── Auto-build: list seeds → dispatch node_pipeline_wf ────────
    from kt_db.keys import key_to_uuid, make_seed_key
    from kt_db.repositories.write_seeds import WriteSeedRepository
    from kt_hatchet.models import BuildNodeInput
    from kt_worker_nodes.workflows.node_pipeline import node_pipeline_wf

    proposed: list[dict[str, Any]] = []
    try:
        write_session = write_sf()
        try:
            seed_repo = WriteSeedRepository(write_session)
            seeds = await seed_repo.list_seeds(exclude_merged=True, limit=500)

            for seed in seeds:
                if seed.status in ("garbage",):
                    continue
                existing_id = None
                if seed.status == "promoted" and seed.promoted_node_key:
                    existing_id = str(key_to_uuid(seed.promoted_node_key))

                aliases = (seed.metadata_ or {}).get("aliases", []) if seed.metadata_ else []
                proposed.append(
                    {
                        "name": seed.name,
                        "node_type": seed.node_type,
                        "entity_subtype": seed.entity_subtype,
                        "seed_key": seed.key,
                        "existing_node_id": existing_id,
                        "fact_count": seed.fact_count,
                        "aliases": aliases,
                    }
                )
        finally:
            await write_session.close()
    except Exception:
        logger.warning("orphan_fact_sweep: seed listing failed", exc_info=True)

    created_node_count = 0

    if proposed:
        bulk_items = []
        for p in proposed:
            sk = p["seed_key"] or make_seed_key(p["node_type"], p["name"])
            bulk_items.append(
                node_pipeline_wf.create_bulk_run_item(
                    input=BuildNodeInput(
                        scope_id="orphan_fact_sweep",
                        concept=p["name"],
                        node_type=p["node_type"],
                        entity_subtype=p.get("entity_subtype"),
                        seed_key=sk,
                        existing_node_id=p.get("existing_node_id"),
                        graph_id=graph_id_str,
                    ),
                )
            )

        logger.info("orphan_fact_sweep: dispatching %d node pipelines", len(bulk_items))
        ctx.refresh_timeout("2h")

        results = await node_pipeline_wf.aio_run_many(bulk_items)

        for result in results:
            create_data: dict = result.get("create_node", {}) if isinstance(result, dict) else {}
            if create_data.get("node_id"):
                created_node_count += 1

    logger.info(
        "orphan_fact_sweep: done orphans=%d seeds=%d nodes=%d",
        len(orphan_ids),
        len(seed_keys),
        created_node_count,
    )

    return {
        "orphan_facts": len(orphan_ids),
        "seeds_created": len(seed_keys),
        "nodes_created": created_node_count,
    }
