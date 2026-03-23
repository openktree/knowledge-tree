"""Unified node builder for concept, entity, event, method, and inquiry nodes.

Extracted from agents/tools/build_concept.py:build_node_unified().
Delegates to sub-pipelines for dimension generation, edge resolution,
and node creation.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from kt_agents_core.state import AgentContext
from kt_config.settings import get_settings
from kt_db.models import Fact
from kt_facts.pipeline import DecompositionPipeline
from kt_providers.search_and_fetch import search_and_store
from kt_worker_nodes.pipelines.building.base import NodeBuilder
from kt_worker_nodes.pipelines.building.helpers import (
    dedup_on_refresh,
    emit_budget,
    get_pool_hint,
)
from kt_worker_nodes.pipelines.definitions.pipeline import DefinitionPipeline
from kt_worker_nodes.pipelines.dimensions.pipeline import DimensionPipeline
from kt_worker_nodes.pipelines.edges.pipeline import EdgePipeline
from kt_worker_nodes.pipelines.nodes.enrichment import PoolEnricher
from kt_worker_orchestrator.agents.orchestrator_state import OrchestratorState

logger = logging.getLogger(__name__)

MAX_BATCH_SIZE = 10


class UnifiedNodeBuilder(NodeBuilder):
    """Builds concept, entity, event, method, and inquiry nodes.

    Handles dedup, stale refresh, enrichment, and creation with the same
    code path. Key differences between types:
    - Concepts match any existing node_type; others filter by their own type.
    - Only concepts support stale-node refresh (costs explore_budget).
    - Dimension mode varies per type ("neutral" for concepts, type name for others).
    """

    @property
    def builder_id(self) -> str:
        return "unified"

    async def build(
        self,
        name: str,
        node_type: str,
        ctx: AgentContext,
        state: OrchestratorState,
        entity_subtype: str | None = None,
    ) -> dict[str, Any]:
        """Full node build with dedup, refresh, enrichment.

        Args:
            name: Node name / concept label.
            node_type: One of "concept", "entity", "event".
            ctx: Agent context.
            state: Orchestrator state (mutated in-place).

        Returns:
            Dict with action, node_id, fact_count, suggested_concepts, etc.
        """
        is_concept = node_type == "concept"
        dim_mode = "neutral" if is_concept else node_type
        tool_label = f"build_{node_type}"

        await ctx.emit("activity_log", action=f"Building {node_type}: '{name}'", tool=tool_label)

        # ── Check for existing node ──────────────────────────────────
        # Use trigram similarity (ranked, exact match first) with node_type
        # filter in SQL so LIMIT isn't consumed by irrelevant types.
        type_filter = None if is_concept else node_type
        existing = await ctx.graph_engine.search_nodes_by_trigram(
            name,
            threshold=0.3,
            limit=5,
            node_type=type_filter,
        )

        if not existing and ctx.embedding_service:
            try:
                embedding = await ctx.embedding_service.embed_text(name)
                similar = await ctx.graph_engine.find_similar_nodes(
                    embedding,
                    threshold=0.25,
                    limit=5,
                    node_type=type_filter,
                )
                if similar:
                    existing = similar
            except Exception:
                logger.exception("Error in embedding dedup for '%s'", name)

        if existing:
            node = existing[0]

            # ── Concept-only: stale refresh logic ────────────────────
            if is_concept:
                is_stale = ctx.graph_engine.is_node_stale(node)

                if is_stale and state.explore_remaining > 0 and not getattr(state, "disable_external_search", False):
                    return await self._refresh(node, name, ctx, state, dim_mode, tool_label)

            # ── READ + ENRICH (all types) ────────────────────────────
            return await self._read_and_enrich(node, name, is_concept, ctx, state)

        # ── Node doesn't exist — try to assemble from fact pool ──────
        return await self._create(name, node_type, ctx, state, dim_mode, tool_label, entity_subtype=entity_subtype)

    async def _refresh(
        self,
        node: Any,
        name: str,
        ctx: AgentContext,
        state: OrchestratorState,
        dim_mode: str,
        tool_label: str,
    ) -> dict[str, Any]:
        """REFRESH stale concept — costs 1 explore_budget."""
        dim_pl = DimensionPipeline(ctx)
        def_pl = DefinitionPipeline(ctx)
        edge_pl = EdgePipeline(ctx)

        state.explore_used += 1
        await ctx.graph_engine.increment_access_count(node.id)
        await ctx.emit("activity_log", action=f"Refreshing stale node: {name}", tool=tool_label)
        await emit_budget(ctx, state)

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

        all_facts = await ctx.graph_engine.get_node_facts(node.id)
        await ctx.graph_engine.delete_dimensions(node.id)

        await dim_pl.generate_and_store(node, all_facts, mode=dim_mode)
        await def_pl.generate_definition(node.id, node.concept)
        await edge_pl.resolve_from_candidates(node)

        await ctx.graph_engine.increment_update_count(node.id)

        try:
            await dedup_on_refresh(node, ctx, state)
        except Exception:
            logger.debug("Error in dedup on refresh for '%s'", name, exc_info=True)

        if str(node.id) not in state.visited_nodes:
            state.visited_nodes.append(str(node.id))
            state.nav_used += 1
        state.exploration_path.append(name)

        await ctx.emit(
            "node_expanded",
            data={
                "id": str(node.id),
                "concept": node.concept,
                "node_type": node.node_type,
            },
        )

        suggested = await dim_pl.collect_suggested_concepts(node.id)
        return {
            "action": "refreshed",
            "node_id": str(node.id),
            "concept": node.concept,
            "node_type": node.node_type,
            "fact_count": len(all_facts),
            "new_facts_linked": 0,
            "is_stale": False,
            "was_refreshed": True,
            "suggested_concepts": suggested,
        }

    @staticmethod
    async def _read_and_enrich(
        node: Any,
        name: str,
        is_concept: bool,
        ctx: AgentContext,
        state: OrchestratorState,
    ) -> dict[str, Any]:
        """READ + ENRICH an existing node (free)."""
        dim_pl = DimensionPipeline(ctx)

        if str(node.id) not in state.visited_nodes:
            state.visited_nodes.append(str(node.id))
            state.nav_used += 1
        state.exploration_path.append(name)
        await ctx.graph_engine.increment_access_count(node.id)

        await ctx.emit(
            "node_visited",
            data={
                "id": str(node.id),
                "concept": node.concept,
                "node_type": node.node_type,
            },
        )

        enricher = PoolEnricher(ctx)
        enrich_result = await enricher.enrich(node)
        facts = await ctx.graph_engine.get_node_facts(node.id)
        suggested = await dim_pl.collect_suggested_concepts(node.id)

        return {
            "action": "enriched" if enrich_result.new_facts_linked > 0 else "read",
            "node_id": str(node.id),
            "concept": node.concept,
            "node_type": node.node_type,
            "fact_count": len(facts),
            "new_facts_linked": enrich_result.new_facts_linked,
            "is_stale": is_concept and ctx.graph_engine.is_node_stale(node),
            "was_refreshed": False,
            "suggested_concepts": suggested,
        }

    async def _create(
        self,
        name: str,
        node_type: str,
        ctx: AgentContext,
        state: OrchestratorState,
        dim_mode: str,
        tool_label: str,
        entity_subtype: str | None = None,
    ) -> dict[str, Any]:
        """Create node from fact pool, gathering externally if needed."""
        dim_pl = DimensionPipeline(ctx)
        def_pl = DefinitionPipeline(ctx)
        edge_pl = EdgePipeline(ctx)

        embedding = await ctx.embedding_service.embed_text(name) if ctx.embedding_service else None

        settings = get_settings()
        pool_limit = settings.dimension_fact_limit * settings.dimension_pool_multiplier
        pool_facts: list[Fact] = []
        if embedding:
            pool_facts = await ctx.graph_engine.search_fact_pool(
                embedding,
                limit=pool_limit,
                threshold=settings.fact_pool_threshold,
            )
        text_pool = await ctx.graph_engine.search_fact_pool_text(name, limit=pool_limit)

        seen_ids: set[uuid.UUID] = {f.id for f in pool_facts}
        for f in text_pool:
            if f.id not in seen_ids:
                seen_ids.add(f.id)
                pool_facts.append(f)

        if not pool_facts:
            if getattr(state, "disable_external_search", False):
                return {
                    "action": "skipped",
                    "reason": f"No facts in pool for '{name}' — decompose more sections first",
                }

            if state.explore_remaining <= 0:
                return {"action": "skipped", "reason": "explore budget exhausted, no facts in pool"}

            state.explore_used += 1
            await ctx.emit("activity_log", action=f"Gathering facts for new {node_type}: {name}", tool=tool_label)
            await emit_budget(ctx, state)

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
                    pool_facts = decomp_result.facts
                except Exception:
                    logger.exception("Error decomposing facts for new %s '%s'", node_type, name)

        if not pool_facts:
            return {"action": "skipped", "reason": "no facts available"}

        # CREATE node
        node = await ctx.graph_engine.create_node(
            concept=name,
            embedding=embedding,
            node_type=node_type,
            entity_subtype=entity_subtype,
        )

        for fact in pool_facts:
            await ctx.graph_engine.link_fact_to_node(node.id, fact.id)

        dim_result = await dim_pl.generate_and_store(node, pool_facts, mode=dim_mode)

        # Unlink pool-sourced facts that no dimension model found relevant
        await dim_pl.filter_irrelevant_facts(node.id, pool_facts, dim_result.relevant_fact_indices)

        # Synthesize definition from dimensions
        await def_pl.generate_definition(node.id, node.concept)

        # Resolve edges from pending candidates for this node
        edge_result = await edge_pl.resolve_from_candidates(node)

        state.created_nodes.append(str(node.id))
        state.visited_nodes.append(str(node.id))
        state.nav_used += 1
        state.exploration_path.append(name)

        await ctx.emit(
            "node_created",
            data={
                "id": str(node.id),
                "concept": node.concept,
                "node_type": node.node_type,
            },
        )
        await emit_budget(ctx, state)

        suggested = await dim_pl.collect_suggested_concepts(node.id)
        pool_hint = await get_pool_hint(ctx)

        return {
            "action": "created",
            "node_id": str(node.id),
            "concept": name,
            "node_type": node_type,
            "fact_count": len(pool_facts),
            "new_facts_linked": 0,
            "is_stale": False,
            "was_refreshed": False,
            "suggested_concepts": suggested,
            "pool_hint": pool_hint,
            "edges_created": edge_result.get("edges_created", 0),
        }
