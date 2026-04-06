"""Graph-scoped node endpoints.

Mirrors /api/v1/nodes (read endpoints) scoped to a specific graph via
/api/v1/graphs/{graph_slug}/nodes. Uses GraphContext for session routing.

Most sub-resource endpoints delegate to shared ``_impl`` helpers defined in
``kt_api.nodes`` so the response-building logic is not duplicated.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Query

from kt_api.dependencies import get_qdrant_client_cached
from kt_api.graph_context import GraphContext, get_graph_context
from kt_api.nodes import (
    _batch_parent_concepts,
    _batch_seed_fact_counts,
    _build_node_response,
    _get_node_children_impl,
    _get_node_convergence_impl,
    _get_node_dimensions_impl,
    _get_node_edges_impl,
    _get_node_facts_impl,
    _get_node_history_impl,
    _get_node_impl,
    _get_node_perspectives_impl,
)
from kt_api.schemas import (
    ConvergenceResponse,
    DimensionResponse,
    EdgeResponse,
    FactResponse,
    NodeResponse,
    NodeVersionResponse,
    PaginatedNodesResponse,
)
from kt_graph.read_engine import ReadGraphEngine

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/graphs/{graph_slug}/nodes", tags=["graph-nodes"])


# ── List (graph-specific logic — not shared) ──────────────────────


@router.get("", response_model=PaginatedNodesResponse)
async def list_graph_nodes(
    ctx: GraphContext = Depends(get_graph_context),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    node_type: str | None = Query(default=None),
    search: str | None = Query(default=None),
    sort: str = Query("updated_at", description="Sort order: updated_at, edge_count, fact_count"),
) -> PaginatedNodesResponse:
    """List nodes in a specific graph."""
    async with ctx.graph_session_factory() as session:
        engine = ReadGraphEngine(session=session, qdrant_client=get_qdrant_client_cached())
        nodes = await engine.list_nodes(offset=offset, limit=limit, search=search, node_type=node_type, sort=sort)
        total = await engine.count_nodes(search=search, node_type=node_type)
        parent_map = await _batch_parent_concepts(session, nodes)
        seed_fc = await _batch_seed_fact_counts(nodes, ctx.write_session_factory)
        items = [_build_node_response(n, parent_map, seed_fc) for n in nodes]
        return PaginatedNodesResponse(items=items, total=total, offset=offset, limit=limit)


# ── Get + Sub-resources (delegated to shared _impl helpers) ───────


@router.get("/{node_id}", response_model=NodeResponse)
async def get_graph_node(
    node_id: str,
    ctx: GraphContext = Depends(get_graph_context),
) -> NodeResponse:
    """Get a single node from a specific graph."""
    async with ctx.graph_session_factory() as session:
        return await _get_node_impl(
            node_id, session, get_qdrant_client_cached(), write_session_factory=ctx.write_session_factory
        )


@router.get("/{node_id}/dimensions", response_model=list[DimensionResponse])
async def get_graph_node_dimensions(
    node_id: str,
    ctx: GraphContext = Depends(get_graph_context),
) -> list[DimensionResponse]:
    """Get all dimensions for a node in a specific graph."""
    async with ctx.graph_session_factory() as session:
        return await _get_node_dimensions_impl(node_id, session, get_qdrant_client_cached())


@router.get("/{node_id}/facts", response_model=list[FactResponse])
async def get_graph_node_facts(
    node_id: str,
    ctx: GraphContext = Depends(get_graph_context),
) -> list[FactResponse]:
    """Get all facts linked to a node in a specific graph."""
    async with ctx.graph_session_factory() as session:
        return await _get_node_facts_impl(node_id, session, get_qdrant_client_cached())


@router.get("/{node_id}/edges", response_model=list[EdgeResponse])
async def get_graph_node_edges(
    node_id: str,
    direction: str = Query("both", description="Edge direction: outgoing, incoming, or both"),
    ctx: GraphContext = Depends(get_graph_context),
) -> list[EdgeResponse]:
    """Get all edges connected to a node in a specific graph."""
    async with ctx.graph_session_factory() as session:
        return await _get_node_edges_impl(node_id, session, get_qdrant_client_cached(), direction=direction)


@router.get("/{node_id}/history", response_model=list[NodeVersionResponse])
async def get_graph_node_history(
    node_id: str,
    ctx: GraphContext = Depends(get_graph_context),
) -> list[NodeVersionResponse]:
    """Get version history for a node in a specific graph."""
    async with ctx.graph_session_factory() as session:
        return await _get_node_history_impl(node_id, session, get_qdrant_client_cached())


@router.get("/{node_id}/convergence", response_model=ConvergenceResponse)
async def get_graph_node_convergence(
    node_id: str,
    ctx: GraphContext = Depends(get_graph_context),
) -> ConvergenceResponse:
    """Compute convergence analysis for a node in a specific graph."""
    async with ctx.graph_session_factory() as session:
        return await _get_node_convergence_impl(node_id, session, get_qdrant_client_cached())


@router.get("/{node_id}/perspectives", response_model=list[NodeResponse])
async def get_graph_node_perspectives(
    node_id: str,
    ctx: GraphContext = Depends(get_graph_context),
) -> list[NodeResponse]:
    """Get all perspective nodes for a concept node in a specific graph."""
    async with ctx.graph_session_factory() as session:
        return await _get_node_perspectives_impl(
            node_id, session, get_qdrant_client_cached(), write_session_factory=ctx.write_session_factory
        )


@router.get("/{node_id}/children", response_model=list[NodeResponse])
async def get_graph_node_children(
    node_id: str,
    ctx: GraphContext = Depends(get_graph_context),
) -> list[NodeResponse]:
    """Get all child nodes in a specific graph."""
    async with ctx.graph_session_factory() as session:
        return await _get_node_children_impl(node_id, session, write_session_factory=ctx.write_session_factory)
