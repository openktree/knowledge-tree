"""Graph-scoped source endpoints.

Mirrors /api/v1/sources (read endpoints) scoped to a specific graph via
/api/v1/graphs/{graph_slug}/sources.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query

from kt_api.graph_context import GraphContext, get_graph_context
from kt_api.schemas import (
    PaginatedSourcesResponse,
    SourceDetailResponse,
    SourceResponse,
)
from kt_api.sources import _build_source_detail
from kt_db.repositories.sources import SourceRepository

router = APIRouter(prefix="/api/v1/graphs/{graph_slug}/sources", tags=["graph-sources"])


@router.get("", response_model=PaginatedSourcesResponse)
async def list_graph_sources(
    ctx: GraphContext = Depends(get_graph_context),
    offset: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    search: str | None = Query(None, description="Search by title or URI"),
    provider_id: str | None = Query(None, description="Filter by provider"),
    sort_by: str | None = Query(None, description="Sort by: retrieved_at, fact_count, prohibited_chunks"),
    has_prohibited: bool | None = Query(None),
    is_super_source: bool | None = Query(None),
    fetch_status: str | None = Query(
        None,
        description="Filter by fetch status: full_text, fetch_failed, snippet",
        pattern="^(full_text|fetch_failed|snippet)$",
    ),
) -> PaginatedSourcesResponse:
    """List raw sources in a specific graph."""
    async with ctx.graph_session_factory() as session:
        repo = SourceRepository(session)
        sources = await repo.list_sources(
            offset=offset,
            limit=limit,
            search=search,
            provider_id=provider_id,
            sort_by=sort_by,
            has_prohibited=has_prohibited,
            is_super_source=is_super_source,
            fetch_status=fetch_status,
        )
        total = await repo.count_sources(
            search=search,
            provider_id=provider_id,
            has_prohibited=has_prohibited,
            is_super_source=is_super_source,
            fetch_status=fetch_status,
        )
        return PaginatedSourcesResponse(
            items=[
                SourceResponse(
                    id=str(s.id),
                    uri=s.uri,
                    title=s.title,
                    provider_id=s.provider_id,
                    retrieved_at=s.retrieved_at,
                    fact_count=s.fact_count,
                    prohibited_chunk_count=s.prohibited_chunk_count,
                    is_super_source=s.is_super_source,
                    is_full_text=s.is_full_text,
                    fetch_attempted=s.fetch_attempted,
                    fetch_error=s.fetch_error,
                )
                for s in sources
            ],
            total=total,
            offset=offset,
            limit=limit,
        )


@router.get("/{source_id}", response_model=SourceDetailResponse)
async def get_graph_source(
    source_id: str,
    ctx: GraphContext = Depends(get_graph_context),
) -> SourceDetailResponse:
    """Get full detail for a single source in a specific graph."""
    try:
        uid = uuid.UUID(source_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid source ID format")

    async with ctx.graph_session_factory() as session:
        return await _build_source_detail(uid, session)
