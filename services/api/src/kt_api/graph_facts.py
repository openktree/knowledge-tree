"""Graph-scoped fact endpoints.

Mirrors /api/v1/facts (read endpoints) scoped to a specific graph via
/api/v1/graphs/{graph_slug}/facts.
"""

from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query

from kt_api.dependencies import get_qdrant_client_cached
from kt_api.graph_context import GraphContext, get_graph_context
from kt_api.schemas import (
    FactNodeInfo,
    FactResponse,
    FactSourceInfo,
    PaginatedFactsResponse,
)
from kt_db.repositories.facts import FactRepository
from kt_graph.read_engine import ReadGraphEngine

logger = logging.getLogger(__name__)

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
        engine = ReadGraphEngine(session=session, qdrant_client=get_qdrant_client_cached())
        facts = await engine.list_facts(
            offset=offset,
            limit=limit,
            search=search,
            fact_type=fact_type,
            author_org=author_org,
            source_domain=source_domain,
        )
        total = await engine.count_facts(
            search=search,
            fact_type=fact_type,
            author_org=author_org,
            source_domain=source_domain,
        )
        return PaginatedFactsResponse(
            items=[
                FactResponse(
                    id=str(f.id),
                    content=f.content,
                    fact_type=f.fact_type,
                    metadata=f.metadata_,
                    created_at=f.created_at,
                )
                for f in facts
            ],
            total=total,
            offset=offset,
            limit=limit,
        )


@router.get("/{fact_id}", response_model=FactResponse)
async def get_graph_fact(
    fact_id: str,
    ctx: GraphContext = Depends(get_graph_context),
) -> FactResponse:
    """Get a single fact by ID from a specific graph."""
    try:
        uid = uuid.UUID(fact_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid fact ID format")

    async with ctx.graph_session_factory() as session:
        repo = FactRepository(session)
        fact = await repo.get_by_id_with_sources(uid)
        if not fact:
            raise HTTPException(status_code=404, detail="Fact not found")
        return FactResponse(
            id=str(fact.id),
            content=fact.content,
            fact_type=fact.fact_type,
            metadata=fact.metadata_,
            created_at=fact.created_at,
            sources=[
                FactSourceInfo(
                    source_id=str(fs.raw_source.id),
                    uri=fs.raw_source.uri,
                    title=fs.raw_source.title,
                    provider_id=fs.raw_source.provider_id,
                    retrieved_at=fs.raw_source.retrieved_at,
                    context_snippet=fs.context_snippet,
                    attribution=fs.attribution,
                    author_person=fs.author_person,
                    author_org=fs.author_org,
                )
                for fs in fact.sources
            ],
        )


@router.get("/{fact_id}/nodes", response_model=list[FactNodeInfo])
async def get_graph_fact_nodes(
    fact_id: str,
    ctx: GraphContext = Depends(get_graph_context),
) -> list[FactNodeInfo]:
    """Get all nodes linked to a fact in a specific graph."""
    try:
        uid = uuid.UUID(fact_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid fact ID format")

    async with ctx.graph_session_factory() as session:
        engine = ReadGraphEngine(session=session, qdrant_client=get_qdrant_client_cached())
        repo = FactRepository(session)
        fact = await repo.get_by_id(uid)
        if not fact:
            raise HTTPException(status_code=404, detail="Fact not found")
        pairs = await engine.get_fact_nodes(uid)
        return [
            FactNodeInfo(
                node_id=str(node.id),
                concept=node.concept,
                node_type=node.node_type,
                relevance_score=nf.relevance_score,
                stance=nf.stance,
                linked_at=nf.linked_at,
            )
            for node, nf in pairs
        ]
