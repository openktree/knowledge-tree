"""Graph-scoped seed endpoints.

Mirrors /api/v1/seeds (read endpoints) scoped to a specific graph via
/api/v1/graphs/{graph_slug}/seeds. Uses write_session_factory from GraphContext.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from kt_api.graph_context import GraphContext, get_graph_context
from kt_api.schemas import (
    PaginatedSeedsResponse,
    SeedDetailResponse,
    SeedDivergenceResponse,
    SeedTreeResponse,
)
from kt_api.seeds import (
    _get_seed_divergence_impl,
    _get_seed_impl,
    _get_seed_tree_impl,
    _list_seeds_impl,
)

router = APIRouter(prefix="/api/v1/graphs/{graph_slug}/seeds", tags=["graph-seeds"])


@router.get("", response_model=PaginatedSeedsResponse)
async def list_graph_seeds(
    ctx: GraphContext = Depends(get_graph_context),
    offset: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    search: str | None = Query(None, description="Filter by name"),
    status: str | None = Query(None, description="Filter by status"),
    node_type: str | None = Query(None, description="Filter by node type"),
) -> PaginatedSeedsResponse:
    """List seeds in a specific graph."""
    return await _list_seeds_impl(ctx.write_session_factory, offset, limit, search, status, node_type)


@router.get("/divergence/{seed_key:path}", response_model=SeedDivergenceResponse)
async def get_graph_seed_divergence(
    seed_key: str,
    ctx: GraphContext = Depends(get_graph_context),
) -> SeedDivergenceResponse:
    """Compute fact embedding divergence for a seed in a specific graph."""
    from kt_api.dependencies import get_qdrant_client_cached

    collection_name = f"{ctx.qdrant_collection_prefix}facts" if ctx.qdrant_collection_prefix else "facts"
    return await _get_seed_divergence_impl(
        ctx.write_session_factory,
        seed_key,
        get_qdrant_client_cached(),
        collection_name,
    )


@router.get("/tree/{seed_key:path}", response_model=SeedTreeResponse)
async def get_graph_seed_tree(
    seed_key: str,
    ctx: GraphContext = Depends(get_graph_context),
) -> SeedTreeResponse:
    """Get the full disambiguation tree for a seed in a specific graph."""
    return await _get_seed_tree_impl(ctx.write_session_factory, seed_key)


@router.get("/{seed_key:path}", response_model=SeedDetailResponse)
async def get_graph_seed(
    seed_key: str,
    ctx: GraphContext = Depends(get_graph_context),
) -> SeedDetailResponse:
    """Get full detail for a single seed in a specific graph."""
    return await _get_seed_impl(ctx.write_session_factory, seed_key)
