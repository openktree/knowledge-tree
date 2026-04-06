"""Graph-scoped edge endpoints.

Mirrors /api/v1/edges (read endpoints) scoped to a specific graph via
/api/v1/graphs/{graph_slug}/edges.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query

from kt_api.dependencies import get_qdrant_client_cached
from kt_api.edges import _edge_to_response
from kt_api.graph_context import GraphContext, get_graph_context
from kt_api.schemas import (
    EdgeDetailResponse,
    FactResponse,
    FactSourceInfo,
    PaginatedEdgesResponse,
)
from kt_graph.read_engine import ReadGraphEngine

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
async def get_graph_edges_between(
    source: str = Query(..., description="Source node UUID or key"),
    target: str = Query(..., description="Target node UUID or key"),
    ctx: GraphContext = Depends(get_graph_context),
) -> list[EdgeDetailResponse]:
    """Get all edges between two nodes in a specific graph."""
    from kt_db.keys import key_to_uuid, url_key_to_node_key

    def _parse(nid: str) -> uuid.UUID:
        try:
            return uuid.UUID(nid)
        except ValueError:
            return key_to_uuid(url_key_to_node_key(nid))

    source_id = _parse(source)
    target_id = _parse(target)

    async with ctx.graph_session_factory() as session:
        engine = ReadGraphEngine(session=session, qdrant_client=get_qdrant_client_cached())
        source_node = await engine.get_node(source_id)
        target_node = await engine.get_node(target_id)
        if not source_node or not target_node:
            raise HTTPException(status_code=404, detail="Source or target node not found")

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
async def get_graph_edge(
    edge_id: str,
    ctx: GraphContext = Depends(get_graph_context),
) -> EdgeDetailResponse:
    """Get a single edge by ID from a specific graph."""
    try:
        uid = uuid.UUID(edge_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid edge ID format")

    async with ctx.graph_session_factory() as session:
        engine = ReadGraphEngine(session=session, qdrant_client=get_qdrant_client_cached())
        edge = await engine.get_edge_by_id(uid)
        if not edge:
            raise HTTPException(status_code=404, detail="Edge not found")

        source_node = await engine.get_node(edge.source_node_id)
        target_node = await engine.get_node(edge.target_node_id)
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
