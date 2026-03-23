"""Shared agent result types and utilities.

Replaces the duplicated subgraph serialization and final-state extraction
logic across orchestrator, conversation, query_agent, and ingest_agent.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from typing import Any

from kt_agents_core.state import AgentContext

logger = logging.getLogger(__name__)


@dataclass
class AgentResult:
    """Unified result type returned by all agent workers."""

    answer: str = ""
    visited_nodes: list[str] = field(default_factory=list)
    created_nodes: list[str] = field(default_factory=list)
    created_edges: list[str] = field(default_factory=list)
    hidden_nodes: list[str] = field(default_factory=list)
    nav_used: int = 0
    explore_used: int = 0
    subgraph: dict[str, Any] = field(default_factory=lambda: {"nodes": [], "edges": []})


async def build_subgraph(
    node_ids: list[str],
    ctx: AgentContext,
    *,
    depth: int = 1,
) -> dict[str, Any]:
    """Build a serialized subgraph from node IDs using get_subgraph.

    Replaces the duplicated subgraph serialization blocks in orchestrator,
    conversation, and query_agent.  Uses GraphEngine.get_subgraph() which
    returns ORM Node/Edge objects, then serializes them to plain dicts.

    For ingest-style subgraphs (per-ID lookup with extra fields), use
    ``build_ingest_subgraph()`` instead.
    """
    if not node_ids:
        return {"nodes": [], "edges": []}

    uuids = [uuid.UUID(nid) for nid in node_ids if nid]
    if not uuids:
        return {"nodes": [], "edges": []}

    subgraph_data: dict[str, Any] = await ctx.graph_engine.get_subgraph(
        uuids, depth=depth
    )

    sg_nodes = subgraph_data.get("nodes", [])
    sg_edges = subgraph_data.get("edges", [])

    return {
        "nodes": [
            {
                "id": str(n.id),
                "concept": n.concept,
                "node_type": n.node_type,
                "parent_id": (
                    str(n.parent_id) if n.parent_id else None
                ),
                "attractor": n.attractor,
                "filter_id": n.filter_id,
                "max_content_tokens": n.max_content_tokens,
                "created_at": (
                    n.created_at.isoformat() if n.created_at else None
                ),
                "updated_at": (
                    n.updated_at.isoformat() if n.updated_at else None
                ),
                "update_count": n.update_count,
                "access_count": n.access_count,
                "richness": 0.0,
            }
            for n in sg_nodes
        ],
        "edges": [
            {
                "id": str(e.id),
                "source_node_id": str(e.source_node_id),
                "target_node_id": str(e.target_node_id),
                "relationship_type": e.relationship_type,
                "weight": e.weight,
                "justification": e.justification,
                "created_at": (
                    e.created_at.isoformat() if e.created_at else None
                ),
            }
            for e in sg_edges
        ],
    }


async def build_ingest_subgraph(
    node_ids: list[str],
    edge_ids: list[str],
    ctx: AgentContext,
) -> dict[str, Any]:
    """Build a subgraph from individual node/edge ID lookups.

    Used by the ingest agent which tracks created edge IDs separately
    and includes extra fields (convergence_score, justification).
    """
    nodes_data: list[dict[str, Any]] = []
    edges_data: list[dict[str, Any]] = []

    for nid in node_ids:
        try:
            node = await ctx.graph_engine.get_node(uuid.UUID(nid))
            if node:
                nodes_data.append({
                    "id": str(node.id),
                    "concept": node.concept,
                    "node_type": node.node_type,
                    "parent_id": (
                        str(node.parent_id)
                        if node.parent_id
                        else None
                    ),
                    "created_at": node.created_at.isoformat(),
                    "updated_at": node.updated_at.isoformat(),
                    "update_count": node.update_count,
                    "access_count": node.access_count,
                    "richness": 0.0,
                    "convergence_score": 0.0,
                    "max_content_tokens": node.max_content_tokens,
                })
        except Exception:
            logger.debug("Error loading node %s for subgraph", nid, exc_info=True)

    for eid in edge_ids:
        try:
            from kt_db.repositories.edges import EdgeRepository

            edge_repo = EdgeRepository(ctx.session)
            edge = await edge_repo.get_by_id(uuid.UUID(eid))
            if edge:
                edges_data.append({
                    "id": str(edge.id),
                    "source_node_id": str(edge.source_node_id),
                    "target_node_id": str(edge.target_node_id),
                    "relationship_type": edge.relationship_type,
                    "weight": edge.weight,
                    "justification": edge.justification,
                    "supporting_fact_ids": [],
                    "created_at": edge.created_at.isoformat(),
                })
        except Exception:
            logger.debug("Error loading edge %s for subgraph", eid, exc_info=True)

    return {"nodes": nodes_data, "edges": edges_data}


def extract_final_state(
    final_state: Any,
    fallback_state: Any,
    fields: list[str],
) -> dict[str, Any]:
    """Extract field values from LangGraph's final state.

    LangGraph returns either a dict or a state object depending on
    configuration. This helper normalizes the extraction pattern that
    was duplicated across all ``run_*`` functions.

    Args:
        final_state: The value returned by ``compiled.ainvoke()``.
        fallback_state: The original state object to use as fallback.
        fields: List of field names to extract.

    Returns:
        Dict mapping field names to their values.
    """
    result: dict[str, Any] = {}
    for f in fields:
        if isinstance(final_state, dict):
            result[f] = final_state.get(f, getattr(fallback_state, f, None))
        else:
            result[f] = getattr(final_state, f, getattr(fallback_state, f, None))
    return result
