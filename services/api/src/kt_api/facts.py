"""Fact lookup endpoints."""

from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from kt_api.dependencies import get_db_session, get_qdrant_client_cached
from kt_api.schemas import (
    DeleteResponse,
    FactNodeInfo,
    FactResponse,
    FactSourceInfo,
    FactUpdateRequest,
    PaginatedFactsResponse,
)
from kt_db.repositories.facts import FactRepository
from kt_graph.engine import GraphEngine

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/facts", tags=["facts"])


_embedding_service_cache: object | None = None
_embedding_service_initialized: bool = False


def _get_embedding_service():  # noqa: ANN202
    """Lazy-init cached embedding service singleton."""
    global _embedding_service_cache, _embedding_service_initialized  # noqa: PLW0603
    if not _embedding_service_initialized:
        from kt_config.settings import get_settings
        from kt_models.embeddings import EmbeddingService

        settings = get_settings()
        if settings.openrouter_api_key:
            _embedding_service_cache = EmbeddingService()
        _embedding_service_initialized = True
    return _embedding_service_cache


@router.get("", response_model=PaginatedFactsResponse)
async def list_facts(
    offset: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    search: str | None = Query(None, description="Search by fact content"),
    fact_type: str | None = Query(None, description="Filter by fact type"),
    author_org: str | None = Query(None, description="Filter by author organization (e.g. CNN, Reuters)"),
    source_domain: str | None = Query(None, description="Filter by source URL domain (e.g. cnn.com)"),
    session: AsyncSession = Depends(get_db_session),
) -> PaginatedFactsResponse:
    """List facts with pagination and optional filters."""
    engine = GraphEngine(session, qdrant_client=get_qdrant_client_cached())

    # Use hybrid search when a text query is provided, embeddings are available,
    # and no source-level filters are used (those require SQL joins).
    facts: list = []
    total: int = 0
    has_source_filters = bool(author_org or source_domain)
    use_hybrid = False

    if search and not has_source_filters:
        embedding_service = _get_embedding_service()
        if embedding_service is not None:
            try:
                query_embedding = await embedding_service.embed_text(search)
                facts = await engine.hybrid_search_facts(
                    query=search,
                    embedding=query_embedding,
                    limit=100,
                    fact_type=fact_type,
                )
                total = len(facts)
                facts = facts[offset : offset + limit]
                use_hybrid = True
            except Exception:
                logger.warning("Hybrid search failed, falling back to ILIKE", exc_info=True)

    if not use_hybrid:
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


@router.get("/search", response_model=list[FactResponse])
async def search_facts(
    fact_type: str | None = Query(None, description="Filter by fact type"),
    session: AsyncSession = Depends(get_db_session),
) -> list[FactResponse]:
    """Search facts by type."""
    repo = FactRepository(session)
    if fact_type:
        facts = await repo.get_facts_by_type(fact_type)
    else:
        facts = await repo.get_facts_by_type("claim")  # default to a common type
    return [
        FactResponse(
            id=str(f.id),
            content=f.content,
            fact_type=f.fact_type,
            metadata=f.metadata_,
            created_at=f.created_at,
        )
        for f in facts
    ]


@router.get("/{fact_id}", response_model=FactResponse)
async def get_fact(
    fact_id: str,
    session: AsyncSession = Depends(get_db_session),
) -> FactResponse:
    """Get a single fact by ID."""
    repo = FactRepository(session)
    try:
        uid = uuid.UUID(fact_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid fact ID format")
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
async def get_fact_nodes(
    fact_id: str,
    session: AsyncSession = Depends(get_db_session),
) -> list[FactNodeInfo]:
    """Get all nodes linked to a fact."""
    try:
        uid = uuid.UUID(fact_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid fact ID format")
    engine = GraphEngine(session, qdrant_client=get_qdrant_client_cached())
    # Verify fact exists
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


@router.patch("/{fact_id}", response_model=FactResponse)
async def update_fact(
    fact_id: str,
    body: FactUpdateRequest,
    session: AsyncSession = Depends(get_db_session),
) -> FactResponse:
    """Update a fact's editable fields."""
    engine = GraphEngine(session, qdrant_client=get_qdrant_client_cached())
    try:
        uid = uuid.UUID(fact_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid fact ID format")
    fields = body.model_dump(exclude_unset=True)
    if not fields:
        raise HTTPException(status_code=400, detail="No fields to update")
    try:
        fact = await engine.update_fact(uid, **fields)
    except ValueError:
        raise HTTPException(status_code=404, detail="Fact not found")
    await session.commit()
    return FactResponse(
        id=str(fact.id),
        content=fact.content,
        fact_type=fact.fact_type,
        metadata=fact.metadata_,
        created_at=fact.created_at,
    )


@router.delete("/{fact_id}", response_model=DeleteResponse)
async def delete_fact(
    fact_id: str,
    session: AsyncSession = Depends(get_db_session),
) -> DeleteResponse:
    """Delete a fact and unlink it from all nodes."""
    engine = GraphEngine(session, qdrant_client=get_qdrant_client_cached())
    try:
        uid = uuid.UUID(fact_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid fact ID format")
    deleted = await engine.delete_fact(uid)
    if not deleted:
        raise HTTPException(status_code=404, detail="Fact not found")
    await session.commit()
    return DeleteResponse(deleted=True, id=fact_id)
