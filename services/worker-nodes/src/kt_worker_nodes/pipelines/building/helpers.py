"""Shared helper functions for node builders.

Slimmed to utilities (``emit_budget``, ``get_pool_hint``) plus backwards-compatible
shims that delegate to the new sub-pipeline classes.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from kt_agents_core.state import AgentContext, PipelineState
from kt_config.settings import get_settings
from kt_db.models import Fact

logger = logging.getLogger(__name__)


# ── Utilities ────────────────────────────────────────────────────────


async def emit_budget(ctx: AgentContext, state: PipelineState) -> None:
    """Emit a budget update event."""
    data: dict[str, object] = {
        "nav_remaining": max(0, state.nav_budget - state.nav_used),
        "nav_total": state.nav_budget,
        "explore_remaining": state.explore_remaining,
        "explore_total": state.explore_budget,
    }
    scope = getattr(state, "scope", None)
    if scope is not None:
        data["scope"] = scope
    await ctx.emit("budget_update", data=data)


async def get_pool_hint(ctx: AgentContext) -> str:
    """Return a hint about remaining facts in the pool to guide the agent."""
    try:
        remaining = await ctx.graph_engine.search_fact_pool_text("", limit=1)
        if remaining:
            return "The fact pool still has unlinked facts. Keep building concepts and entities."
        return "The fact pool appears exhausted."
    except Exception:
        return ""


# ── Backwards-compatible shims ───────────────────────────────────────


async def generate_and_store_dimensions(
    node: Any,
    facts: list[Fact],
    ctx: AgentContext,
    mode: str = "neutral",
) -> tuple[int, set[int]]:
    """Generate dimensions and store them, then resolve edges.

    Preserves the original tuple return API ``(edges_created, relevant_fact_indices)``.
    Delegates to ``DimensionPipeline`` for dimension logic and ``EdgeResolver``
    for edge resolution (matching original behavior).
    """
    from kt_worker_nodes.pipelines.dimensions.pipeline import DimensionPipeline

    dim_pipeline = DimensionPipeline(ctx)
    result = await dim_pipeline.generate_and_store(node, facts, mode=mode)

    # Original behavior: resolve edges after storing dimensions
    edges_created = 0
    try:
        from kt_worker_nodes.pipelines.edges.resolver import EdgeResolver

        resolver = EdgeResolver(ctx)
        edge_result = await resolver.resolve(node)
        edges_created = edge_result.get("edges_created", 0)
    except Exception:
        logger.exception("Error resolving edges for '%s'", node.concept)

    return edges_created, result.relevant_fact_indices


async def collect_suggested_concepts(node_id: uuid.UUID, ctx: AgentContext) -> list[str]:
    """Collect and deduplicate suggested_concepts from a node's dimensions.

    Delegates to ``DimensionPipeline.collect_suggested_concepts()``.
    """
    from kt_worker_nodes.pipelines.dimensions.pipeline import DimensionPipeline

    return await DimensionPipeline(ctx).collect_suggested_concepts(node_id)


async def dedup_on_refresh(
    node: Any,
    ctx: AgentContext,
    state: PipelineState,
    *,
    prefer_existing: bool = False,
) -> Any | None:
    """Merge near-duplicate nodes.

    Uses a conservative embedding threshold (relation_dedup_threshold, default 0.15)
    to only merge nodes that are virtually identical. Only merges nodes of the same
    node_type. This is tighter than the creation-time dedup (0.25) since merging is
    destructive.

    Args:
        prefer_existing: When True, merge ``node`` INTO the found candidate
            (candidate survives, ``node`` is absorbed). Use for freshly created
            nodes where a pre-existing equivalent already holds richer data.
            Returns the surviving candidate so callers can redirect references.
            When False (default), merge the candidate INTO ``node`` (``node``
            survives). Continues checking all candidates.

    Returns:
        The surviving node if a merge occurred with prefer_existing=True,
        ``node`` itself if merges occurred with prefer_existing=False,
        or ``None`` if no duplicates were found.
    """
    if node.embedding is None:
        return None

    settings = get_settings()
    node_concept = node.concept  # cache before any DB op that might expire attributes
    try:
        similar = await ctx.graph_engine.find_similar_nodes(
            node.embedding,
            threshold=settings.relation_dedup_threshold,
            limit=10,
        )
    except Exception:
        logger.debug("Error finding similar nodes for dedup of '%s'", node_concept, exc_info=True)
        return None

    merged_any = False

    for candidate in similar:
        if candidate.id == node.id:
            continue
        if candidate.node_type != node.node_type:
            continue

        try:
            if prefer_existing:
                # Keep the pre-existing candidate; absorb the newly created node into it.
                # This ensures subsequent pipeline steps (generate_dimensions) work on
                # a node that will not be deleted by a concurrent dedup.
                keep_id, absorb_id = candidate.id, node.id
                keep_concept, absorbed_concept = candidate.concept, node_concept
            else:
                # Keep the current node; absorb the candidate into it.
                keep_id, absorb_id = node.id, candidate.id
                keep_concept, absorbed_concept = node_concept, candidate.concept

            await ctx.graph_engine.merge_nodes(keep_id, absorb_id)
            absorbed_id_str = str(absorb_id)

            # Update state references: redirect absorbed ID → survivor ID
            survivor_id_str = str(keep_id)
            if absorbed_id_str in state.created_nodes:
                state.created_nodes.remove(absorbed_id_str)
                if survivor_id_str not in state.created_nodes:
                    state.created_nodes.append(survivor_id_str)
            if absorbed_id_str in state.visited_nodes:
                state.visited_nodes.remove(absorbed_id_str)
                if survivor_id_str not in state.visited_nodes:
                    state.visited_nodes.append(survivor_id_str)

            logger.info(
                "Dedup merged '%s' (%s) into '%s' (%s)",
                absorbed_concept,
                absorbed_id_str,
                keep_concept,
                survivor_id_str,
            )
            await ctx.emit(
                "activity_log",
                action=f"Merged duplicate '{absorbed_concept}' into '{keep_concept}'",
                tool="build_concept",
            )

            if prefer_existing:
                # node has been absorbed — stop immediately and return the survivor
                return candidate

            merged_any = True

        except Exception:
            logger.debug(
                "Error merging '%s' into '%s'",
                candidate.concept,
                node_concept,
                exc_info=True,
            )

    return node if merged_any else None
