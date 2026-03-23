"""Tool: build_node — Unified node builder that dispatches by node_type.

Merges the old build_concept / build_entity tool split into a single
``build_node(name, node_type)`` interface so the LLM always uses one tool
and specifies the type as a parameter.  This eliminates tool-selection bias
that caused the model to default to concept-only creation.

``build_nodes`` is the batch variant that uses the phased pipeline for
parallel processing of expensive HTTP/LLM calls.
"""

from __future__ import annotations

import logging
from typing import Any

from kt_worker_orchestrator.agents.orchestrator_state import OrchestratorState
from kt_agents_core.state import AgentContext
from kt_worker_nodes.pipelines.building.unified import UnifiedNodeBuilder
from kt_worker_nodes.pipelines.batch import BatchPipeline

logger = logging.getLogger(__name__)

MAX_BATCH_SIZE = 10

VALID_NODE_TYPES = {"concept", "entity", "event"}


async def build_node_impl(
    name: str,
    node_type: str,
    ctx: AgentContext,
    state: OrchestratorState,
) -> dict[str, Any]:
    """Build a single node via the unified builder.

    Args:
        name: Node name / concept label.
        node_type: One of "concept", "entity", "event".
                   Defaults to "concept" if invalid.
        ctx: Agent context.
        state: Orchestrator state (mutated in-place).

    Returns:
        Dict with action, node_id, fact_count, suggested_concepts, etc.
    """
    if node_type not in VALID_NODE_TYPES:
        node_type = "concept"

    return await UnifiedNodeBuilder(ctx).build(name, node_type, ctx, state)


async def build_nodes_impl(
    nodes: list[dict[str, str]],
    ctx: AgentContext,
    state: OrchestratorState,
    scope_name: str = "",
) -> dict[str, Any]:
    """Batch build multiple nodes using the phased pipeline.

    The pipeline processes all nodes through 4 phases:
    1. Classify + Gather Facts (parallel external search)
    2. Create Nodes + Link Facts (sequential DB, then commit)
    3. Generate Dimensions (parallel LLM calls)
    4. Materialize Edges (all nodes exist, better resolution)

    Args:
        nodes: List of dicts with "name" and "node_type" keys.
               Capped at MAX_BATCH_SIZE.
        ctx: Agent context.
        state: Orchestrator state (mutated in-place).
        scope_name: Optional scope name for progress events.

    Returns:
        Dict with results list and summary.
    """
    return await BatchPipeline(ctx).build_batch(nodes, state, scope_name=scope_name)
