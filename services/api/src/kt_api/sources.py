"""Raw source lookup endpoints."""

from __future__ import annotations

import logging
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from kt_api.auth.permissions import require_system_permission
from kt_rbac import Permission
from kt_api.dependencies import get_db_session
from kt_api.schemas import (
    DailyFailureCount,
    DomainFailureCount,
    ErrorGroupCount,
    FactResponse,
    FactSourceInfo,
    PaginatedSourcesResponse,
    ProhibitedChunkResponse,
    SourceDetailResponse,
    SourceInsightsResponse,
    SourceLinkedNode,
    SourceReingestResponse,
    SourceResponse,
)
from kt_db.models import User
from kt_db.repositories.sources import SourceRepository

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/sources", tags=["sources"])


async def _build_source_detail(
    source_id: uuid.UUID,
    session: AsyncSession,
) -> SourceDetailResponse:
    """Build a SourceDetailResponse for a given source ID."""
    repo = SourceRepository(session)
    source = await repo.get_by_id(source_id)
    if not source:
        raise HTTPException(status_code=404, detail="Source not found")

    facts = await repo.get_facts_for_source(source_id)
    linked_nodes = await repo.get_linked_nodes_for_source(source_id)
    prohibited = await repo.get_prohibited_chunks(source_id)

    content_preview = None
    if source.raw_content:
        content_preview = source.raw_content[:2000]

    return SourceDetailResponse(
        id=str(source.id),
        uri=source.uri,
        title=source.title,
        provider_id=source.provider_id,
        retrieved_at=source.retrieved_at,
        fact_count=source.fact_count,
        prohibited_chunk_count=source.prohibited_chunk_count,
        is_full_text=source.is_full_text,
        fetch_error=source.fetch_error,
        content_type=source.content_type,
        content_preview=content_preview,
        facts=[
            FactResponse(
                id=str(f.id),
                content=f.content,
                fact_type=f.fact_type,
                metadata=f.metadata_,
                created_at=f.created_at,
                sources=[
                    FactSourceInfo(
                        source_id=str(fs.raw_source_id),
                        uri=fs.raw_source.uri,
                        title=fs.raw_source.title,
                        provider_id=fs.raw_source.provider_id,
                        retrieved_at=fs.raw_source.retrieved_at,
                        context_snippet=fs.context_snippet,
                        attribution=fs.attribution,
                        author_person=fs.author_person,
                        author_org=fs.author_org,
                    )
                    for fs in f.sources
                    if fs.raw_source is not None
                ],
            )
            for f in facts
        ],
        linked_nodes=[SourceLinkedNode(**n) for n in linked_nodes],
        prohibited_chunks=[
            ProhibitedChunkResponse(
                id=str(pc.id),
                chunk_text=pc.chunk_text,
                model_id=pc.model_id,
                fallback_model_id=pc.fallback_model_id,
                error_message=pc.error_message,
                created_at=pc.created_at,
            )
            for pc in prohibited
        ],
    )


async def _list_sources_impl(
    session: AsyncSession,
    offset: int,
    limit: int,
    search: str | None,
    provider_id: str | None,
    sort_by: str | None,
    has_prohibited: bool | None,
    is_super_source: bool | None,
    fetch_status: str | None,
) -> PaginatedSourcesResponse:
    """Shared implementation for listing sources with pagination and filters."""
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


@router.get("", response_model=PaginatedSourcesResponse)
async def list_sources(
    offset: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    search: str | None = Query(None, description="Search by title or URI"),
    provider_id: str | None = Query(None, description="Filter by provider"),
    sort_by: str | None = Query(None, description="Sort by: retrieved_at (default), fact_count, prohibited_chunks"),
    has_prohibited: bool | None = Query(None, description="Filter to sources with/without prohibited chunks"),
    is_super_source: bool | None = Query(None, description="Filter to super sources (large, deferred)"),
    fetch_status: str | None = Query(
        None,
        description="Filter by fetch status: full_text, fetch_failed, snippet",
        pattern="^(full_text|fetch_failed|snippet)$",
    ),
    session: AsyncSession = Depends(get_db_session),
) -> PaginatedSourcesResponse:
    """List raw sources with pagination and optional filters."""
    return await _list_sources_impl(
        session, offset, limit, search, provider_id, sort_by, has_prohibited, is_super_source, fetch_status
    )


@router.get("/insights", response_model=SourceInsightsResponse)
async def get_source_insights(
    since: datetime | None = Query(None, description="Only include sources retrieved after this ISO datetime"),
    _admin: User = Depends(require_system_permission(Permission.SYSTEM_ADMIN_OPS)),
    session: AsyncSession = Depends(get_db_session),
) -> SourceInsightsResponse:
    """Get aggregate insights about source fetch health (admin only)."""
    repo = SourceRepository(session)
    # Queries are sequential because they share one AsyncSession (single DB connection).
    summary = await repo.get_insights_summary(since)
    top_domains = await repo.get_top_failed_domains(since)
    errors = await repo.get_common_fetch_errors(since)
    daily = await repo.get_failures_per_day(since)
    return SourceInsightsResponse(
        total_count=summary["total_count"],
        failed_count=summary["failed_count"],
        pending_super_count=summary["pending_super_count"],
        top_failed_domains=[DomainFailureCount(**d) for d in top_domains],
        common_errors=[ErrorGroupCount(**e) for e in errors],
        failures_per_day=[DailyFailureCount(**d) for d in daily],
    )


@router.get("/{source_id}", response_model=SourceDetailResponse)
async def get_source(
    source_id: str,
    session: AsyncSession = Depends(get_db_session),
) -> SourceDetailResponse:
    """Get full detail for a single raw source including its facts and linked nodes."""
    try:
        uid = uuid.UUID(source_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid source ID format")
    return await _build_source_detail(uid, session)


@router.post("/{source_id}/reingest", response_model=SourceReingestResponse)
async def reingest_source(
    source_id: str,
    session: AsyncSession = Depends(get_db_session),
) -> SourceReingestResponse:
    """Re-fetch a source URL and re-extract facts via Hatchet workflow.

    Dispatches the reingest_source_wf which re-fetches the URL, force-updates
    content (bypassing hash dedup), and runs the same decompose_source_task
    used by bottom-up ingestion. Waits for the workflow to complete.
    """
    try:
        uid = uuid.UUID(source_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid source ID format")

    repo = SourceRepository(session)
    source = await repo.get_by_id(uid)
    if not source:
        raise HTTPException(status_code=404, detail="Source not found")

    # Dispatch Hatchet workflow
    from kt_hatchet.client import run_workflow
    from kt_hatchet.models import ReingestSourceOutput

    result = await run_workflow("reingest_source", {"raw_source_id": source_id})
    output = ReingestSourceOutput.model_validate(result)

    # Refresh source detail after workflow completion
    session.expire_all()
    detail = await _build_source_detail(uid, session)

    return SourceReingestResponse(
        source=detail,
        new_facts_count=output.fact_count,
        content_updated=output.content_updated,
        message=output.message,
    )
