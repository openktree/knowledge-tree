"""Phased build pipeline for batch node creation.

Thin 6-phase orchestrator delegating to sub-pipelines:
  1.   Classify + Gather Facts     -> NodeCreationPipeline
  1.5  Enrich Existing Nodes       -> NodeCreationPipeline
  2.   Create Nodes + Link Facts   -> NodeCreationPipeline
  3.   Generate Dimensions         -> DimensionPipeline
  3.5. Synthesize Definitions      -> DefinitionPipeline
  4.   Resolve Edges (candidates)  -> EdgePipeline
  5.   Select Parents              -> ParentSelectionPipeline
"""

from __future__ import annotations

import logging
from typing import Any

from kt_agents_core.state import AgentContext, PipelineState
from kt_worker_nodes.pipelines.definitions.pipeline import DefinitionPipeline
from kt_worker_nodes.pipelines.dimensions.pipeline import DimensionPipeline
from kt_worker_nodes.pipelines.edges.pipeline import EdgePipeline
from kt_worker_nodes.pipelines.nodes.pipeline import NodeCreationPipeline
from kt_worker_nodes.pipelines.nodes.types import CreateNodeTask
from kt_worker_nodes.pipelines.parent.pipeline import ParentSelectionPipeline

logger = logging.getLogger(__name__)

MAX_BATCH_SIZE = 10


class BatchPipeline:
    """Thin 5-phase orchestrator delegating to sub-pipelines."""

    def __init__(self, ctx: AgentContext) -> None:
        self._ctx = ctx
        self._node_pipeline = NodeCreationPipeline(ctx)
        self._dim_pipeline = DimensionPipeline(ctx)
        self._def_pipeline = DefinitionPipeline(ctx)
        self._edge_pipeline = EdgePipeline(ctx)
        self._parent_pipeline = ParentSelectionPipeline(ctx)

    async def build_batch(
        self,
        entries: list[dict[str, str]],
        state: PipelineState,
        scope_name: str = "",
    ) -> dict[str, Any]:
        """Run the phased build pipeline for a batch of nodes.

        Args:
            entries: List of dicts with "name" and "node_type" keys.
            state: Orchestrator state (mutated in-place).
            scope_name: Optional scope name for emitting scope_phase events.

        Returns:
            Dict with results list and summary.
        """
        capped = entries[:MAX_BATCH_SIZE]
        tasks = self._parse_entries(capped)
        active = sum(1 for t in tasks if t.action != "skip")
        tracker = self._ctx.pipeline_tracker

        # Phase 1: Classify + Gather Facts
        await self._ctx.emit(
            "activity_log", action=f"Pipeline: classifying & gathering facts for {active} nodes", tool="build_pipeline"
        )
        if scope_name:
            await self._ctx.emit("scope_phase", data={"scope": scope_name, "phase": "classifying"})
        if tracker and scope_name:
            await tracker.start_phase(scope_name, "gathering")
        gathering_metrics = await self._node_pipeline.classify_and_gather_batch(tasks, state)

        # Phase 1.5: Enrich Nodes (pool search, fact linking, dimension regen)
        enrich_count = sum(1 for t in tasks if t.action == "enrich" and not t.result)
        if enrich_count > 0:
            await self._ctx.emit(
                "activity_log", action=f"Pipeline: enriching {enrich_count} existing nodes", tool="build_pipeline"
            )
            if scope_name:
                await self._ctx.emit("scope_phase", data={"scope": scope_name, "phase": "enriching"})
            if tracker and scope_name:
                await tracker.start_phase(scope_name, "enriching")
            enrich_metrics = await self._node_pipeline.enrich_batch(tasks, state)
            if tracker and scope_name:
                await tracker.end_phase(scope_name, "enriching")
                await tracker.log_phase_outcome(
                    scope_name,
                    "enriching",
                    node_count=enrich_metrics.get("node_count", 0),
                    metrics=enrich_metrics,
                )

        # Phase 2: Create Nodes + Link Facts
        await self._ctx.emit("activity_log", action="Pipeline: creating nodes & linking facts", tool="build_pipeline")
        if scope_name:
            await self._ctx.emit("scope_phase", data={"scope": scope_name, "phase": "creating"})
        if tracker and scope_name:
            gathered_fact_count = sum(len(t.pool_facts) for t in tasks)
            await tracker.end_phase(scope_name, "gathering")
            await tracker.log_phase_outcome(
                scope_name,
                "gathering",
                fact_count=gathered_fact_count,
                metrics=gathering_metrics,
            )
            await tracker.start_phase(scope_name, "building")
        building_metrics = await self._node_pipeline.create_batch(tasks, state)

        # Phase 3: Generate Dimensions (parallel LLM)
        await self._ctx.emit("activity_log", action="Pipeline: generating dimensions", tool="build_pipeline")
        if scope_name:
            await self._ctx.emit("scope_phase", data={"scope": scope_name, "phase": "dimensions"})
        if tracker and scope_name:
            created_node_count = sum(1 for t in tasks if t.node is not None and t.action == "create")
            await tracker.end_phase(scope_name, "building")
            await tracker.log_phase_outcome(
                scope_name,
                "building",
                node_count=created_node_count,
                metrics=building_metrics,
            )
            await tracker.start_phase(scope_name, "dimensions")
        dim_metrics = await self._dim_pipeline.generate_batch(tasks)

        # Phase 3.5: Synthesize Definitions
        await self._ctx.emit("activity_log", action="Pipeline: synthesizing definitions", tool="build_pipeline")
        if scope_name:
            await self._ctx.emit("scope_phase", data={"scope": scope_name, "phase": "definitions"})
        if tracker and scope_name:
            await tracker.end_phase(scope_name, "dimensions")
            await tracker.log_phase_outcome(scope_name, "dimensions", metrics=dim_metrics)
            await tracker.start_phase(scope_name, "definitions")
        def_metrics = await self._def_pipeline.generate_batch(tasks)

        # Phase 4: Resolve Edges from candidates
        await self._ctx.emit("activity_log", action="Pipeline: resolving edges from candidates", tool="build_pipeline")
        if scope_name:
            await self._ctx.emit("scope_phase", data={"scope": scope_name, "phase": "edges"})
        if tracker and scope_name:
            await tracker.end_phase(scope_name, "definitions")
            await tracker.log_phase_outcome(scope_name, "definitions", metrics=def_metrics)
            await tracker.start_phase(scope_name, "edges")
        edges_before = sum(t.edges_created for t in tasks)
        edge_metrics = await self._edge_pipeline.resolve_from_candidates_batch(tasks, state)

        # Phase 5: Select Parents (tree structure)
        await self._ctx.emit("activity_log", action="Pipeline: selecting parents", tool="build_pipeline")
        if scope_name:
            await self._ctx.emit("scope_phase", data={"scope": scope_name, "phase": "parents"})
        if tracker and scope_name:
            total_edges = sum(t.edges_created for t in tasks) - edges_before
            await tracker.end_phase(scope_name, "edges")
            await tracker.log_phase_outcome(scope_name, "edges", edge_count=total_edges, metrics=edge_metrics)
            await tracker.start_phase(scope_name, "parents")
        parent_metrics = await self._parent_pipeline.select_parents_batch(tasks)
        if tracker and scope_name:
            await tracker.end_phase(scope_name, "parents")
            await tracker.log_phase_outcome(scope_name, "parents", metrics=parent_metrics)

        # Build final results for create/refresh tasks that don't have results yet
        for t in tasks:
            if t.action in ("create", "refresh") and not t.result:
                if t.action == "create":
                    t.result = await self._node_pipeline.build_result_for_create(t)
                elif t.action == "refresh":
                    t.result = await self._node_pipeline.build_result_for_refresh(t)

        return self._build_results(tasks, state, len(entries))

    @staticmethod
    def _parse_entries(capped: list[dict[str, str]]) -> list[CreateNodeTask]:
        """Parse raw entries into CreateNodeTask objects."""
        from kt_db.keys import make_seed_key

        tasks: list[CreateNodeTask] = []
        for entry in capped:
            name = entry.get("name") or entry.get("concept") or entry.get("label") or entry.get("title") or ""
            node_type = entry.get("node_type") or entry.get("type") or "concept"
            if not name:
                logger.warning(
                    "build_pipeline: skipping entry with no name, keys=%s",
                    list(entry.keys()),
                )
                task = CreateNodeTask(name="", node_type=node_type, seed_key="")
                task.action = "skip"
                task.result = {"action": "skipped", "reason": "empty name"}
                tasks.append(task)
                continue
            entity_subtype = entry.get("entity_subtype") if node_type == "entity" else None
            sk = entry.get("seed_key") or make_seed_key(node_type, name)
            tasks.append(CreateNodeTask(name=name, node_type=node_type, seed_key=sk, entity_subtype=entity_subtype))
        return tasks

    @staticmethod
    def _build_results(
        tasks: list[CreateNodeTask],
        state: PipelineState,
        original_count: int,
    ) -> dict[str, Any]:
        """Build the final return value from processed tasks."""
        results: list[dict[str, Any]] = []
        for t in tasks:
            results.append(t.result)
            if t.result.get("action") not in ("skipped", "error", "skip"):
                logger.info(
                    "build_pipeline '%s' (type=%s) -> %s",
                    t.name,
                    t.node_type,
                    t.result.get("action", "unknown"),
                )

        actions: dict[str, int] = {}
        for r in results:
            action = r.get("action", "unknown")
            actions[action] = actions.get(action, 0) + 1

        return {
            "results": results,
            "count": len(results),
            "actions_summary": actions,
            "explore_remaining": state.explore_remaining,
            "capped": original_count > MAX_BATCH_SIZE,
        }


# Backwards-compatible alias
NodePipeline = BatchPipeline
