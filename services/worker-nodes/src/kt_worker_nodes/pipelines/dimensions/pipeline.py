"""Dimension generation, storage, and curation pipeline.

Consolidates ALL dimension logic previously scattered across
``building/helpers.py``, ``pipeline.py`` phase 3, ``enrichment.py``,
and ``building/perspective.py``.

Supports fact batching: facts are grouped into batches of N (default 60),
each producing one dimension. Dimensions are marked "draft" until they
reach 70% capacity, then locked as "definitive".
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any

from sqlalchemy import text

from kt_agents_core.state import AgentContext
from kt_config.settings import get_settings
from kt_db.models import Dimension, Fact
from kt_models.dimensions import generate_dimensions
from kt_worker_nodes.pipelines.dimensions.types import DimensionResult
from kt_worker_nodes.pipelines.models import CreateNodeTask

logger = logging.getLogger(__name__)


class DimensionPipeline:
    """Generates, stores, and curates dimensions for nodes."""

    def __init__(self, ctx: AgentContext) -> None:
        self._ctx = ctx

    # ── Batching logic ────────────────────────────────────────────────

    @staticmethod
    def _batch_facts(
        facts: list[Fact],
        existing_dimensions: list[Dimension],
        fact_limit: int,
    ) -> list[tuple[int, list[Fact], Dimension | None]]:
        """Group facts into batches for dimension generation.

        1. Collects fact IDs consumed by definitive (locked) dimensions.
        2. Finds the first unsaturated draft dimension and fills it.
        3. Creates new batches from remaining facts.

        Concurrency safety is handled by the caller via advisory locks —
        this method assumes serial access.

        Returns:
            List of (batch_index, facts_for_batch, existing_dim_or_None).
            existing_dim_or_None is set when regenerating an unsaturated dim.
        """
        # Fact IDs already consumed by definitive dimensions
        consumed_ids: set[uuid.UUID] = set()
        for dim in existing_dimensions:
            if dim.is_definitive:
                for df in dim.dimension_facts:
                    consumed_ids.add(df.fact_id)

        # Available facts = not consumed by any definitive dimension
        available = [f for f in facts if f.id not in consumed_ids]

        batches: list[tuple[int, list[Fact], Dimension | None]] = []

        # Find first unsaturated draft dimension to fill
        unsaturated_dim: Dimension | None = None
        max_batch_idx = -1
        for dim in existing_dimensions:
            max_batch_idx = max(max_batch_idx, dim.batch_index)
            if not dim.is_definitive and unsaturated_dim is None:
                unsaturated_dim = dim

        if unsaturated_dim is not None and available:
            # Fill this dimension up to fact_limit
            batch_facts = available[:fact_limit]
            batches.append((unsaturated_dim.batch_index, batch_facts, unsaturated_dim))
            available = available[fact_limit:]

        # Create new batches from remaining available facts
        next_idx = max_batch_idx + 1
        while available:
            batch_facts = available[:fact_limit]
            # Only generate if there are facts to process
            if batch_facts:
                batches.append((next_idx, batch_facts, None))
                next_idx += 1
            available = available[fact_limit:]

        return batches

    # ── Main generation ───────────────────────────────────────────────

    async def generate_and_store(
        self,
        node: Any,
        facts: list[Fact],
        mode: str = "neutral",
    ) -> DimensionResult:
        """Generate dimensions via LLM with fact batching and saturation tracking.

        Reloads facts with source attribution so that dimension prompts
        include source names (e.g. "BBC News", "WHO").

        Does NOT resolve edges -- the caller orchestrates that separately.

        Args:
            node: The node to generate dimensions for.
            facts: Facts linked to the node.
            mode: Dimension mode -- "neutral" for concepts, or
                  "entity"/"event"/"credulous".

        Returns:
            DimensionResult with relevant_fact_indices and dim_results.
        """
        if not facts:
            return DimensionResult()

        ctx = self._ctx
        settings = get_settings()
        result = DimensionResult()
        # Pre-cache: accessing ORM attributes on a session in PendingRollbackError
        # state raises a second exception inside the except clause.
        node_concept = node.concept

        try:
            # Advisory lock keyed on node ID prevents concurrent dimension
            # generation for the same node (e.g. from parallel Hatchet tasks).
            # Released automatically on transaction commit/rollback.
            lock_key = node.id.int & 0x7FFFFFFF  # positive 32-bit int from UUID
            await ctx.graph_engine._write_session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": lock_key})

            # Reload with sources for attribution in dimension prompt
            facts_with_sources = await ctx.graph_engine.get_node_facts_with_sources(node.id)
            if facts_with_sources:
                facts = facts_with_sources

            # Load existing dimensions with their fact links
            existing_dims = await ctx.graph_engine.get_dimensions_with_facts(node.id)

            # Compute batches
            batches = self._batch_facts(
                facts,
                existing_dims,
                settings.dimension_fact_limit,
            )

            if not batches:
                return result

            model_ids = [ctx.model_gateway.dimension_model]
            kwargs: dict[str, Any] = {}
            if mode != "neutral":
                kwargs["mode"] = mode

            saturation_threshold = int(settings.dimension_fact_limit * settings.dimension_saturation_ratio)

            from kt_models.usage import clear_usage_task, set_usage_task

            for batch_index, batch_facts, existing_dim in batches:
                # Generate dimension for this batch
                set_usage_task("dimensions")
                dim_results = await generate_dimensions(node, batch_facts, model_ids, ctx.model_gateway, **kwargs)
                clear_usage_task()
                result.dim_results.extend(dim_results)

                # If regenerating unsaturated dim, delete old one first
                if existing_dim is not None:
                    await ctx.graph_engine.delete_dimension(existing_dim.id, write_key=existing_dim.write_key)

                for d in dim_results:
                    suggested = d.get("suggested_concepts")
                    suggested_list: list[str] | None = list(suggested) if isinstance(suggested, list) else None

                    # Collect relevant fact indices from this batch
                    relevant = d.get("relevant_facts")
                    batch_relevant_ids: list[uuid.UUID] = []
                    if isinstance(relevant, list):
                        for i in relevant:
                            if isinstance(i, int) and 1 <= i <= len(batch_facts):
                                result.relevant_fact_indices.add(i)
                                batch_relevant_ids.append(batch_facts[i - 1].id)

                    # If no relevant indices reported, assume all are relevant
                    if not batch_relevant_ids:
                        batch_relevant_ids = [f.id for f in batch_facts]

                    is_definitive = len(batch_facts) >= saturation_threshold

                    await ctx.graph_engine.add_dimension(
                        node.id,
                        str(d["model_id"]),
                        str(d["content"]),
                        float(d.get("confidence", 0.5)),  # type: ignore[arg-type]
                        suggested_list,
                        batch_index=batch_index,
                        fact_count=len(batch_facts),
                        is_definitive=is_definitive,
                        fact_ids=batch_relevant_ids,
                    )

        except Exception:
            logger.exception("Error generating dimensions for '%s'", node_concept)
            raise

        return result

    async def generate_batch(
        self,
        tasks: list[CreateNodeTask],
        concurrency: int | None = None,
    ) -> dict[str, Any]:
        """Parallel LLM generation + sequential store for a batch of nodes.

        Mutates each task's dim_results and unlinks irrelevant facts.

        Returns:
            Metrics dict with per-node dimension counts and facts unlinked.
        """
        ctx = self._ctx
        settings = get_settings()
        sem = asyncio.Semaphore(concurrency or settings.pipeline_concurrency)

        dim_tasks = [t for t in tasks if t.action in ("create", "refresh") and t.node is not None]
        if not dim_tasks:
            return {"node_count": 0, "total_dimensions": 0, "total_facts_unlinked": 0, "nodes": []}

        # Reload facts with sources (fast sequential DB reads)
        for t in dim_tasks:
            try:
                facts_with_sources = await ctx.graph_engine.get_node_facts_with_sources(t.node.id)
                if facts_with_sources:
                    t.pool_facts = facts_with_sources
            except Exception:
                logger.debug("Error reloading facts for '%s'", t.name, exc_info=True)

        saturation_threshold = int(settings.dimension_fact_limit * settings.dimension_saturation_ratio)

        # Parallel LLM dimension generation (batched per task)
        async def _gen_dims(t: CreateNodeTask) -> None:
            async with sem:
                try:
                    model_ids = [ctx.model_gateway.dimension_model]
                    kwargs: dict[str, Any] = {}
                    if t.dim_mode != "neutral":
                        kwargs["mode"] = t.dim_mode
                    if t.seed_context:
                        kwargs["attractor"] = t.seed_context

                    # For batch pipeline, new nodes have no existing dims
                    # Just generate one batch with up to fact_limit facts
                    from kt_models.usage import clear_usage_task, set_usage_task

                    batch_facts = t.pool_facts[: settings.dimension_fact_limit]
                    set_usage_task("dimensions")
                    t.dim_results = await generate_dimensions(
                        t.node,
                        batch_facts,
                        model_ids,
                        ctx.model_gateway,
                        **kwargs,
                    )
                    clear_usage_task()
                    # Tag batch metadata for storage phase
                    t._batch_facts_list = batch_facts  # type: ignore[attr-defined]
                    await ctx.emit("activity_log", action=f"Generated dimensions for '{t.name}'", tool="build_pipeline")
                except Exception:
                    logger.exception("Error generating dimensions for '%s'", t.name)
                    t.dim_results = []

        results = await asyncio.gather(*[_gen_dims(t) for t in dim_tasks], return_exceptions=True)
        for i, r in enumerate(results):
            if isinstance(r, BaseException):
                logger.error("Dimension generation failed for '%s': %s", dim_tasks[i].name, r)

        # Sequential store: write dimensions to DB + collect relevant fact indices
        total_dims = 0
        node_details: list[dict[str, Any]] = []
        for t in dim_tasks:
            all_relevant_indices: set[int] = set()
            batch_facts: list[Fact] = getattr(t, "_batch_facts_list", t.pool_facts)

            for d in t.dim_results:
                try:
                    suggested = d.get("suggested_concepts")
                    suggested_list: list[str] | None = list(suggested) if isinstance(suggested, list) else None

                    relevant = d.get("relevant_facts")
                    batch_relevant_ids: list[uuid.UUID] = []
                    if isinstance(relevant, list):
                        for i in relevant:
                            if isinstance(i, int) and 1 <= i <= len(batch_facts):
                                all_relevant_indices.add(i)
                                batch_relevant_ids.append(batch_facts[i - 1].id)

                    if not batch_relevant_ids:
                        batch_relevant_ids = [f.id for f in batch_facts]

                    is_definitive = len(batch_facts) >= saturation_threshold

                    await ctx.graph_engine.add_dimension(
                        t.node.id,
                        str(d["model_id"]),
                        str(d["content"]),
                        float(d.get("confidence", 0.5)),  # type: ignore[arg-type]
                        suggested_list,
                        batch_index=0,
                        fact_count=len(batch_facts),
                        is_definitive=is_definitive,
                        fact_ids=batch_relevant_ids,
                    )
                except Exception:
                    logger.debug("Error storing dimension for '%s'", t.name, exc_info=True)

            total_dims += len(t.dim_results)
            if len(node_details) < 10:
                node_details.append(
                    {
                        "name": t.name,
                        "dimensions": len(t.dim_results),
                    }
                )

        try:
            await ctx.graph_engine._write_session.commit()
        except Exception:
            logger.exception("Error committing dimension batch")
            try:
                await ctx.graph_engine._write_session.rollback()
            except Exception:
                logger.debug("Rollback failed after dimension batch commit error", exc_info=True)

        return {
            "node_count": len(dim_tasks),
            "total_dimensions": total_dims,
            "total_facts_unlinked": 0,
            "nodes": node_details,
        }

    async def generate_credulous(
        self,
        node: Any,
        facts: list[Fact],
    ) -> None:
        """Generate a credulous dimension for a perspective node.

        Extracted from ``building/perspective.py:_generate_credulous_dimension()``.
        """
        if not facts:
            return
        ctx = self._ctx
        try:
            facts_with_sources = await ctx.graph_engine.get_node_facts_with_sources(node.id)
            if facts_with_sources:
                facts = facts_with_sources
            model_ids = [ctx.model_gateway.dimension_model]
            dim_results = await generate_dimensions(
                node,
                facts,
                model_ids,
                ctx.model_gateway,
                mode="credulous",
            )
            for d in dim_results:
                await ctx.graph_engine.add_dimension(
                    node.id,
                    str(d["model_id"]),
                    str(d["content"]),
                    float(d.get("confidence", 0.5)),  # type: ignore[arg-type]
                    None,
                )
        except Exception:
            logger.exception("Error generating credulous dimension for '%s'", node.concept)

    async def collect_suggested_concepts(self, node_id: uuid.UUID) -> list[str]:
        """Collect and deduplicate suggested_concepts from a node's dimensions.

        Moved from ``building/helpers.py:collect_suggested_concepts()``.
        """
        dims = await self._ctx.graph_engine.get_dimensions(node_id)
        seen: set[str] = set()
        result: list[str] = []
        for dim in dims:
            for sc in dim.suggested_concepts or []:
                key = sc.lower().strip()
                if key and key not in seen:
                    seen.add(key)
                    result.append(sc)
        return result
