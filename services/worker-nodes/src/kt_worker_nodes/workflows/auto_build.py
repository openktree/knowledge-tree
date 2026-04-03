"""Auto-build graph from accumulated seeds — zero LLM calls.

Promotes seeds to stub nodes and creates edges from co-occurrence data.
Edge weight = log2(shared_fact_count + 1), no justification (generated on demand).
"""

from __future__ import annotations

import json
import logging
import math
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
    """Promote seeds to stub nodes and create co-occurrence edges.

    Step 1: Promote eligible seeds to stub nodes (concept name embedding only).
    Step 2: Create edges from co-occurrence (log weight, no LLM).
    Step 3: Recalculate weights for existing co-occurrence edges.
    """
    state = cast(WorkerState, ctx.lifespan)
    settings = get_settings()

    nodes_promoted = 0
    nodes_absorbed = 0
    edges_created = 0
    edges_updated = 0
    nodes_recalculated = 0
    nodes_enrichment_dispatched = 0

    # ── Step 1: Promote seeds to stub nodes ──────────────────────────
    try:
        nodes_promoted, nodes_enrichment_dispatched = await _promote_seeds(state, settings, ctx)
    except Exception:
        logger.exception("Error in seed promotion step")

    # ── Step 2: Absorb merged nodes ──────────────────────────────────
    try:
        nodes_absorbed = await _absorb_merged_nodes(state, settings, ctx)
    except Exception:
        logger.exception("Error in merge absorption step")

    # ── Step 3: Create edges from co-occurrence ──────────────────────
    try:
        edges_created = await _create_cooccurrence_edges(state, settings, ctx)
    except Exception:
        logger.exception("Error in edge creation step")

    # ── Step 4: Dispatch recalculation for fact-stale nodes ──────────
    try:
        nodes_recalculated = await _check_fact_stale_nodes(state, settings, ctx)
    except Exception:
        logger.exception("Error in fact-stale recalculation step")

    result = AutoBuildOutput(
        nodes_promoted=nodes_promoted,
        nodes_absorbed=nodes_absorbed,
        edges_created=edges_created,
        edges_updated=edges_updated,
        nodes_recalculated=nodes_recalculated,
        nodes_enrichment_dispatched=nodes_enrichment_dispatched,
    )

    try:
        await ctx.aio_put_stream(
            json.dumps(
                {
                    "type": "auto_build_complete",
                    "nodes_promoted": nodes_promoted,
                    "nodes_absorbed": nodes_absorbed,
                    "edges_created": edges_created,
                    "nodes_recalculated": nodes_recalculated,
                    "nodes_enrichment_dispatched": nodes_enrichment_dispatched,
                }
            )
        )
    except Exception:
        pass

    return result.model_dump()


async def _promote_seeds(state: WorkerState, settings: object, ctx: Context) -> tuple[int, int]:
    """Promote active seeds to stub nodes.

    Loops in batches until all eligible seeds are promoted so a single
    auto_build run drains the full backlog.

    Returns (nodes_promoted, 0).  Enrichment is NOT dispatched here —
    ``_check_fact_stale_nodes`` detects newly promoted stubs (facts_at_last_build=0)
    and dispatches ``rebuild_node`` for them.
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

    while True:
        batch_promoted = 0

        async with write_sf() as ws:
            seed_repo = WriteSeedRepository(ws)
            node_repo = WriteNodeRepository(ws)

            seeds = await seed_repo.get_promotable_seeds(min_facts, limit=batch_size)
            if not seeds:
                break

            logger.info("Promoting batch of %d seeds (%d promoted so far)", len(seeds), promoted)

            # Batch embed concept names
            names = [s.name for s in seeds]
            try:
                embeddings = await embedding_service.embed_batch(names)
            except Exception:
                logger.exception("Failed to batch-embed seed names, skipping embedding")
                embeddings = [None] * len(seeds)

            for seed, embedding in zip(seeds, embeddings):
                try:
                    node_key = make_node_key(seed.node_type, seed.name)
                    node_uuid = key_to_uuid(node_key)

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

                    # Upsert to Qdrant if embedding available
                    if embedding is not None and state.qdrant_client is not None:
                        from kt_qdrant.repositories.nodes import QdrantNodeRepository

                        qdrant_repo = QdrantNodeRepository(state.qdrant_client)
                        await qdrant_repo.upsert(
                            node_id=node_uuid,
                            concept=seed.name,
                            node_type=seed.node_type,
                            embedding=embedding,
                        )

                    # Mark seed as promoted
                    await seed_repo.mark_seed_promoted(seed.key, node_key)
                    batch_promoted += 1

                except Exception:
                    logger.warning("Error promoting seed %s", seed.key, exc_info=True)

            await ws.commit()

        promoted += batch_promoted

        # If this batch was smaller than the limit, there are no more
        if len(seeds) < batch_size:
            break

    logger.info("Promoted %d seeds to stub nodes", promoted)
    try:
        await ctx.aio_put_stream(
            json.dumps(
                {
                    "type": "auto_build_progress",
                    "step": "promote",
                    "nodes_promoted": promoted,
                }
            )
        )
    except Exception:
        pass

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
            try:
                loser_node_key = loser_seed.promoted_node_key
                if not loser_node_key:
                    continue

                # Find the winner seed (follow merge chain)
                winner_key = loser_seed.merged_into_key
                if not winner_key:
                    continue

                winner_seed = await seed_repo.get_seed_by_key(winner_key)
                if winner_seed is None:
                    continue

                winner_node_key = winner_seed.promoted_node_key
                if not winner_node_key:
                    # Winner not promoted yet — skip, will retry next run
                    continue

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
                    # Upsert under the winner node key (handles duplicates via ON CONFLICT)
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
                    # Determine the other node in the edge
                    if edge.source_node_key == loser_node_key:
                        other_key = edge.target_node_key
                    else:
                        other_key = edge.source_node_key

                    # Self-edge after rewrite — delete
                    if other_key == winner_node_key:
                        await edge_repo.delete_by_key(edge.key)
                        continue

                    new_edge_key = make_edge_key(
                        edge.relationship_type,
                        winner_node_key,
                        other_key,
                    )
                    old_edge_key = edge.key

                    # Merge fact_ids if winner already has an edge to the same node
                    existing_new_facts = edge.fact_ids or []
                    merged_facts = list(set(existing_new_facts))

                    # Upsert the new edge (winner_node_key replaces loser)
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

                    # Delete the old edge key if it changed
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

                # Delete from Qdrant
                if state.qdrant_client is not None:
                    try:
                        from kt_qdrant.repositories.nodes import QdrantNodeRepository

                        qdrant_repo = QdrantNodeRepository(state.qdrant_client)
                        await qdrant_repo.delete(key_to_uuid(loser_node_key))
                    except Exception:
                        logger.debug(
                            "Failed to delete Qdrant vector for %s",
                            loser_node_key,
                            exc_info=True,
                        )

                # Mark as absorbed (clear promoted_node_key so not reprocessed)
                await seed_repo.clear_promoted_node_key(loser_seed.key)
                absorbed += 1

                logger.info(
                    "Absorbed node '%s' into '%s'",
                    loser_node_key,
                    winner_node_key,
                )

            except Exception:
                logger.debug(
                    "Error absorbing seed %s",
                    loser_seed.key,
                    exc_info=True,
                )

        await ws.commit()

    logger.info("Absorbed %d merged nodes", absorbed)
    try:
        await ctx.aio_put_stream(
            json.dumps(
                {
                    "type": "auto_build_progress",
                    "step": "absorb",
                    "nodes_absorbed": absorbed,
                }
            )
        )
    except Exception:
        pass

    return absorbed


async def _create_cooccurrence_edges(state: WorkerState, settings: object, ctx: Context) -> int:
    """Create edges from seed co-occurrence data."""
    from kt_db.repositories.write_edges import WriteEdgeRepository
    from kt_db.repositories.write_seeds import WriteSeedRepository

    min_shared = settings.graph_build_edge_min_shared_facts  # type: ignore[attr-defined]
    created = 0

    write_sf = state.write_session_factory
    if write_sf is None:
        return 0

    async with write_sf() as ws:
        seed_repo = WriteSeedRepository(ws)
        edge_repo = WriteEdgeRepository(ws)

        pairs = await seed_repo.get_buildable_edge_pairs(min_shared)
        if not pairs:
            return 0

        logger.info("Found %d edge candidate pairs", len(pairs))

        # Pre-fetch seeds to determine node types
        all_seed_keys = set()
        for a, b, _, _ in pairs:
            all_seed_keys.add(a)
            all_seed_keys.add(b)
        seeds_map = await seed_repo.get_seeds_by_keys_batch(list(all_seed_keys))

        for seed_key_a, seed_key_b, shared_count, fact_ids in pairs:
            try:
                seed_a = seeds_map.get(seed_key_a)
                seed_b = seeds_map.get(seed_key_b)
                if not seed_a or not seed_b:
                    continue

                # Get node keys from promoted seeds
                node_key_a = seed_a.promoted_node_key
                node_key_b = seed_b.promoted_node_key
                if not node_key_a or not node_key_b:
                    continue

                # Determine relationship type
                same_type = seed_a.node_type == seed_b.node_type
                rel_type = "related" if same_type else "cross_type"

                # log₂(n+1): clear signal without overwhelming
                weight = math.log2(shared_count + 1)

                await edge_repo.upsert(
                    rel_type=rel_type,
                    source_node_key=node_key_a,
                    target_node_key=node_key_b,
                    weight=weight,
                    fact_ids=fact_ids,
                    weight_source="cooccurrence",
                )
                created += 1

            except Exception:
                logger.debug(
                    "Error creating edge %s <-> %s",
                    seed_key_a,
                    seed_key_b,
                    exc_info=True,
                )

        await ws.commit()

    logger.info("Created %d co-occurrence edges", created)
    try:
        await ctx.aio_put_stream(
            json.dumps(
                {
                    "type": "auto_build_progress",
                    "step": "edges",
                    "edges_created": created,
                }
            )
        )
    except Exception:
        pass

    return created


async def _check_fact_stale_nodes(state: WorkerState, settings: object, ctx: Context) -> int:
    """Dispatch enrichment or recalculation for nodes with accumulated new facts.

    Dispatches ``rebuild_node`` in incremental mode for all stale nodes.
    """
    from kt_db.keys import key_to_uuid
    from kt_db.repositories.write_seeds import WriteSeedRepository

    # Use dimension_fact_limit as the threshold: a node qualifies for rebuild
    # when it has accumulated enough new facts to produce a new dimension batch.
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

        from kt_hatchet.models import RebuildNodeInput
        from kt_worker_nodes.workflows.rebuild_node import rebuild_node_task as _rebuild

        # Do NOT update facts_at_last_build here — rebuild_node updates it
        # after successful dimension generation, preventing the watermark
        # from advancing if the task fails.
        dispatch_entries: list[tuple[str, str, dict]] = []
        for entry in stale_nodes:
            try:
                node_key = entry["promoted_node_key"]
                node_uuid = key_to_uuid(node_key)
                dispatch_entries.append((node_key, str(node_uuid), entry))
            except Exception:
                logger.debug(
                    "Error preparing dispatch for %s",
                    entry.get("promoted_node_key"),
                    exc_info=True,
                )

    # Dispatch in batches to provide backpressure — each batch fires
    # concurrently, then we move to the next.  The full list was loaded
    # upfront so no new nodes can sneak in mid-loop.
    import asyncio

    batch_size = settings.graph_build_auto_recalculate_batch_size  # type: ignore[attr-defined]

    async def _dispatch_one(node_key: str, node_uuid: str, entry: dict) -> bool:
        try:
            await _rebuild.aio_run_no_wait(
                RebuildNodeInput(node_id=node_uuid, mode="incremental", scope="all"),
            )
            logger.info(
                "Dispatched rebuild for node '%s' (status=%s, delta=%d, total_facts=%d)",
                node_key,
                entry.get("enrichment_status", "?"),
                entry["delta"],
                entry["fact_count"],
            )
            return True
        except Exception:
            logger.debug(
                "Error dispatching rebuild for %s",
                node_key,
                exc_info=True,
            )
            return False

    for i in range(0, len(dispatch_entries), batch_size):
        batch = dispatch_entries[i : i + batch_size]
        results = await asyncio.gather(*(_dispatch_one(nk, nu, e) for nk, nu, e in batch))
        dispatched += sum(1 for r in results if r)

    logger.info("Dispatched %d fact-stale node tasks", dispatched)
    try:
        await ctx.aio_put_stream(
            json.dumps(
                {
                    "type": "auto_build_progress",
                    "step": "recalculate",
                    "nodes_recalculated": dispatched,
                }
            )
        )
    except Exception:
        pass

    return dispatched
