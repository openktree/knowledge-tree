"""Graph-scoped edge endpoints.

Mirrors /api/v1/edges (read endpoints) scoped to a specific graph via
/api/v1/graphs/{graph_slug}/edges.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from kt_api.dependencies import get_qdrant_client_cached
from kt_api.edges import _get_edge_impl, _get_edges_between_impl, _list_edges_impl
from kt_api.graph_context import GraphContext, get_graph_context
from kt_api.schemas import (
    EdgeDetailResponse,
    PaginatedEdgesResponse,
)

router = APIRouter(prefix="/api/v1/graphs/{graph_slug}/edges", tags=["graph-edges"])


@router.get("", response_model=PaginatedEdgesResponse)
async def list_graph_edges(
    ctx: GraphContext = Depends(get_graph_context),
    offset: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    relationship_type: str | None = Query(None, description="Filter by relationship type"),
    node_id: str | None = Query(None, description="Filter by source or target node ID"),
    search: str | None = Query(None, description="Search by justification text"),
) -> PaginatedEdgesResponse:
    """List edges in a specific graph."""
    async with ctx.graph_session_factory() as session:
        return await _list_edges_impl(
            session, get_qdrant_client_cached(), offset, limit, relationship_type, node_id, search
        )


@router.get("/between", response_model=list[EdgeDetailResponse])
async def get_graph_edges_between(
    source: str = Query(..., description="Source node UUID or key"),
    target: str = Query(..., description="Target node UUID or key"),
    ctx: GraphContext = Depends(get_graph_context),
) -> list[EdgeDetailResponse]:
    """Get all edges between two nodes in a specific graph."""
    async with ctx.graph_session_factory() as session:
        return await _get_edges_between_impl(session, get_qdrant_client_cached(), source, target)


@router.get("/{edge_id}", response_model=EdgeDetailResponse)
async def get_graph_edge(
    edge_id: str,
    ctx: GraphContext = Depends(get_graph_context),
) -> EdgeDetailResponse:
    """Get a single edge by ID from a specific graph."""
    async with ctx.graph_session_factory() as session:
        return await _get_edge_impl(session, get_qdrant_client_cached(), edge_id)
