"""Auto-build graph from accumulated seeds.

Promotes seeds to stub nodes.  Edges are created exclusively via the
candidate-based resolver in the node pipeline (no co-occurrence shortcut).
"""

from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from typing import cast

from hatchet_sdk import Context

from kt_config.settings import get_settings
from kt_hatchet.client import get_hatchet
from kt_hatchet.lifespan import WorkerState
from kt_hatchet.models import AutoBuildInput, AutoBuildOutput

logger = logging.getLogger(__name__)

hatchet = get_hatchet()
_schedule_timeout = timedelta(minutes=get_settings().hatchet_schedule_timeout_minutes)


auto_build_task = hatchet.workflow(
    name="auto_build_graph",
    input_validator=AutoBuildInput,
)


@auto_build_task.task(
    execution_timeout=timedelta(minutes=30),
    schedule_timeout=_schedule_timeout,
)
async def auto_build_graph(input: AutoBuildInput, ctx: Context) -> dict:
    """Promote seeds to stub nodes and dispatch enrichment for stale nodes.

    Step 1: Promote eligible seeds to stub nodes (concept name embedding only).
    Step 2: Absorb merged seed nodes into winner nodes.
    Step 3: Dispatch node_pipeline for fact-stale nodes.
    """
    state = cast(WorkerState, ctx.lifespan)
    settings = get_settings()

    nodes_promoted = 0
    nodes_absorbed = 0
    nodes_recalculated = 0
    nodes_enrichment_dispatched = 0

    # -- Step 1: Promote seeds to stub nodes -------------------------------
    try:
        nodes_promoted, nodes_enrichment_dispatched = await _promote_seeds(state, settings, ctx)
    except Exception:
        logger.exception("Error in seed promotion step")

    # -- Step 2: Absorb merged nodes ---------------------------------------
    try:
        nodes_absorbed = await _absorb_merged_nodes(state, settings, ctx)
    except Exception:
        logger.exception("Error in merge absorption step")

    # -- Step 3: Dispatch enrichment for fact-stale nodes ------------------
    try:
        nodes_recalculated = await _check_fact_stale_nodes(state, settings, ctx)
    except Exception:
        logger.exception("Error in fact-stale recalculation step")

    result = AutoBuildOutput(
        nodes_promoted=nodes_promoted,
        nodes_absorbed=nodes_absorbed,
        nodes_recalculated=nodes_recalculated,
        nodes_enrichment_dispatched=nodes_enrichment_dispatched,
    )

    return result.model_dump()


async def _promote_seeds(state: WorkerState, settings: object, ctx: Context) -> tuple[int, int]:
    """Promote active seeds to stub nodes.

    Snapshots all eligible seed keys up front, then processes them in
    batches.  This avoids re-querying during promotion, which could
    pick up seeds still being created by a concurrent ingestion and
    run indefinitely.

    Returns (nodes_promoted, 0).  Enrichment is NOT dispatched here --
    ``_check_fact_stale_nodes`` detects newly promoted stubs (facts_at_last_build=0)
    and dispatches ``node_pipeline`` for them.
    """
    from kt_db.keys import key_to_uuid, make_node_key
    from kt_db.repositories.write_nodes import WriteNodeRepository
    from kt_db.repositories.write_seeds import WriteSeedRepository
    from kt_models.embeddings import EmbeddingService

    min_facts = settings.graph_build_auto_promote_min_facts  # type: ignore[attr-defined]
    batch_size = settings.graph_build_batch_size  # type: ignore[attr-defined]

    embedding_service = EmbeddingService()
    promoted = 0

    write_sf = state.write_session_factory
    if write_sf is None:
        logger.error("No write session factory available")
        return 0, 0

    # Snapshot eligible seed keys once to avoid chasing a moving target
    seed_keys: list[str] = []
    async with write_sf() as ws:
        seed_repo = WriteSeedRepository(ws)
        all_seeds = await seed_repo.get_promotable_seeds(min_facts, limit=10_000)
        seed_keys = [s.key for s in all_seeds]

    if not seed_keys:
        return 0, 0

    logger.info("Found %d promotable seeds to process", len(seed_keys))

    # Process in batches
    for i in range(0, len(seed_keys), batch_size):
        batch_keys = seed_keys[i : i + batch_size]
        batch_promoted = 0

        async with write_sf() as ws:
            seed_repo = WriteSeedRepository(ws)
            node_repo = WriteNodeRepository(ws)

            # Re-fetch seeds by key (they may have been merged/promoted
            # by a concurrent process since the snapshot)
            seeds = [await seed_repo.get_seed_by_key(k) for k in batch_keys]
            seeds = [s for s in seeds if s is not None and s.status == "active"]

            if not seeds:
                continue

            total_batches = -(-len(seed_keys) // batch_size)
            logger.info(
                "Promoting batch %d/%d (%d seeds)",
                i // batch_size + 1,
                total_batches,
                len(seeds),
            )

            # Batch embed concept names
            names = [s.name for s in seeds]
            try:
                embeddings = await embedding_service.embed_batch(names)
            except Exception:
                logger.exception("Failed to batch-embed seed names, skipping embedding")
                embeddings = [None] * len(seeds)

            for seed, embedding in zip(seeds, embeddings):
                try:
                    node_key = make_node_key(seed.name)
                    node_uuid = key_to_uuid(node_key)

                    async with ws.begin_nested():
                        # Create WriteNode with enrichment_status='stub'
                        # facts_at_last_build=0 so _check_fact_stale_nodes picks it up
                        await node_repo.upsert(
                            node_type=seed.node_type,
                            concept=seed.name,
                            entity_subtype=seed.entity_subtype,
                            metadata_=seed.metadata_,
                            enrichment_status="stub",
                        )
                        await node_repo.update_facts_at_last_build(node_key, 0)

                        # Copy seed fact IDs to node
                        seed_facts = await seed_repo.get_seed_fact_ids(seed.key)
                        for fid in seed_facts:
                            await node_repo.append_fact_id(node_key, str(fid))

                        # Mark seed as promoted
                        await seed_repo.mark_seed_promoted(seed.key, node_key)

                    # Qdrant upsert outside savepoint (not transactional)
                    if embedding is not None and state.qdrant_client is not None:
                        from kt_qdrant.repositories.nodes import QdrantNodeRepository

                        qdrant_repo = QdrantNodeRepository(state.qdrant_client)
                        await qdrant_repo.upsert(
                            node_id=node_uuid,
                            concept=seed.name,
                            node_type=seed.node_type,
                            embedding=embedding,
                        )

                    batch_promoted += 1

                except Exception:
                    logger.warning("Error promoting seed %s", seed.key, exc_info=True)

                await asyncio.sleep(0)  # yield to event loop between seeds

            await ws.commit()

        promoted += batch_promoted

    logger.info("Promoted %d seeds to stub nodes", promoted)
    return promoted, 0


async def _absorb_merged_nodes(state: WorkerState, settings: object, ctx: Context) -> int:
    """Absorb nodes from merged seeds into the winner's node.

    When a seed is merged (loser), but had already been promoted to a node,
    the loser's node data (dimensions, edges, justifications, facts) must be
    transferred to the winner's node and the loser node deleted.
    """
    from kt_db.keys import key_to_uuid, make_edge_key
    from kt_db.repositories.write_dimensions import WriteDimensionRepository
    from kt_db.repositories.write_edges import WriteEdgeRepository
    from kt_db.repositories.write_nodes import WriteNodeRepository
    from kt_db.repositories.write_seeds import WriteSeedRepository

    batch_size = settings.graph_build_batch_size  # type: ignore[attr-defined]

    write_sf = state.write_session_factory
    if write_sf is None:
        return 0

    absorbed = 0

    async with write_sf() as ws:
        seed_repo = WriteSeedRepository(ws)
        node_repo = WriteNodeRepository(ws)
        edge_repo = WriteEdgeRepository(ws)
        dim_repo = WriteDimensionRepository(ws)

        merged_seeds = await seed_repo.get_merged_promoted_seeds(limit=batch_size)
        if not merged_seeds:
            return 0

        logger.info("Found %d merged seeds with nodes to absorb", len(merged_seeds))

        for loser_seed in merged_seeds:
            loser_node_key_for_qdrant: str | None = None
            try:
                loser_node_key = loser_seed.promoted_node_key
                if not loser_node_key:
                    continue

                winner_key = loser_seed.merged_into_key
                if not winner_key:
                    continue

                winner_seed = await seed_repo.get_seed_by_key(winner_key)
                if winner_seed is None:
                    continue

                winner_node_key = winner_seed.promoted_node_key
                if not winner_node_key:
                    continue

                async with ws.begin_nested():
                    # Verify both nodes exist
                    winner_node = await node_repo.get_by_key(winner_node_key)
                    loser_node = await node_repo.get_by_key(loser_node_key)
                    if winner_node is None or loser_node is None:
                        # Already absorbed or missing — clear and skip
                        await seed_repo.clear_promoted_node_key(loser_seed.key)
                        continue

                    # ── Absorb dimensions ──
                    loser_dims = await dim_repo.get_by_node_key(loser_node_key, limit=100)
                    for dim in loser_dims:
                        await dim_repo.upsert(
                            node_key=winner_node_key,
                            model_id=dim.model_id,
                            content=dim.content,
                            confidence=dim.confidence,
                            suggested_concepts=dim.suggested_concepts,
                            batch_index=dim.batch_index,
                            fact_count=dim.fact_count,
                            is_definitive=dim.is_definitive,
                            fact_ids=dim.fact_ids,
                            metadata_=dim.metadata_,
                        )
                        await dim_repo.delete_by_key(dim.key)

                    # ── Absorb edges ──
                    loser_edges = await edge_repo.get_edges_for_node(loser_node_key)
                    for edge in loser_edges:
                        other_key = (
                            edge.target_node_key if edge.source_node_key == loser_node_key else edge.source_node_key
                        )
                        if other_key == winner_node_key:
                            await edge_repo.delete_by_key(edge.key)
                            continue

                        new_edge_key = make_edge_key(edge.relationship_type, winner_node_key, other_key)
                        old_edge_key = edge.key
                        merged_facts = list(set(edge.fact_ids or []))

                        await edge_repo.upsert(
                            rel_type=edge.relationship_type,
                            source_node_key=winner_node_key,
                            target_node_key=other_key,
                            weight=edge.weight,
                            justification=edge.justification,
                            fact_ids=merged_facts,
                            metadata_=edge.metadata_,
                            weight_source=edge.weight_source,
                        )
                        if old_edge_key != new_edge_key:
                            await edge_repo.delete_by_key(old_edge_key)

                    # ── Absorb facts ──
                    loser_fact_ids = loser_node.fact_ids or []
                    if loser_fact_ids:
                        await node_repo.merge_fact_ids(winner_node_key, loser_fact_ids)

                    # ── Clean up derived data ──
                    await dim_repo.delete_convergence_report(loser_node_key)
                    await dim_repo.delete_divergent_claims(loser_node_key)

                    # ── Delete loser node ──
                    await node_repo.delete_by_key(loser_node_key)

                    await seed_repo.clear_promoted_node_key(loser_seed.key)
                    loser_node_key_for_qdrant = loser_node_key

                # Qdrant delete outside savepoint (not transactional)
                if loser_node_key_for_qdrant and state.qdrant_client is not None:
                    try:
                        from kt_qdrant.repositories.nodes import QdrantNodeRepository

                        await QdrantNodeRepository(state.qdrant_client).delete(key_to_uuid(loser_node_key_for_qdrant))
                    except Exception:
                        logger.debug("Failed to delete Qdrant vector for %s", loser_node_key_for_qdrant, exc_info=True)
                absorbed += 1
                logger.info("Absorbed node '%s' into '%s'", loser_node_key, winner_node_key)

            except Exception:
                logger.debug("Error absorbing seed %s", loser_seed.key, exc_info=True)

            await asyncio.sleep(0)  # yield to event loop between absorptions

        await ws.commit()

    logger.info("Absorbed %d merged nodes", absorbed)
    return absorbed


async def _check_fact_stale_nodes(state: WorkerState, settings: object, ctx: Context) -> int:
    """Dispatch node_pipeline in rebuild mode for nodes with accumulated new facts."""
    from kt_db.keys import key_to_uuid
    from kt_db.repositories.write_seeds import WriteSeedRepository

    threshold = settings.dimension_fact_limit  # type: ignore[attr-defined]

    write_sf = state.write_session_factory
    if write_sf is None:
        return 0

    dispatched = 0

    async with write_sf() as ws:
        seed_repo = WriteSeedRepository(ws)
        stale_nodes = await seed_repo.get_fact_stale_nodes(threshold)
        if not stale_nodes:
            return 0

        logger.info("Found %d fact-stale nodes to process", len(stale_nodes))

        from kt_hatchet.models import NodePipelineInput
        from kt_worker_nodes.workflows.node_pipeline import node_pipeline_wf

        dispatch_entries: list[tuple[str, str, dict]] = []
        for entry in stale_nodes:
            try:
                node_key = entry["promoted_node_key"]
                node_uuid = key_to_uuid(node_key)
                dispatch_entries.append((node_key, str(node_uuid), entry))
            except Exception:
                logger.debug("Error preparing dispatch for %s", entry.get("promoted_node_key"), exc_info=True)

    batch_size = settings.graph_build_auto_recalculate_batch_size  # type: ignore[attr-defined]

    async def _dispatch_one(node_key: str, node_uuid: str, entry: dict) -> bool:
        try:
            await node_pipeline_wf.aio_run_no_wait(
                NodePipelineInput(mode="rebuild_incremental", scope="all", node_id=node_uuid),
            )
            logger.info(
                "Dispatched node_pipeline (rebuild) for '%s' (status=%s, delta=%d, total_facts=%d)",
                node_key,
                entry.get("enrichment_status", "?"),
                entry["delta"],
                entry["fact_count"],
            )
            return True
        except Exception:
            logger.debug("Error dispatching node_pipeline for %s", node_key, exc_info=True)
            return False

    for i in range(0, len(dispatch_entries), batch_size):
        batch = dispatch_entries[i : i + batch_size]
        results = await asyncio.gather(*(_dispatch_one(nk, nu, e) for nk, nu, e in batch))
        dispatched += sum(1 for r in results if r)
        await asyncio.sleep(0)  # yield to event loop between dispatch batches

    logger.info("Dispatched %d fact-stale node pipeline tasks", dispatched)
    return dispatched
