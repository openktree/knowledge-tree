"""Edge browse and management endpoints."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from kt_api.auth.tokens import require_auth
from kt_api.dependencies import get_db_session, get_qdrant_client_cached, require_api_key
from kt_api.schemas import (
    DeleteResponse,
    EdgeDetailResponse,
    EdgeResponse,
    FactResponse,
    FactSourceInfo,
    PaginatedEdgesResponse,
)
from kt_db.models import User
from kt_graph.read_engine import ReadGraphEngine

router = APIRouter(prefix="/api/v1/edges", tags=["edges"])


def _edge_to_response(
    edge: object,
    concept_map: dict[uuid.UUID, str] | None = None,
) -> EdgeResponse:
    """Convert an Edge ORM object to an EdgeResponse."""
    from kt_db.models import Edge as EdgeModel

    e: EdgeModel = edge  # type: ignore[assignment]
    source_concept = concept_map.get(e.source_node_id) if concept_map else None
    target_concept = concept_map.get(e.target_node_id) if concept_map else None
    return EdgeResponse(
        id=str(e.id),
        source_node_id=str(e.source_node_id),
        source_node_concept=source_concept,
        target_node_id=str(e.target_node_id),
        target_node_concept=target_concept,
        relationship_type=e.relationship_type,
        weight=e.weight,
        justification=e.justification,
        weight_source=e.weight_source,
        supporting_fact_ids=[str(ef.fact_id) for ef in e.edge_facts],
        created_at=e.created_at,
    )


@router.get("", response_model=PaginatedEdgesResponse)
async def list_edges(
    offset: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    relationship_type: str | None = Query(None, description="Filter by relationship type"),
    node_id: str | None = Query(None, description="Filter by source or target node ID"),
    search: str | None = Query(None, description="Search by justification text"),
    session: AsyncSession = Depends(get_db_session),
) -> PaginatedEdgesResponse:
    """List edges with pagination and optional filters."""
    engine = ReadGraphEngine(session=session, qdrant_client=get_qdrant_client_cached())
    parsed_node_id: uuid.UUID | None = None
    if node_id:
        try:
            parsed_node_id = uuid.UUID(node_id)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid node_id format")

    edges = await engine.list_edges(
        offset=offset,
        limit=limit,
        relationship_type=relationship_type,
        node_id=parsed_node_id,
        search=search,
    )
    total = await engine.count_edges(
        relationship_type=relationship_type,
        node_id=parsed_node_id,
        search=search,
    )

    # Batch-resolve node concepts for the page
    node_ids: set[uuid.UUID] = set()
    for e in edges:
        node_ids.add(e.source_node_id)
        node_ids.add(e.target_node_id)
    nodes = await engine.get_nodes_by_ids(list(node_ids))
    concept_map = {n.id: n.concept for n in nodes}

    return PaginatedEdgesResponse(
        items=[_edge_to_response(e, concept_map) for e in edges],
        total=total,
        offset=offset,
        limit=limit,
    )


@router.get("/between", response_model=list[EdgeDetailResponse])
async def get_edges_between(
    source: str = Query(..., description="Source node UUID or key"),
    target: str = Query(..., description="Target node UUID or key"),
    session: AsyncSession = Depends(get_db_session),
) -> list[EdgeDetailResponse]:
    """Get all edges between two specific nodes with full detail."""
    from kt_db.keys import key_to_uuid, url_key_to_node_key

    def _parse(nid: str) -> uuid.UUID:
        try:
            return uuid.UUID(nid)
        except ValueError:
            return key_to_uuid(url_key_to_node_key(nid))

    source_id = _parse(source)
    target_id = _parse(target)

    engine = ReadGraphEngine(session=session, qdrant_client=get_qdrant_client_cached())
    source_node = await engine.get_node(source_id)
    target_node = await engine.get_node(target_id)
    if not source_node or not target_node:
        raise HTTPException(status_code=404, detail="Source or target node not found")

    # Get all edges touching the source, filter to those connecting to target
    all_edges = await engine.get_edges(source_id, direction="both")
    matching = [e for e in all_edges if {e.source_node_id, e.target_node_id} == {source_id, target_id}]

    results: list[EdgeDetailResponse] = []
    for edge in matching:
        facts = await engine.get_edge_facts(edge.id)
        results.append(
            EdgeDetailResponse(
                id=str(edge.id),
                source_node_id=str(edge.source_node_id),
                source_node_concept=source_node.concept,
                target_node_id=str(edge.target_node_id),
                target_node_concept=target_node.concept,
                relationship_type=edge.relationship_type,
                weight=edge.weight,
                justification=edge.justification,
                weight_source=edge.weight_source,
                supporting_fact_ids=[str(ef.fact_id) for ef in edge.edge_facts],
                supporting_facts=[
                    FactResponse(
                        id=str(f.id),
                        content=f.content,
                        fact_type=f.fact_type,
                        metadata=f.metadata_,
                        created_at=f.created_at,
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
                            for fs in f.sources
                        ]
                        if hasattr(f, "sources") and f.sources
                        else [],
                    )
                    for f in facts
                ],
                created_at=edge.created_at,
            )
        )
    return results


@router.get("/{edge_id}", response_model=EdgeDetailResponse)
async def get_edge(
    edge_id: str,
    session: AsyncSession = Depends(get_db_session),
) -> EdgeDetailResponse:
    """Get a single edge by ID with resolved node names and full facts."""
    engine = ReadGraphEngine(session=session, qdrant_client=get_qdrant_client_cached())
    try:
        uid = uuid.UUID(edge_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid edge ID format")

    edge = await engine.get_edge_by_id(uid)
    if not edge:
        raise HTTPException(status_code=404, detail="Edge not found")

    # Resolve node concepts
    source_node = await engine.get_node(edge.source_node_id)
    target_node = await engine.get_node(edge.target_node_id)

    # Fetch full facts
    facts = await engine.get_edge_facts(uid)

    return EdgeDetailResponse(
        id=str(edge.id),
        source_node_id=str(edge.source_node_id),
        source_node_concept=source_node.concept if source_node else None,
        target_node_id=str(edge.target_node_id),
        target_node_concept=target_node.concept if target_node else None,
        relationship_type=edge.relationship_type,
        weight=edge.weight,
        justification=edge.justification,
        weight_source=edge.weight_source,
        supporting_fact_ids=[str(ef.fact_id) for ef in edge.edge_facts],
        supporting_facts=[
            FactResponse(
                id=str(f.id),
                content=f.content,
                fact_type=f.fact_type,
                metadata=f.metadata_,
                created_at=f.created_at,
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
                    for fs in f.sources
                ]
                if hasattr(f, "sources") and f.sources
                else [],
            )
            for f in facts
        ],
        created_at=edge.created_at,
    )


@router.post("/{edge_id}/enrich")
async def enrich_edge(
    edge_id: str,
    user: User = Depends(require_auth),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, str]:
    """Trigger on-demand justification generation for a co-occurrence edge."""
    engine = ReadGraphEngine(session=session, qdrant_client=get_qdrant_client_cached())
    try:
        uid = uuid.UUID(edge_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid edge ID format")
    edge = await engine.get_edge_by_id(uid)
    if not edge:
        raise HTTPException(status_code=404, detail="Edge not found")

    from kt_api.dispatch import dispatch_with_graph

    api_key = require_api_key(user)
    await dispatch_with_graph("enrich_edge", {"edge_id": edge_id, "api_key": api_key})
    return {"status": "started", "edge_id": edge_id}


@router.delete("/{edge_id}", response_model=DeleteResponse)
async def delete_edge(
    edge_id: str,
    session: AsyncSession = Depends(get_db_session),
) -> DeleteResponse:
    """Delete an edge by ID."""
    engine = ReadGraphEngine(session=session, qdrant_client=get_qdrant_client_cached())
    try:
        uid = uuid.UUID(edge_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid edge ID format")
    deleted = await engine.delete_edge(uid)
    if not deleted:
        raise HTTPException(status_code=404, detail="Edge not found")
    await session.commit()
    return DeleteResponse(deleted=True, id=edge_id)
