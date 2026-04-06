"""Graph-scoped edge candidate endpoints.

Mirrors /api/v1/edge-candidates (read endpoints) scoped to a specific graph via
/api/v1/graphs/{graph_slug}/edge-candidates.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from kt_api.edge_candidates import (
    _get_edge_candidate_pair_impl,
    _list_candidates_for_seed_impl,
    _list_edge_candidate_pairs_impl,
)
from kt_api.graph_context import GraphContext, get_graph_context
from kt_api.schemas import (
    EdgeCandidatePairDetail,
    PaginatedEdgeCandidatePairs,
)

router = APIRouter(prefix="/api/v1/graphs/{graph_slug}/edge-candidates", tags=["graph-edge-candidates"])


@router.get("", response_model=PaginatedEdgeCandidatePairs)
async def list_graph_edge_candidate_pairs(
    ctx: GraphContext = Depends(get_graph_context),
    offset: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    status: str | None = Query(None),
    search: str | None = Query(None),
    min_facts: int = Query(1, ge=1),
) -> PaginatedEdgeCandidatePairs:
    """List edge candidate pairs in a specific graph."""
    return await _list_edge_candidate_pairs_impl(ctx.write_session_factory, offset, limit, status, search, min_facts)


@router.get("/by-seed/{seed_key:path}", response_model=PaginatedEdgeCandidatePairs)
async def list_graph_candidates_for_seed(
    seed_key: str,
    ctx: GraphContext = Depends(get_graph_context),
    offset: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
) -> PaginatedEdgeCandidatePairs:
    """List edge candidate pairs involving a specific seed in a graph."""
    return await _list_candidates_for_seed_impl(ctx.write_session_factory, seed_key, offset, limit)


@router.get("/pair", response_model=EdgeCandidatePairDetail)
async def get_graph_edge_candidate_pair(
    seed_key_a: str = Query(..., description="First seed key"),
    seed_key_b: str = Query(..., description="Second seed key"),
    ctx: GraphContext = Depends(get_graph_context),
) -> EdgeCandidatePairDetail:
    """Get full detail for a specific edge candidate pair in a graph."""
    return await _get_edge_candidate_pair_impl(ctx.write_session_factory, seed_key_a, seed_key_b)
