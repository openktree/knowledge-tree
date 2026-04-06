"""Graph-scoped node endpoints.

Mirrors /api/v1/nodes (read endpoints) scoped to a specific graph via
/api/v1/graphs/{graph_slug}/nodes. Uses GraphContext for session routing.
"""

from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from kt_api.dependencies import get_qdrant_client_cached
from kt_api.graph_context import GraphContext, get_graph_context
from kt_api.nodes import (
    _batch_parent_concepts,
    _build_node_response,
    _dedupe_sources,
)
from kt_api.schemas import (
    ConvergenceResponse,
    DimensionResponse,
    EdgeResponse,
    FactResponse,
    NodeResponse,
    NodeVersionResponse,
    PaginatedNodesResponse,
)
from kt_db.keys import key_to_uuid, url_key_to_node_key
from kt_db.models import Node
from kt_graph.convergence import compute_convergence
from kt_graph.read_engine import ReadGraphEngine

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/graphs/{graph_slug}/nodes", tags=["graph-nodes"])


# ── Helpers ────────────────────────────────────────────────────────


async def _batch_seed_fact_counts_graph(
    nodes: list[Node],
    write_session_factory: async_sessionmaker,
) -> dict[uuid.UUID, int]:
    """Look up seed fact counts from a graph-scoped write-db."""
    if not nodes:
        return {}
    from kt_db.keys import make_node_key
    from kt_db.write_models import WriteSeed

    key_to_id: dict[str, uuid.UUID] = {}
    for n in nodes:
        nk = make_node_key(n.node_type, n.concept)
        key_to_id[nk] = n.id

    keys = list(key_to_id.keys())
    result: dict[uuid.UUID, int] = {}

    async with write_session_factory() as ws:
        stmt = select(WriteSeed.promoted_node_key, WriteSeed.fact_count).where(WriteSeed.promoted_node_key.in_(keys))
        rows = await ws.execute(stmt)
        for row in rows.all():
            node_id = key_to_id.get(row.promoted_node_key)
            if node_id is not None:
                result[node_id] = result.get(node_id, 0) + row.fact_count
    return result


def _parse_node_id(node_id: str) -> uuid.UUID:
    """Parse a node ID string to UUID, supporting both UUID and URL-key formats."""
    try:
        return uuid.UUID(node_id)
    except ValueError:
        return key_to_uuid(url_key_to_node_key(node_id))


# ── List + Get ─────────────────────────────────────────────────────


@router.get("", response_model=PaginatedNodesResponse)
async def list_graph_nodes(
    ctx: GraphContext = Depends(get_graph_context),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    node_type: str | None = Query(default=None),
    search: str | None = Query(default=None),
    sort: str = Query("updated_at", description="Sort order: updated_at, edge_count, fact_count"),
) -> PaginatedNodesResponse:
    """List nodes in a specific graph."""
    async with ctx.graph_session_factory() as session:
        engine = ReadGraphEngine(session=session, qdrant_client=get_qdrant_client_cached())
        nodes = await engine.list_nodes(offset=offset, limit=limit, search=search, node_type=node_type, sort=sort)
        total = await engine.count_nodes(search=search, node_type=node_type)
        parent_map = await _batch_parent_concepts(session, nodes)
        seed_fc = await _batch_seed_fact_counts_graph(nodes, ctx.write_session_factory)
        items = [_build_node_response(n, parent_map, seed_fc) for n in nodes]
        return PaginatedNodesResponse(items=items, total=total, offset=offset, limit=limit)


@router.get("/{node_id}", response_model=NodeResponse)
async def get_graph_node(
    node_id: str,
    ctx: GraphContext = Depends(get_graph_context),
) -> NodeResponse:
    """Get a single node from a specific graph."""
    uid = _parse_node_id(node_id)
    async with ctx.graph_session_factory() as session:
        engine = ReadGraphEngine(session=session, qdrant_client=get_qdrant_client_cached())
        node = await engine.get_node(uid)
        if node is None:
            raise HTTPException(status_code=404, detail="Node not found")
        await engine.increment_access_count(uid)
        await session.commit()
        parent_map = await _batch_parent_concepts(session, [node])
        seed_fc = await _batch_seed_fact_counts_graph([node], ctx.write_session_factory)
        return _build_node_response(node, parent_map, seed_fc)


# ── Sub-resources ──────────────────────────────────────────────────


@router.get("/{node_id}/dimensions", response_model=list[DimensionResponse])
async def get_graph_node_dimensions(
    node_id: str,
    ctx: GraphContext = Depends(get_graph_context),
) -> list[DimensionResponse]:
    """Get all dimensions for a node in a specific graph."""
    uid = _parse_node_id(node_id)
    async with ctx.graph_session_factory() as session:
        engine = ReadGraphEngine(session=session, qdrant_client=get_qdrant_client_cached())
        node = await engine.get_node(uid)
        if not node:
            raise HTTPException(status_code=404, detail="Node not found")
        dims = await engine.get_dimensions(uid)
        return [
            DimensionResponse(
                id=str(d.id),
                node_id=str(d.node_id),
                model_id=d.model_id,
                content=d.content,
                confidence=d.confidence,
                suggested_concepts=d.suggested_concepts,
                generated_at=d.generated_at,
                batch_index=d.batch_index,
                fact_count=d.fact_count,
                is_definitive=d.is_definitive,
            )
            for d in dims
        ]


@router.get("/{node_id}/facts", response_model=list[FactResponse])
async def get_graph_node_facts(
    node_id: str,
    ctx: GraphContext = Depends(get_graph_context),
) -> list[FactResponse]:
    """Get all facts linked to a node in a specific graph."""
    uid = _parse_node_id(node_id)
    async with ctx.graph_session_factory() as session:
        engine = ReadGraphEngine(session=session, qdrant_client=get_qdrant_client_cached())
        node = await engine.get_node(uid)
        if not node:
            raise HTTPException(status_code=404, detail="Node not found")
        facts = await engine.get_node_facts_with_sources(uid)
        return [
            FactResponse(
                id=str(f.id),
                content=f.content,
                fact_type=f.fact_type,
                metadata=f.metadata_,
                created_at=f.created_at,
                sources=_dedupe_sources(f.sources),
            )
            for f in facts
        ]


@router.get("/{node_id}/edges", response_model=list[EdgeResponse])
async def get_graph_node_edges(
    node_id: str,
    direction: str = Query("both", description="Edge direction: outgoing, incoming, or both"),
    ctx: GraphContext = Depends(get_graph_context),
) -> list[EdgeResponse]:
    """Get all edges connected to a node in a specific graph."""
    uid = _parse_node_id(node_id)
    async with ctx.graph_session_factory() as session:
        engine = ReadGraphEngine(session=session, qdrant_client=get_qdrant_client_cached())
        node = await engine.get_node(uid)
        if not node:
            raise HTTPException(status_code=404, detail="Node not found")
        edges = await engine.get_edges(uid, direction=direction)
        results: list[EdgeResponse] = []
        for e in edges:
            fact_ids: list[str] = []
            if hasattr(e, "edge_facts") and e.edge_facts:
                fact_ids = [str(ef.fact_id) for ef in e.edge_facts]
            results.append(
                EdgeResponse(
                    id=str(e.id),
                    source_node_id=str(e.source_node_id),
                    target_node_id=str(e.target_node_id),
                    relationship_type=e.relationship_type,
                    weight=e.weight,
                    justification=e.justification,
                    weight_source=e.weight_source,
                    supporting_fact_ids=fact_ids,
                    created_at=e.created_at,
                )
            )
        return results


@router.get("/{node_id}/history", response_model=list[NodeVersionResponse])
async def get_graph_node_history(
    node_id: str,
    ctx: GraphContext = Depends(get_graph_context),
) -> list[NodeVersionResponse]:
    """Get version history for a node in a specific graph."""
    uid = _parse_node_id(node_id)
    async with ctx.graph_session_factory() as session:
        engine = ReadGraphEngine(session=session, qdrant_client=get_qdrant_client_cached())
        node = await engine.get_node(uid)
        if not node:
            raise HTTPException(status_code=404, detail="Node not found")
        versions = await engine.get_node_history(uid)
        return [
            NodeVersionResponse(
                id=str(v.id),
                version_number=v.version_number,
                snapshot=v.snapshot,
                source_node_count=getattr(v, "source_node_count", 0) or 0,
                is_default=getattr(v, "is_default", False) or False,
                created_at=v.created_at,
            )
            for v in versions
        ]


@router.get("/{node_id}/convergence", response_model=ConvergenceResponse)
async def get_graph_node_convergence(
    node_id: str,
    ctx: GraphContext = Depends(get_graph_context),
) -> ConvergenceResponse:
    """Compute convergence analysis for a node in a specific graph."""
    uid = _parse_node_id(node_id)
    async with ctx.graph_session_factory() as session:
        engine = ReadGraphEngine(session=session, qdrant_client=get_qdrant_client_cached())
        node = await engine.get_node(uid)
        if not node:
            raise HTTPException(status_code=404, detail="Node not found")
        dims = await engine.get_dimensions(uid)
        result = compute_convergence(dims)
        return ConvergenceResponse(
            convergence_score=result["convergence_score"],
            converged_claims=result["converged_claims"],
            divergent_claims=result["divergent_claims"],
            recommended_content=result["recommended_content"],
        )


@router.get("/{node_id}/perspectives", response_model=list[NodeResponse])
async def get_graph_node_perspectives(
    node_id: str,
    ctx: GraphContext = Depends(get_graph_context),
) -> list[NodeResponse]:
    """Get all perspective nodes for a concept node in a specific graph."""
    uid = _parse_node_id(node_id)
    async with ctx.graph_session_factory() as session:
        engine = ReadGraphEngine(session=session, qdrant_client=get_qdrant_client_cached())
        node = await engine.get_node(uid)
        if not node:
            raise HTTPException(status_code=404, detail="Node not found")
        perspectives = await engine.get_perspectives(uid)
        parent_map = await _batch_parent_concepts(session, perspectives)
        seed_fc = await _batch_seed_fact_counts_graph(perspectives, ctx.write_session_factory)
        return [_build_node_response(p, parent_map, seed_fc) for p in perspectives]


@router.get("/{node_id}/children", response_model=list[NodeResponse])
async def get_graph_node_children(
    node_id: str,
    ctx: GraphContext = Depends(get_graph_context),
) -> list[NodeResponse]:
    """Get all child nodes in a specific graph."""
    uid = _parse_node_id(node_id)
    async with ctx.graph_session_factory() as session:
        result = await session.execute(select(Node).where(Node.parent_id == uid))
        children = list(result.scalars().all())
        if not children:
            return []
        parent_map = await _batch_parent_concepts(session, children)
        seed_fc = await _batch_seed_fact_counts_graph(children, ctx.write_session_factory)
        return [_build_node_response(c, parent_map, seed_fc) for c in children]
