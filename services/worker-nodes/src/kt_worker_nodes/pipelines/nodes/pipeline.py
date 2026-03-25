"""Node CRUD pipeline: create, dedup, refresh, enrich.

Handles the first three phases of the batch pipeline (classify + gather,
enrich existing nodes, create nodes + link facts) and single-node operations.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from kt_agents_core.state import AgentContext, PipelineState
from kt_config.settings import get_settings
from kt_db.models import Fact
from kt_facts.pipeline import DecompositionPipeline
from kt_providers.search_and_fetch import search_and_store
from kt_worker_nodes.pipelines.building.helpers import (
    collect_suggested_concepts,
    dedup_on_refresh,
    get_pool_hint,
)
from kt_worker_nodes.pipelines.models import CreateNodeTask
from kt_worker_nodes.pipelines.nodes.enrichment import PoolEnricher

logger = logging.getLogger(__name__)


class NodeCreationPipeline:
    """Node CRUD operations for single and batch workflows."""

    def __init__(self, ctx: AgentContext) -> None:
        self._ctx = ctx
        self._enricher = PoolEnricher(ctx)

    # ── Batch operations (phases 1 & 2) ──────────────────────────────

    async def classify_and_gather_batch(
        self,
        tasks: list[CreateNodeTask],
        state: PipelineState,
    ) -> dict[str, Any]:
        """Batch embed all names, classify each task, gather facts if needed.

        Returns:
            Metrics dict with action breakdown, search count, per-node detail.
        """
        ctx = self._ctx
        active_tasks = [t for t in tasks if t.action != "skip"]
        if not active_tasks:
            return {"actions": {}, "external_searches": 0, "nodes": []}

        # Batch embed all names in a single API call
        if ctx.embedding_service:
            try:
                names = [t.name for t in active_tasks]
                embeddings = await ctx.embedding_service.embed_batch(names)
                for t, emb in zip(active_tasks, embeddings):
                    t.embedding = emb
            except Exception:
                logger.exception("Batch embedding failed, falling back to individual")
                for t in active_tasks:
                    try:
                        t.embedding = await ctx.embedding_service.embed_text(t.name)
                    except Exception:
                        logger.debug("Individual embed failed for '%s'", t.name, exc_info=True)

        # Sequential classify: dedup check + pool search (fast DB reads)
        # Track tasks already seen in this batch so we don't create duplicates
        # when the same entity/concept appears multiple times before any commit.
        batch_seen: dict[tuple[str, str], CreateNodeTask] = {}
        for t in active_tasks:
            key = (t.name.lower().strip(), t.node_type)
            earlier = batch_seen.get(key)
            if earlier is not None and earlier.action in ("create", "enrich", "refresh"):
                # Duplicate within the same batch — point to the same node
                t.action = "skip"
                t.result = {"action": "skipped", "reason": "duplicate in batch"}
                logger.debug("Intra-batch dedup: skipping '%s' (%s), already handled", t.name, t.node_type)
                continue
            await self._classify_task(t, state)
            if t.action != "skip":
                batch_seen[key] = t

        # Parallel external search + decompose for tasks needing it
        settings = get_settings()
        sem = asyncio.Semaphore(settings.pipeline_concurrency)
        search_tasks = [t for t in active_tasks if t.action == "create" and not t.pool_facts]

        async def _gather_for_task(t: CreateNodeTask) -> None:
            async with sem:
                try:
                    raw_sources = await search_and_store(t.name, ctx)
                    if raw_sources:
                        pipeline = DecompositionPipeline(ctx.model_gateway)
                        decomp_result = await pipeline.decompose(
                            raw_sources,
                            t.name,
                            ctx.session,
                            ctx.embedding_service,
                            query_context=state.query,
                            qdrant_client=ctx.qdrant_client,
                            write_session=ctx.graph_engine._write_session,
                        )
                        t.pool_facts = decomp_result.facts
                except Exception:
                    logger.exception("Error gathering facts for '%s'", t.name)

        if search_tasks:
            await asyncio.gather(*[_gather_for_task(t) for t in search_tasks])

        # Mark tasks that still have no facts as skipped
        for t in active_tasks:
            if t.action == "create" and not t.pool_facts:
                t.action = "skip"
                t.result = {"action": "skipped", "reason": "no facts available"}

        # Build metrics
        actions: dict[str, int] = {}
        for t in active_tasks:
            actions[t.action] = actions.get(t.action, 0) + 1
        node_details: list[dict[str, Any]] = []
        for t in active_tasks[:10]:
            node_details.append(
                {
                    "name": t.name,
                    "type": t.node_type,
                    "action": t.action,
                    "pool_facts": len(t.pool_facts),
                }
            )
        return {
            "actions": actions,
            "external_searches": sum(1 for t in active_tasks if t.explore_charged),
            "nodes": node_details,
        }

    async def _classify_task(
        self,
        task: CreateNodeTask,
        state: PipelineState,
    ) -> None:
        """Classify a single task: check for existing node, search fact pool."""
        ctx = self._ctx
        settings = get_settings()

        # Check if node already exists using trigram similarity (ranked by
        # closeness, so exact matches come first).  Push node_type filter into
        # SQL so LIMIT isn't consumed by irrelevant types.
        type_filter = None if task.is_concept else task.node_type
        existing = await ctx.graph_engine.search_nodes_by_trigram(
            task.name,
            threshold=0.3,
            limit=5,
            node_type=type_filter,
        )

        if not existing and task.embedding:
            try:
                similar = await ctx.graph_engine.find_similar_nodes(
                    task.embedding,
                    threshold=0.25,
                    limit=5,
                    node_type=type_filter,
                )
                if similar:
                    existing = similar
            except Exception:
                logger.debug("Embedding dedup failed for '%s'", task.name, exc_info=True)

        if existing:
            node = existing[0]
            task.existing_node = node

            # Concept-only: check if stale and eligible for refresh
            if task.is_concept and ctx.graph_engine.is_node_stale(node):
                if state.explore_remaining > 0 and not getattr(state, "disable_external_search", False):
                    task.action = "refresh"
                    state.explore_used += 1
                    task.explore_charged = True
                    return

            # Enrich existing node (free) — fast classification only;
            # actual enrichment work happens in enrich_batch().
            task.action = "enrich"
            if str(node.id) not in state.visited_nodes:
                state.visited_nodes.append(str(node.id))
                state.nav_used += 1
            state.exploration_path.append(task.name)
            await ctx.graph_engine.increment_access_count(node.id)

            await ctx.emit(
                "node_visited",
                data={
                    "id": str(node.id),
                    "concept": node.concept,
                    "node_type": node.node_type,
                },
            )

            # Merge any duplicates of this node (all types, not just concepts)
            try:
                await dedup_on_refresh(node, ctx, state)
            except Exception:
                logger.debug("Dedup on enrich failed for '%s'", task.name, exc_info=True)

            task.node = node
            return

        # Node doesn't exist -- search the fact pool
        task.action = "create"

        # Check if a matching seed exists with pre-accumulated facts
        write_session = getattr(ctx.graph_engine, "_write_session", None)
        if write_session is not None and task.seed_key:
            try:
                from kt_db.repositories.write_seeds import WriteSeedRepository

                seed_repo = WriteSeedRepository(write_session)
                seed = await seed_repo.get_seed_by_key(task.seed_key)

                if seed is not None and seed.status == "promoted" and seed.promoted_node_key:
                    # Seed already promoted → enrich the existing node
                    from kt_db.keys import key_to_uuid

                    node_uuid = key_to_uuid(seed.promoted_node_key)
                    existing = await ctx.graph_engine.get_node_by_id(node_uuid)
                    if existing:
                        task.action = "enrich"
                        task.existing_node = existing
                        task.node = existing
                        if str(existing.id) not in state.visited_nodes:
                            state.visited_nodes.append(str(existing.id))
                            state.nav_used += 1
                        state.exploration_path.append(task.name)
                        return

                if (
                    seed is not None
                    and seed.status in ("active", "ambiguous")
                    and seed.fact_count >= settings.seed_promotion_min_facts
                ):
                    # Load facts from seed (aggregate descendants for ambiguous)
                    if seed.status == "ambiguous":
                        seed_fact_ids = await seed_repo.get_all_descendant_facts(task.seed_key)
                        # Build seed context listing sub-seeds
                        routes = await seed_repo.get_routes_for_parent(task.seed_key)
                        if routes:
                            child_names = [r.label for r in routes]
                            task.seed_context = (
                                f"This concept encompasses multiple distinct senses: "
                                f"{', '.join(child_names)}. "
                                f"Facts below are aggregated from all senses."
                            )
                    else:
                        seed_fact_ids = await seed_repo.get_facts_for_seed(task.seed_key)
                        # Pass aliases so the LLM knows about alternate names
                        meta = seed.metadata_ or {}
                        aliases = meta.get("aliases", [])
                        if aliases:
                            task.seed_context = f"Also known as: {', '.join(aliases)}."
                    if seed_fact_ids:
                        seed_facts = await ctx.graph_engine.get_facts_by_ids(seed_fact_ids)
                        if seed_facts:
                            task.pool_facts = seed_facts
                            logger.info(
                                "Seed '%s' (%s) has %d facts — promoting without external search",
                                task.name,
                                seed.status,
                                len(seed_facts),
                            )
                            return
            except Exception:
                logger.debug("Seed lookup failed for '%s'", task.name, exc_info=True)

        # No seed found — seeds are the sole source of facts.
        # Fall through to external search or skip.
        if getattr(state, "disable_external_search", False):
            task.action = "skip"
            task.result = {
                "action": "skipped",
                "reason": f"No facts in pool for '{task.name}' — decompose more sections first",
            }
        elif state.explore_remaining <= 0:
            task.action = "skip"
            task.result = {"action": "skipped", "reason": "explore budget exhausted, no facts in pool"}
        else:
            # Will need external search -- pre-allocate budget
            state.explore_used += 1
            task.explore_charged = True
            await ctx.emit(
                "activity_log",
                action=f"Gathering facts for new {task.node_type}: {task.name}",
                tool="build_pipeline",
            )

    async def enrich_batch(
        self,
        tasks: list[CreateNodeTask],
        state: PipelineState,
    ) -> dict[str, Any]:
        """Run enrichment on tasks classified as 'enrich'.

        This is the slow part (pool search, fact linking, possible dimension
        regen) that was previously inlined in ``_classify_task``.  Splitting it
        into its own phase gives visibility into enrichment time.

        Returns:
            Metrics dict with enrichment counts and per-node detail.
        """
        ctx = self._ctx
        enrich_tasks = [t for t in tasks if t.action == "enrich" and t.existing_node is not None and not t.result]
        if not enrich_tasks:
            return {"node_count": 0, "total_new_facts_linked": 0, "dimensions_regenerated": 0, "nodes": []}

        enriched_count = 0
        total_new_facts = 0
        dims_regen_count = 0
        node_details: list[dict[str, Any]] = []
        for t in enrich_tasks:
            node = t.existing_node
            try:
                enrich_result = await self._enricher.enrich(node)
                facts = await ctx.graph_engine.get_node_facts(node.id)
                suggested = await collect_suggested_concepts(node.id, ctx)

                new_linked = enrich_result.new_facts_linked
                dims_regen = getattr(enrich_result, "dimensions_regenerated", False)
                t.result = {
                    "action": "enriched" if new_linked > 0 else "read",
                    "node_id": str(node.id),
                    "concept": node.concept,
                    "node_type": node.node_type,
                    "fact_count": len(facts),
                    "new_facts_linked": new_linked,
                    "is_stale": t.is_concept and ctx.graph_engine.is_node_stale(node),
                    "was_refreshed": False,
                    "suggested_concepts": suggested,
                }
                enriched_count += 1
                total_new_facts += new_linked
                if dims_regen:
                    dims_regen_count += 1
                if len(node_details) < 10:
                    node_details.append(
                        {
                            "name": t.name,
                            "new_facts_linked": new_linked,
                            "dimensions_regenerated": bool(dims_regen),
                        }
                    )
            except Exception:
                logger.exception("Error enriching node '%s'", t.name)
                # Fall back to a read result so the task isn't left without a result
                t.result = {
                    "action": "read",
                    "node_id": str(node.id),
                    "concept": node.concept,
                    "node_type": node.node_type,
                    "fact_count": 0,
                    "new_facts_linked": 0,
                    "is_stale": False,
                    "was_refreshed": False,
                    "suggested_concepts": [],
                }

        return {
            "node_count": enriched_count,
            "total_new_facts_linked": total_new_facts,
            "dimensions_regenerated": dims_regen_count,
            "nodes": node_details,
        }

    async def create_batch(
        self,
        tasks: list[CreateNodeTask],
        state: PipelineState,
    ) -> dict[str, Any]:
        """Create nodes in the DB and link facts. Then commit so all nodes exist.

        Returns:
            Metrics dict with created/refreshed counts and per-node detail.
        """
        ctx = self._ctx

        for t in tasks:
            if t.action == "skip" or t.result:
                continue

            try:
                if t.action == "refresh":
                    await self._handle_refresh(t, state)
                elif t.action == "create":
                    await self._handle_create(t, state)
            except Exception:
                logger.exception("Error in create_batch for '%s'", t.name)
                t.action = "error"
                t.result = {"action": "error", "name": t.name, "reason": "internal error"}

        # Commit all nodes so they're visible to each other in later phases
        try:
            await ctx.graph_engine._write_session.commit()
        except Exception:
            logger.exception("Error committing create_batch")
            try:
                await ctx.graph_engine._write_session.rollback()
            except Exception:
                logger.debug("Rollback failed after create_batch commit error", exc_info=True)
            return {"created": 0, "refreshed": 0, "nodes": []}

        # Post-commit dedup: now that nodes are visible, merge any cross-session
        # or cross-batch duplicates (e.g. two sub-explorers created the same entity).
        # prefer_existing=True: the newly created node is absorbed into any pre-existing
        # equivalent, so subsequent pipeline steps (generate_dimensions) operate on the
        # surviving node and never encounter a FK violation on a deleted node.
        created = [t for t in tasks if t.action == "create" and t.node is not None and t.node.embedding is not None]
        for t in created:
            try:
                survivor = await dedup_on_refresh(t.node, ctx, state, prefer_existing=True)
                if survivor is not None and survivor.id != t.node.id:
                    # t.node was absorbed into an existing node — redirect to survivor
                    logger.info(
                        "create_batch: node '%s' absorbed into existing '%s' (%s)",
                        t.name,
                        survivor.concept,
                        survivor.id,
                    )
                    t.node = survivor
            except Exception:
                logger.debug("Post-create dedup failed for '%s'", t.name, exc_info=True)

        # Build metrics
        created_count = sum(1 for t in tasks if t.action == "create" and t.node is not None)
        refreshed_count = sum(1 for t in tasks if t.action == "refresh" and t.node is not None)
        node_details: list[dict[str, Any]] = []
        for t in tasks:
            if t.node is not None and t.action in ("create", "refresh") and len(node_details) < 10:
                node_details.append(
                    {
                        "name": t.name,
                        "type": t.node_type,
                        "action": t.action,
                        "fact_count": len(t.pool_facts),
                    }
                )
        return {"created": created_count, "refreshed": refreshed_count, "nodes": node_details}

    async def _handle_refresh(
        self,
        t: CreateNodeTask,
        state: PipelineState,
    ) -> None:
        """Handle stale concept refresh."""
        ctx = self._ctx
        node = t.existing_node
        await ctx.graph_engine.increment_access_count(node.id)
        await ctx.emit("activity_log", action=f"Refreshing stale node: {t.name}", tool="build_pipeline")

        raw_sources = await search_and_store(t.name, ctx)
        if raw_sources:
            try:
                pipeline = DecompositionPipeline(ctx.model_gateway)
                decomp_result = await pipeline.decompose(
                    raw_sources,
                    t.name,
                    ctx.session,
                    ctx.embedding_service,
                    query_context=state.query,
                    qdrant_client=ctx.qdrant_client,
                    write_session=ctx.graph_engine._write_session,
                )
                for fact in decomp_result.facts:
                    await ctx.graph_engine.link_fact_to_node(node.id, fact.id)
            except Exception:
                logger.exception("Error decomposing facts for refresh of '%s'", t.name)

        # Refresh seed metadata (aliases, merges, ambiguity) and link
        # accumulated seed facts — seeds may have grown since node creation.
        await self._refresh_seed_metadata(t)

        t.node = node
        t.pool_facts = await ctx.graph_engine.get_node_facts(node.id)

        # Delete old dimensions -- later phases will regenerate
        await ctx.graph_engine.delete_dimensions(node.id)
        await ctx.graph_engine.increment_update_count(node.id)

        try:
            await dedup_on_refresh(node, ctx, state)
        except Exception:
            logger.debug("Error in dedup on refresh for '%s'", t.name, exc_info=True)

        if str(node.id) not in state.visited_nodes:
            state.visited_nodes.append(str(node.id))
            state.nav_used += 1
        state.exploration_path.append(t.name)

        await ctx.emit(
            "node_expanded",
            data={
                "id": str(node.id),
                "concept": node.concept,
                "node_type": node.node_type,
            },
        )

    async def _refresh_seed_metadata(self, t: CreateNodeTask) -> None:
        """Re-read seed aliases, merges, ambiguity info and link accumulated seed facts.

        Seeds accumulate knowledge between node refreshes — new merges, aliases,
        and disambiguation routes may have appeared.  For ambiguous seeds the
        facts come from sub-seeds, so we pull descendant facts and link them.
        """
        ctx = self._ctx
        node = t.existing_node
        write_session = getattr(ctx.graph_engine, "_write_session", None)
        if write_session is None or not t.seed_key:
            return

        try:
            from kt_db.repositories.write_seeds import WriteSeedRepository

            seed_repo = WriteSeedRepository(write_session)
            seed = await seed_repo.get_seed_by_key(t.seed_key)
            if seed is None:
                return

            extra_meta: dict[str, object] = {}

            # ── Aliases from seed metadata ───────────────────────────
            seed_meta = seed.metadata_ or {}
            aliases = seed_meta.get("aliases", [])
            if aliases:
                extra_meta["aliases"] = aliases

            # ── Names of seeds merged into this one ──────────────────
            merges = await seed_repo.get_merges_for_seed(t.seed_key)
            merged_names: list[str] = []
            for m in merges:
                if m.operation == "merge" and m.target_seed_key == t.seed_key:
                    src = await seed_repo.get_seed_by_key(m.source_seed_key)
                    if src and src.name.lower() != t.name.lower():
                        merged_names.append(src.name)
            if merged_names:
                extra_meta["merged_from"] = merged_names

            # ── Seed ambiguity / disambiguation info ─────────────────
            if seed.status == "ambiguous":
                # Ambiguous parent: aggregate facts from all sub-seeds
                seed_fact_ids = await seed_repo.get_all_descendant_facts(t.seed_key)
                if seed_fact_ids:
                    seed_facts = await ctx.graph_engine.get_facts_by_ids(seed_fact_ids)
                    for fact in seed_facts:
                        await ctx.graph_engine.link_fact_to_node(node.id, fact.id)
                    logger.info(
                        "Refresh: linked %d descendant seed facts for ambiguous '%s'",
                        len(seed_facts),
                        t.name,
                    )
                routes = await seed_repo.get_routes_for_parent(t.seed_key)
                if routes:
                    child_names = [r.label for r in routes]
                    t.seed_context = (
                        f"This concept encompasses multiple distinct senses: "
                        f"{', '.join(child_names)}. "
                        f"Facts below are aggregated from all senses."
                    )
                    extra_meta["seed_ambiguity"] = {
                        "is_disambiguated": False,
                        "ambiguity_type": "parent",
                        "child_names": child_names,
                    }
            else:
                # Normal or child seed: link direct seed facts
                seed_fact_ids = await seed_repo.get_facts_for_seed(t.seed_key)
                if seed_fact_ids:
                    seed_facts = await ctx.graph_engine.get_facts_by_ids(seed_fact_ids)
                    for fact in seed_facts:
                        await ctx.graph_engine.link_fact_to_node(node.id, fact.id)
                    logger.info(
                        "Refresh: linked %d seed facts for '%s'",
                        len(seed_facts),
                        t.name,
                    )

                # Set alias-based seed context for dimension generation
                if aliases:
                    t.seed_context = f"Also known as: {', '.join(aliases)}."

                # Check if this seed is a disambiguated child
                route = await seed_repo.get_route_for_child(t.seed_key)
                if route:
                    parent_seed = await seed_repo.get_seed_by_key(route.parent_seed_key)
                    sibling_routes = await seed_repo.get_routes_for_parent(route.parent_seed_key)
                    sibling_names = []
                    for r in sibling_routes:
                        if r.child_seed_key != t.seed_key:
                            sib = await seed_repo.get_seed_by_key(r.child_seed_key)
                            sibling_names.append(sib.name if sib else r.label)
                    extra_meta["seed_ambiguity"] = {
                        "is_disambiguated": True,
                        "ambiguity_type": route.ambiguity_type,
                        "parent_name": parent_seed.name if parent_seed else None,
                        "sibling_names": sibling_names,
                    }

            if extra_meta:
                await ctx.graph_engine.update_node(
                    node.id,
                    metadata_={
                        **(node.metadata_ or {}),
                        **extra_meta,
                    },
                )
        except Exception:
            logger.debug("Seed metadata refresh failed for '%s'", t.name, exc_info=True)

    async def _handle_create(
        self,
        t: CreateNodeTask,
        state: PipelineState,
    ) -> None:
        """Create a new node and link facts."""
        ctx = self._ctx

        if not t.pool_facts:
            t.action = "skip"
            t.result = {"action": "skipped", "reason": "no facts available"}
            return

        node = await ctx.graph_engine.create_node(
            concept=t.name,
            embedding=t.embedding,
            node_type=t.node_type,
            entity_subtype=t.entity_subtype,
        )

        for fact in t.pool_facts:
            await ctx.graph_engine.link_fact_to_node(node.id, fact.id)

        # Promote seed and store ambiguity metadata (seed_key is always set)
        write_session = getattr(ctx.graph_engine, "_write_session", None)
        if write_session is not None and t.seed_key:
            try:
                from kt_db.repositories.write_seeds import WriteSeedRepository

                seed_repo = WriteSeedRepository(write_session)
                await seed_repo.promote_seed(t.seed_key, t.seed_key)

                extra_meta: dict[str, object] = {}

                # Collect aliases from seed metadata
                seed = await seed_repo.get_seed_by_key(t.seed_key)
                if seed:
                    seed_meta = seed.metadata_ or {}
                    aliases = seed_meta.get("aliases", [])
                    if aliases:
                        extra_meta["aliases"] = aliases

                    # Collect names of seeds that were merged into this one
                    merges = await seed_repo.get_merges_for_seed(t.seed_key)
                    merged_names: list[str] = []
                    for m in merges:
                        if m.operation == "merge" and m.target_seed_key == t.seed_key:
                            src = await seed_repo.get_seed_by_key(m.source_seed_key)
                            if src and src.name.lower() != t.name.lower():
                                merged_names.append(src.name)
                    if merged_names:
                        extra_meta["merged_from"] = merged_names

                # Store seed ambiguity info in node metadata for frontend display
                route = await seed_repo.get_route_for_child(t.seed_key)
                if route:
                    parent_seed = await seed_repo.get_seed_by_key(route.parent_seed_key)
                    sibling_routes = await seed_repo.get_routes_for_parent(route.parent_seed_key)
                    sibling_names = []
                    for r in sibling_routes:
                        if r.child_seed_key != t.seed_key:
                            sib = await seed_repo.get_seed_by_key(r.child_seed_key)
                            sibling_names.append(sib.name if sib else r.label)
                    extra_meta["seed_ambiguity"] = {
                        "is_disambiguated": True,
                        "ambiguity_type": route.ambiguity_type,
                        "parent_name": parent_seed.name if parent_seed else None,
                        "sibling_names": sibling_names,
                    }

                if extra_meta:
                    await ctx.graph_engine.update_node(
                        node.id,
                        metadata_={
                            **(node.metadata_ or {}),
                            **extra_meta,
                        },
                    )
            except Exception:
                logger.debug("Seed promotion failed for '%s'", t.name, exc_info=True)

        t.node = node

        state.created_nodes.append(str(node.id))
        state.visited_nodes.append(str(node.id))
        state.nav_used += 1
        state.exploration_path.append(t.name)

        await ctx.emit(
            "node_created",
            data={
                "id": str(node.id),
                "concept": node.concept,
                "node_type": node.node_type,
            },
        )

    # ── Single-node operations ───────────────────────────────────────

    async def create_single(
        self,
        name: str,
        node_type: str,
        facts: list[Fact],
        embedding: list[float] | None,
    ) -> Any:
        """Create one node and link facts. Returns node object."""
        ctx = self._ctx
        node = await ctx.graph_engine.create_node(
            concept=name,
            embedding=embedding,
            node_type=node_type,
        )
        for fact in facts:
            await ctx.graph_engine.link_fact_to_node(node.id, fact.id)
        return node

    async def refresh_node(
        self,
        node: Any,
        name: str,
        state: PipelineState,
    ) -> None:
        """Refresh a stale node: search, decompose, relink, delete old dims.

        Does NOT regenerate dimensions -- the caller handles that.
        """
        ctx = self._ctx
        raw_sources = await search_and_store(name, ctx)
        if raw_sources:
            try:
                pipeline = DecompositionPipeline(ctx.model_gateway)
                decomp_result = await pipeline.decompose(
                    raw_sources,
                    name,
                    ctx.session,
                    ctx.embedding_service,
                    query_context=state.query,
                    qdrant_client=ctx.qdrant_client,
                    write_session=ctx.graph_engine._write_session,
                )
                for fact in decomp_result.facts:
                    await ctx.graph_engine.link_fact_to_node(node.id, fact.id)
            except Exception:
                logger.exception("Error decomposing facts for refresh of '%s'", name)

        await ctx.graph_engine.delete_dimensions(node.id)
        await ctx.graph_engine.increment_update_count(node.id)

    async def enrich(self, node: Any) -> Any:
        """Enrich existing node from pool. Delegates to PoolEnricher."""
        return await self._enricher.enrich(node)

    async def dedup_on_refresh(
        self,
        node: Any,
        state: PipelineState,
    ) -> None:
        """Merge near-duplicate nodes after refresh."""
        await dedup_on_refresh(node, self._ctx, state)

    async def build_result_for_create(
        self,
        task: CreateNodeTask,
    ) -> dict[str, Any]:
        """Build the result dict for a created node."""
        suggested = await collect_suggested_concepts(task.node.id, self._ctx)
        pool_hint = await get_pool_hint(self._ctx)
        return {
            "action": "created",
            "node_id": str(task.node.id),
            "concept": task.name,
            "node_type": task.node_type,
            "fact_count": len(task.pool_facts),
            "new_facts_linked": 0,
            "is_stale": False,
            "was_refreshed": False,
            "suggested_concepts": suggested,
            "pool_hint": pool_hint,
            "edges_created": task.edges_created,
        }

    async def build_result_for_refresh(
        self,
        task: CreateNodeTask,
    ) -> dict[str, Any]:
        """Build the result dict for a refreshed node."""
        suggested = await collect_suggested_concepts(task.node.id, self._ctx)
        return {
            "action": "refreshed",
            "node_id": str(task.node.id),
            "concept": task.node.concept,
            "node_type": task.node.node_type,
            "fact_count": len(task.pool_facts),
            "new_facts_linked": 0,
            "is_stale": False,
            "was_refreshed": True,
            "suggested_concepts": suggested,
            "edges_created": task.edges_created,
        }
