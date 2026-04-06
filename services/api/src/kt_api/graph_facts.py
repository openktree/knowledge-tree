"""Graph-scoped fact endpoints.

Mirrors /api/v1/facts (read endpoints) scoped to a specific graph via
/api/v1/graphs/{graph_slug}/facts.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from kt_api.dependencies import get_qdrant_client_cached
from kt_api.facts import _get_fact_impl, _get_fact_nodes_impl, _list_facts_impl
from kt_api.graph_context import GraphContext, get_graph_context
from kt_api.schemas import (
    FactNodeInfo,
    FactResponse,
    PaginatedFactsResponse,
)

router = APIRouter(prefix="/api/v1/graphs/{graph_slug}/facts", tags=["graph-facts"])


@router.get("", response_model=PaginatedFactsResponse)
async def list_graph_facts(
    ctx: GraphContext = Depends(get_graph_context),
    offset: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    search: str | None = Query(None, description="Search by fact content"),
    fact_type: str | None = Query(None, description="Filter by fact type"),
    author_org: str | None = Query(None, description="Filter by author organization"),
    source_domain: str | None = Query(None, description="Filter by source URL domain"),
) -> PaginatedFactsResponse:
    """List facts in a specific graph with pagination and optional filters."""
    async with ctx.graph_session_factory() as session:
        return await _list_facts_impl(
            session, get_qdrant_client_cached(), offset, limit, search, fact_type, author_org, source_domain
        )


@router.get("/{fact_id}", response_model=FactResponse)
async def get_graph_fact(
    fact_id: str,
    ctx: GraphContext = Depends(get_graph_context),
) -> FactResponse:
    """Get a single fact by ID from a specific graph."""
    async with ctx.graph_session_factory() as session:
        return await _get_fact_impl(session, fact_id)


@router.get("/{fact_id}/nodes", response_model=list[FactNodeInfo])
async def get_graph_fact_nodes(
    fact_id: str,
    ctx: GraphContext = Depends(get_graph_context),
) -> list[FactNodeInfo]:
    """Get all nodes linked to a fact in a specific graph."""
    async with ctx.graph_session_factory() as session:
        return await _get_fact_nodes_impl(session, get_qdrant_client_cached(), fact_id)
