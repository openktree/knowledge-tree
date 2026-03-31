"""Graph-level endpoints (subgraph, stats)."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from kt_api.dependencies import get_db_session, get_qdrant_client_cached
from kt_api.nodes import _compute_richness
from kt_api.schemas import (
    EdgeResponse,
    GraphStatsResponse,
    NodeResponse,
    PathResponse,
    PathsResponse,
    PathStepResponse,
    SubgraphResponse,
)
from kt_db.keys import key_to_uuid, url_key_to_node_key
from kt_db.models import Edge, Fact, Node, RawSource
from kt_graph.engine import GraphEngine

router = APIRouter(prefix="/api/v1/graph", tags=["graph"])


@router.get("/stats", response_model=GraphStatsResponse)
async def get_graph_stats(
    session: AsyncSession = Depends(get_db_session),
) -> GraphStatsResponse:
    """Get graph-wide statistics."""
    from kt_config.cache import cache_get, cache_set

    cache_key = "kt:graph:stats"
    cached = await cache_get(cache_key)
    if cached is not None:
        return GraphStatsResponse(**cached)

    node_count = (await session.execute(select(func.count(Node.id)))).scalar_one()
    edge_count = (await session.execute(select(func.count(Edge.id)))).scalar_one()
    fact_count = (await session.execute(select(func.count(Fact.id)))).scalar_one()
    source_count = (await session.execute(select(func.count(RawSource.id)))).scalar_one()

    result = GraphStatsResponse(
        node_count=node_count,
        edge_count=edge_count,
        fact_count=fact_count,
        source_count=source_count,
    )
    await cache_set(cache_key, result.model_dump(), ttl=300)
    return result


@router.get("/subgraph", response_model=SubgraphResponse)
async def get_subgraph(
    node_ids: str = Query(..., description="Comma-separated node UUIDs"),
    depth: int = Query(0, ge=0, le=5, description="Number of neighbor hops to include"),
    session: AsyncSession = Depends(get_db_session),
) -> SubgraphResponse:
    """Get a subgraph containing the specified nodes and edges between them."""
    engine = GraphEngine(session, qdrant_client=get_qdrant_client_cached())

    def _parse_node_id(nid: str) -> uuid.UUID:
        try:
            return uuid.UUID(nid)
        except ValueError:
            return key_to_uuid(url_key_to_node_key(nid))

    uuids = [_parse_node_id(nid.strip()) for nid in node_ids.split(",") if nid.strip()]

    if not uuids:
        return SubgraphResponse(nodes=[], edges=[])

    result = await engine.get_subgraph(uuids, depth=depth)
    nodes: list[Node] = result.get("nodes", [])  # type: ignore[assignment]
    edges: list[Edge] = result.get("edges", [])  # type: ignore[assignment]
    nodes_by_id = {n.id: n for n in nodes}
    # Resolve parent concepts: parents in the subgraph are already loaded;
    # for parents outside the subgraph, fall back to a batch query.
    parent_ids_outside = [n.parent_id for n in nodes if n.parent_id is not None and n.parent_id not in nodes_by_id]
    outside_parent_map: dict[uuid.UUID, str] = {}
    if parent_ids_outside:
        unique_pids = list(set(parent_ids_outside))
        stmt = select(Node.id, Node.concept).where(Node.id.in_(unique_pids))
        result = await session.execute(stmt)
        outside_parent_map = {row.id: row.concept for row in result.all()}

    def _parent_concept(n: Node) -> str | None:
        if n.parent_id is None:
            return None
        if n.parent_id in nodes_by_id:
            return nodes_by_id[n.parent_id].concept
        return outside_parent_map.get(n.parent_id)

    return SubgraphResponse(
        nodes=[
            NodeResponse(
                id=str(n.id),
                concept=n.concept,
                node_type=n.node_type,
                parent_id=str(n.parent_id) if n.parent_id else None,
                parent_concept=_parent_concept(n),
                attractor=n.attractor,
                filter_id=n.filter_id,
                max_content_tokens=n.max_content_tokens,
                created_at=n.created_at,
                updated_at=n.updated_at,
                update_count=n.update_count,
                access_count=n.access_count,
                richness=_compute_richness(n),
                convergence_score=n.convergence_score,
                definition=n.definition,
                definition_generated_at=n.definition_generated_at.isoformat() if n.definition_generated_at else None,
                metadata=n.metadata_,
            )
            for n in nodes
        ],
        edges=[
            EdgeResponse(
                id=str(e.id),
                source_node_id=str(e.source_node_id),
                source_node_concept=nodes_by_id[e.source_node_id].concept if e.source_node_id in nodes_by_id else None,
                target_node_id=str(e.target_node_id),
                target_node_concept=nodes_by_id[e.target_node_id].concept if e.target_node_id in nodes_by_id else None,
                relationship_type=e.relationship_type,
                weight=e.weight,
                justification=e.justification,
                supporting_fact_ids=[str(ef.fact_id) for ef in e.edge_facts],
                created_at=e.created_at,
            )
            for e in edges
        ],
    )


@router.get("/paths", response_model=PathsResponse)
async def get_paths(
    source: str = Query(..., description="Source node UUID"),
    target: str = Query(..., description="Target node UUID"),
    max_depth: int = Query(6, ge=1, le=10, description="Maximum path depth"),
    limit: int = Query(5, ge=1, le=20, description="Maximum number of paths"),
    session: AsyncSession = Depends(get_db_session),
) -> PathsResponse:
    """Find shortest paths between two nodes."""
    try:
        source_uuid = uuid.UUID(source)
        target_uuid = uuid.UUID(target)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid UUID format")

    engine = GraphEngine(session, qdrant_client=get_qdrant_client_cached())

    source_node = await engine.get_node(source_uuid)
    if source_node is None:
        raise HTTPException(status_code=404, detail=f"Source node not found: {source}")

    target_node = await engine.get_node(target_uuid)
    if target_node is None:
        raise HTTPException(status_code=404, detail=f"Target node not found: {target}")

    raw_paths = await engine.find_shortest_paths(
        source_uuid,
        target_uuid,
        max_depth=max_depth,
        limit=limit,
    )

    # Collect all unique node IDs to bulk-fetch concepts
    all_node_ids: set[uuid.UUID] = set()
    for path in raw_paths:
        for step in path:
            all_node_ids.add(step.node_id)

    nodes_by_id: dict[uuid.UUID, Node] = {}
    if all_node_ids:
        fetched = await engine.get_nodes_by_ids(list(all_node_ids))
        nodes_by_id = {n.id: n for n in fetched}

    paths: list[PathResponse] = []
    for raw_path in raw_paths:
        steps: list[PathStepResponse] = []
        for step in raw_path:
            node = nodes_by_id.get(step.node_id)
            edge_resp = None
            if step.edge is not None:
                edge_resp = EdgeResponse(
                    id=str(step.edge.id),
                    source_node_id=str(step.edge.source_node_id),
                    source_node_concept=nodes_by_id.get(step.edge.source_node_id, None)
                    and nodes_by_id[step.edge.source_node_id].concept,
                    target_node_id=str(step.edge.target_node_id),
                    target_node_concept=nodes_by_id.get(step.edge.target_node_id, None)
                    and nodes_by_id[step.edge.target_node_id].concept,
                    relationship_type=step.edge.relationship_type,
                    weight=step.edge.weight,
                    justification=step.edge.justification,
                    created_at=step.edge.created_at,
                )
            steps.append(
                PathStepResponse(
                    node_id=str(step.node_id),
                    node_concept=node.concept if node else "Unknown",
                    node_type=node.node_type if node else "concept",
                    edge=edge_resp,
                )
            )
        paths.append(PathResponse(steps=steps, length=len(raw_path) - 1))

    return PathsResponse(
        source_id=str(source_uuid),
        target_id=str(target_uuid),
        paths=paths,
        total_found=len(paths),
        max_depth=max_depth,
        truncated=len(raw_paths) >= limit,
    )
