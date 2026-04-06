"""Node read endpoints."""

from __future__ import annotations

import logging
import uuid

from kt_db.keys import key_to_uuid, make_url_key, url_key_to_node_key

logger = logging.getLogger(__name__)

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from kt_api.auth.tokens import require_auth
from kt_api.dependencies import get_db_session, get_qdrant_client_cached, require_api_key
from kt_api.schemas import (
    ConvergenceResponse,
    DeleteResponse,
    DimensionResponse,
    EdgeResponse,
    FactResponse,
    FactSourceInfo,
    NodeResponse,
    NodeUpdateRequest,
    NodeVersionResponse,
    PaginatedNodesResponse,
    QuickAddNodeRequest,
    QuickAddNodeResponse,
    QuickPerspectiveRequest,
    QuickPerspectiveResponse,
    QuickPerspectiveValidateResponse,
)
from kt_config.settings import get_settings
from kt_db.models import FactSource, Node, NodeFact, User
from kt_graph.convergence import compute_convergence
from kt_graph.read_engine import ReadGraphEngine

router = APIRouter(prefix="/api/v1/nodes", tags=["nodes"])


def _dedupe_sources(sources: list[FactSource]) -> list[FactSourceInfo]:
    """Deduplicate FactSources by URI, keeping the first occurrence."""
    seen: set[str] = set()
    result: list[FactSourceInfo] = []
    for fs in sources:
        uri = fs.raw_source.uri
        if uri in seen:
            continue
        seen.add(uri)
        result.append(
            FactSourceInfo(
                source_id=str(fs.raw_source.id),
                uri=uri,
                title=fs.raw_source.title,
                provider_id=fs.raw_source.provider_id,
                retrieved_at=fs.raw_source.retrieved_at,
                context_snippet=fs.context_snippet,
                attribution=fs.attribution,
                author_person=fs.author_person,
                author_org=fs.author_org,
                provider_metadata=fs.raw_source.provider_metadata,
            )
        )
    return result


async def _batch_parent_concepts(session: AsyncSession, nodes: list[Node]) -> dict[uuid.UUID, tuple[str, str]]:
    """Fetch parent (concept, node_type) for a batch of nodes in a single query."""
    parent_ids = [n.parent_id for n in nodes if n.parent_id is not None]
    if not parent_ids:
        return {}
    unique_ids = list(set(parent_ids))
    stmt = select(Node.id, Node.concept, Node.node_type).where(Node.id.in_(unique_ids))
    result = await session.execute(stmt)
    return {row.id: (row.concept, row.node_type) for row in result.all()}


def _compute_richness(n: Node) -> float:
    """Compute richness from denormalized columns (no DB query needed)."""
    raw = n.fact_count * 0.1 + n.dimension_count * 0.2 + n.access_count * 0.01
    return min(1.0, raw)


async def _batch_seed_fact_counts(
    nodes: list[Node],
) -> dict[uuid.UUID, int]:
    """Look up seed fact counts from write-db for promoted nodes.

    Returns a map of node_id -> seed_fact_count.
    """
    if not nodes:
        return {}

    from kt_api.dependencies import get_write_session_factory_cached
    from kt_db.keys import make_node_key
    from kt_db.write_models import WriteSeed

    # Build node_key -> node_id mapping
    key_to_id: dict[str, uuid.UUID] = {}
    for n in nodes:
        nk = make_node_key(n.node_type, n.concept)
        key_to_id[nk] = n.id

    keys = list(key_to_id.keys())
    result: dict[uuid.UUID, int] = {}

    write_sf = get_write_session_factory_cached()
    async with write_sf() as ws:
        stmt = select(WriteSeed.promoted_node_key, WriteSeed.fact_count).where(WriteSeed.promoted_node_key.in_(keys))
        rows = await ws.execute(stmt)
        for row in rows.all():
            node_id = key_to_id.get(row.promoted_node_key)
            if node_id is not None:
                # A node can have multiple seeds promoted to it (merges);
                # sum their fact counts
                result[node_id] = result.get(node_id, 0) + row.fact_count

    return result


def _build_node_response(
    n: Node,
    parent_map: dict[uuid.UUID, tuple[str, str]],
    seed_fact_count_map: dict[uuid.UUID, int] | None = None,
) -> NodeResponse:
    """Build a NodeResponse from a Node model using denormalized columns."""
    parent_info = parent_map.get(n.parent_id) if n.parent_id else None
    parent_concept = parent_info[0] if parent_info else None
    parent_key = make_url_key(parent_info[1], parent_info[0]) if parent_info else None
    sfc = seed_fact_count_map.get(n.id, 0) if seed_fact_count_map else 0
    return NodeResponse(
        id=str(n.id),
        concept=n.concept,
        node_type=n.node_type,
        key=make_url_key(n.node_type, n.concept),
        entity_subtype=n.entity_subtype,
        parent_id=str(n.parent_id) if n.parent_id else None,
        parent_concept=parent_concept,
        parent_key=parent_key,
        attractor=n.attractor,
        filter_id=n.filter_id,
        max_content_tokens=n.max_content_tokens,
        created_at=n.created_at,
        updated_at=n.updated_at,
        update_count=n.update_count,
        access_count=n.access_count,
        edge_count=n.edge_count,
        child_count=n.child_count,
        fact_count=n.fact_count,
        seed_fact_count=sfc,
        pending_facts=max(0, sfc - n.fact_count),
        richness=_compute_richness(n),
        convergence_score=n.convergence_score,
        definition=n.definition,
        definition_source=n.definition_source,
        definition_generated_at=n.definition_generated_at.isoformat() if n.definition_generated_at else None,
        enrichment_status=n.enrichment_status,
        metadata=_strip_synthesis_doc(n.metadata_),
    )


def _strip_synthesis_doc(meta: dict | None) -> dict | None:
    """Remove the synthesis_document blob from metadata for normal node responses.

    The synthesis document is large (~50KB) and only needed by the
    /syntheses/{id} endpoint. Stripping it avoids sending it over the
    network for node list, detail, wiki, and search responses.
    """
    if not meta or "synthesis_document" not in meta:
        return meta
    filtered = {k: v for k, v in meta.items() if k != "synthesis_document"}
    return filtered or None


async def _list_nodes_by_pending_facts(
    session: AsyncSession,
    offset: int,
    limit: int,
    search: str | None,
    node_type: str | None,
) -> PaginatedNodesResponse:
    """List nodes sorted by pending facts (seed_fact_count - node_fact_count).

    Since pending facts span two databases, we compute it in two phases:
    1. Get all matching node IDs + their NodeFact counts from graph-db,
       and seed fact counts from write-db.
    2. Sort by pending DESC, paginate, then load full data for the page.
    """
    from kt_api.dependencies import get_write_session_factory_cached
    from kt_db.keys import make_node_key
    from kt_db.write_models import WriteSeed

    # Phase 1: Get all matching node IDs with minimal data
    stmt = select(Node.id, Node.concept, Node.node_type)
    if search:
        stmt = stmt.where(Node.concept.ilike(f"%{search}%"))
    if node_type:
        stmt = stmt.where(Node.node_type == node_type)
    result = await session.execute(stmt)
    all_rows = result.all()

    if not all_rows:
        return PaginatedNodesResponse(items=[], total=0, offset=offset, limit=limit)

    all_ids = [r.id for r in all_rows]
    total = len(all_ids)

    # Node fact counts from graph-db (batch)
    fact_stmt = (
        select(NodeFact.node_id, func.count(NodeFact.fact_id))
        .where(NodeFact.node_id.in_(all_ids))
        .group_by(NodeFact.node_id)
    )
    fact_result = await session.execute(fact_stmt)
    node_fc: dict[uuid.UUID, int] = {r[0]: r[1] for r in fact_result.all()}

    # Seed fact counts from write-db
    key_to_id: dict[str, uuid.UUID] = {}
    for r in all_rows:
        key_to_id[make_node_key(r.node_type, r.concept)] = r.id

    seed_fc: dict[uuid.UUID, int] = {}
    write_sf = get_write_session_factory_cached()
    async with write_sf() as ws:
        seed_stmt = select(WriteSeed.promoted_node_key, WriteSeed.fact_count).where(
            WriteSeed.promoted_node_key.in_(list(key_to_id.keys()))
        )
        seed_rows = await ws.execute(seed_stmt)
        for sr in seed_rows.all():
            nid = key_to_id.get(sr.promoted_node_key)
            if nid is not None:
                seed_fc[nid] = seed_fc.get(nid, 0) + sr.fact_count

    # Sort all node IDs by pending (seed - node) DESC
    def pending(nid: uuid.UUID) -> int:
        return max(0, seed_fc.get(nid, 0) - node_fc.get(nid, 0))

    sorted_ids = sorted(all_ids, key=pending, reverse=True)
    page_ids = sorted_ids[offset : offset + limit]

    if not page_ids:
        return PaginatedNodesResponse(items=[], total=total, offset=offset, limit=limit)

    # Phase 2: Load full Node rows for the page
    nodes_stmt = select(Node).where(Node.id.in_(page_ids))
    nodes_result = await session.execute(nodes_stmt)
    nodes_by_id = {n.id: n for n in nodes_result.scalars().all()}
    # Preserve sort order
    nodes = [nodes_by_id[nid] for nid in page_ids if nid in nodes_by_id]

    # Build responses using denormalized columns
    parent_map = await _batch_parent_concepts(session, nodes)
    seed_fact_count_map = seed_fc

    items = [_build_node_response(n, parent_map, seed_fact_count_map) for n in nodes]
    return PaginatedNodesResponse(items=items, total=total, offset=offset, limit=limit)


@router.get("", response_model=PaginatedNodesResponse)
async def list_nodes(
    offset: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    search: str | None = Query(None, description="Search by concept name"),
    node_type: str | None = Query(None, description="Filter by node type: concept, perspective, entity, event"),
    sort: str = Query("updated_at", description="Sort order: updated_at, edge_count, fact_count, or pending_facts"),
    session: AsyncSession = Depends(get_db_session),
) -> PaginatedNodesResponse:
    """List nodes with pagination, optional search, and optional node_type filter."""
    if sort == "pending_facts":
        return await _list_nodes_by_pending_facts(session, offset, limit, search, node_type)

    from kt_config.cache import cache_get, cache_set, make_cache_key

    cache_key = make_cache_key("nodes:list", offset=offset, limit=limit, search=search, node_type=node_type, sort=sort)
    cached = await cache_get(cache_key)
    if cached is not None:
        return PaginatedNodesResponse(**cached)

    engine = ReadGraphEngine(session=session, qdrant_client=get_qdrant_client_cached())
    nodes = await engine.list_nodes(offset=offset, limit=limit, search=search, node_type=node_type, sort=sort)
    total = await engine.count_nodes(search=search, node_type=node_type)
    parent_map = await _batch_parent_concepts(session, nodes)
    seed_fact_count_map = await _batch_seed_fact_counts(nodes)
    items = [_build_node_response(n, parent_map, seed_fact_count_map) for n in nodes]
    result = PaginatedNodesResponse(items=items, total=total, offset=offset, limit=limit)
    await cache_set(cache_key, result.model_dump(), ttl=30)
    return result


@router.get("/search", response_model=list[NodeResponse])
async def search_nodes(
    query: str = Query(..., description="Search term for concept names"),
    limit: int = Query(10, ge=1, le=100),
    session: AsyncSession = Depends(get_db_session),
) -> list[NodeResponse]:
    """Search nodes by concept name (text search)."""
    engine = ReadGraphEngine(session=session, qdrant_client=get_qdrant_client_cached())
    nodes = await engine.search_nodes(query, limit=limit)
    parent_map = await _batch_parent_concepts(session, nodes)
    seed_fact_count_map = await _batch_seed_fact_counts(nodes)
    return [_build_node_response(n, parent_map, seed_fact_count_map) for n in nodes]


@router.get("/{node_id}", response_model=NodeResponse)
async def get_node(
    node_id: str,
    session: AsyncSession = Depends(get_db_session),
) -> NodeResponse:
    """Get a single node by ID.

    Not cached: access_count is incremented on every read, and caching would
    serve stale counts while silently skipping increments for cache hits.
    Denormalized counters on the nodes table already keep this query fast.
    """
    engine = ReadGraphEngine(session=session, qdrant_client=get_qdrant_client_cached())
    try:
        uid = uuid.UUID(node_id)
    except ValueError:
        # Support both canonical keys (concept:ai) and URL-friendly keys (concept-ai)
        real_key = url_key_to_node_key(node_id)
        uid = key_to_uuid(real_key)

    node = await engine.get_node(uid)
    if not node:
        raise HTTPException(status_code=404, detail="Node not found")
    await engine.increment_access_count(uid)
    await session.commit()
    parent_map = await _batch_parent_concepts(session, [node])
    seed_fact_count_map = await _batch_seed_fact_counts([node])
    return _build_node_response(node, parent_map, seed_fact_count_map)


@router.patch("/{node_id}", response_model=NodeResponse)
async def update_node(
    node_id: str,
    body: NodeUpdateRequest,
    session: AsyncSession = Depends(get_db_session),
) -> NodeResponse:
    """Update a node's editable fields."""
    engine = ReadGraphEngine(session=session, qdrant_client=get_qdrant_client_cached())
    try:
        uid = uuid.UUID(node_id)
    except ValueError:
        uid = key_to_uuid(url_key_to_node_key(node_id))
    fields = body.model_dump(exclude_unset=True)
    if not fields:
        raise HTTPException(status_code=400, detail="No fields to update")
    try:
        node = await engine.update_node(uid, **fields)
    except ValueError:
        raise HTTPException(status_code=404, detail="Node not found")
    await engine.increment_update_count(uid)
    await session.commit()

    from kt_config.cache import cache_invalidate

    await cache_invalidate(f"kt:node:{uid}*")
    await cache_invalidate("kt:nodes:list:*")

    parent_map = await _batch_parent_concepts(session, [node])
    seed_fact_count_map = await _batch_seed_fact_counts([node])
    return _build_node_response(node, parent_map, seed_fact_count_map)


@router.delete("/{node_id}", response_model=DeleteResponse)
async def delete_node(
    node_id: str,
    session: AsyncSession = Depends(get_db_session),
) -> DeleteResponse:
    """Delete a node and its edges, dimensions, versions (but not linked facts)."""
    engine = ReadGraphEngine(session=session, qdrant_client=get_qdrant_client_cached())
    try:
        uid = uuid.UUID(node_id)
    except ValueError:
        uid = key_to_uuid(url_key_to_node_key(node_id))
    deleted = await engine.delete_node(uid)
    if not deleted:
        raise HTTPException(status_code=404, detail="Node not found")
    await session.commit()

    from kt_config.cache import cache_invalidate

    await cache_invalidate(f"kt:node:{uid}*")
    await cache_invalidate("kt:nodes:list:*")
    await cache_invalidate("kt:graph:subgraph:*")

    return DeleteResponse(deleted=True, id=node_id)


@router.get("/{node_id}/dimensions", response_model=list[DimensionResponse])
async def get_node_dimensions(
    node_id: str,
    session: AsyncSession = Depends(get_db_session),
) -> list[DimensionResponse]:
    """Get all dimensions (model perspectives) for a node."""
    engine = ReadGraphEngine(session=session, qdrant_client=get_qdrant_client_cached())
    try:
        uid = uuid.UUID(node_id)
    except ValueError:
        uid = key_to_uuid(url_key_to_node_key(node_id))
    from kt_config.cache import cache_get, cache_set

    cache_key = f"kt:node:{uid}:dimensions"
    cached = await cache_get(cache_key)
    if cached is not None:
        return [DimensionResponse(**d) for d in cached]

    node = await engine.get_node(uid)
    if not node:
        raise HTTPException(status_code=404, detail="Node not found")
    dims = await engine.get_dimensions(uid)
    result = [
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
    await cache_set(cache_key, [r.model_dump() for r in result], ttl=60)
    return result


@router.get("/{node_id}/facts", response_model=list[FactResponse])
async def get_node_facts(
    node_id: str,
    session: AsyncSession = Depends(get_db_session),
) -> list[FactResponse]:
    """Get all facts linked to a node."""
    engine = ReadGraphEngine(session=session, qdrant_client=get_qdrant_client_cached())
    try:
        uid = uuid.UUID(node_id)
    except ValueError:
        uid = key_to_uuid(url_key_to_node_key(node_id))
    from kt_config.cache import cache_get, cache_set

    cache_key = f"kt:node:{uid}:facts"
    cached = await cache_get(cache_key)
    if cached is not None:
        return [FactResponse(**f) for f in cached]

    node = await engine.get_node(uid)
    if not node:
        raise HTTPException(status_code=404, detail="Node not found")
    facts = await engine.get_node_facts_with_sources(uid)
    result = [
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
    await cache_set(cache_key, [r.model_dump() for r in result], ttl=60)
    return result


@router.post("/{node_id}/rebuild")
async def rebuild_node(
    node_id: str,
    body: dict | None = None,
    user: User = Depends(require_auth),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, str]:
    """Rebuild a node's dimensions, edges, definition, and ancestry.

    Accepts optional JSON body ``{"mode": "full"|"incremental", "scope": "all"|"dimensions"|"edges"}``.
    Defaults to ``mode="full", scope="all"`` (full rebuild).
    """
    engine = ReadGraphEngine(session=session, qdrant_client=get_qdrant_client_cached())
    try:
        uid = uuid.UUID(node_id)
    except ValueError:
        uid = key_to_uuid(url_key_to_node_key(node_id))
    node = await engine.get_node(uid)
    if not node:
        raise HTTPException(status_code=404, detail="Node not found")

    from kt_api.dispatch import dispatch_with_graph

    raw_mode = (body or {}).get("mode", "full")
    pipeline_mode = f"rebuild_{raw_mode}" if raw_mode in ("full", "incremental") else "rebuild_full"
    scope = (body or {}).get("scope", "all")
    require_api_key(user)  # fail-fast validation
    await dispatch_with_graph(
        "node_pipeline",
        {
            "mode": pipeline_mode,
            "node_id": node_id,
            "scope": scope,
            "recalculate_pair": True,
            "user_id": str(user.id),
        },
    )

    from kt_config.cache import cache_invalidate

    await cache_invalidate(f"kt:node:{uid}*")
    await cache_invalidate("kt:nodes:list:*")

    return {"status": "started", "node_id": node_id}


@router.post("/{node_id}/recalculate-node")
async def recalculate_node(
    node_id: str,
    user: User = Depends(require_auth),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, str]:
    """Legacy endpoint — redirects to rebuild_node with mode=full, scope=all."""
    return await rebuild_node(node_id, {"mode": "full", "scope": "all"}, user, session)


@router.get("/{node_id}/edges", response_model=list[EdgeResponse])
async def get_node_edges(
    node_id: str,
    direction: str = Query("both", description="Edge direction: outgoing, incoming, or both"),
    session: AsyncSession = Depends(get_db_session),
) -> list[EdgeResponse]:
    """Get all edges connected to a node."""
    engine = ReadGraphEngine(session=session, qdrant_client=get_qdrant_client_cached())
    try:
        uid = uuid.UUID(node_id)
    except ValueError:
        uid = key_to_uuid(url_key_to_node_key(node_id))
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
async def get_node_history(
    node_id: str,
    session: AsyncSession = Depends(get_db_session),
) -> list[NodeVersionResponse]:
    """Get the version history for a node."""
    engine = ReadGraphEngine(session=session, qdrant_client=get_qdrant_client_cached())
    try:
        uid = uuid.UUID(node_id)
    except ValueError:
        uid = key_to_uuid(url_key_to_node_key(node_id))
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
async def get_node_convergence(
    node_id: str,
    session: AsyncSession = Depends(get_db_session),
) -> ConvergenceResponse:
    """Compute convergence analysis across model dimensions for a node."""
    engine = ReadGraphEngine(session=session, qdrant_client=get_qdrant_client_cached())
    try:
        uid = uuid.UUID(node_id)
    except ValueError:
        uid = key_to_uuid(url_key_to_node_key(node_id))
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
async def get_node_perspectives(
    node_id: str,
    session: AsyncSession = Depends(get_db_session),
) -> list[NodeResponse]:
    """Get all perspective nodes for a concept node."""
    engine = ReadGraphEngine(session=session, qdrant_client=get_qdrant_client_cached())
    try:
        uid = uuid.UUID(node_id)
    except ValueError:
        uid = key_to_uuid(url_key_to_node_key(node_id))
    node = await engine.get_node(uid)
    if not node:
        raise HTTPException(status_code=404, detail="Node not found")
    perspectives = await engine.get_perspectives(uid)
    parent_map = await _batch_parent_concepts(session, perspectives)
    seed_fact_count_map = await _batch_seed_fact_counts(perspectives)
    return [_build_node_response(p, parent_map, seed_fact_count_map) for p in perspectives]


@router.get("/{node_id}/children", response_model=list[NodeResponse])
async def get_node_children(
    node_id: str,
    session: AsyncSession = Depends(get_db_session),
) -> list[NodeResponse]:
    """Get all nodes whose parent_id equals this node."""
    try:
        uid = uuid.UUID(node_id)
    except ValueError:
        uid = key_to_uuid(url_key_to_node_key(node_id))
    result = await session.execute(select(Node).where(Node.parent_id == uid))
    children = list(result.scalars().all())
    if not children:
        return []
    parent_map = await _batch_parent_concepts(session, children)
    seed_fact_count_map = await _batch_seed_fact_counts(children)
    return [_build_node_response(c, parent_map, seed_fact_count_map) for c in children]


# ── On-demand enrichment ────────────────────────────────────────────────


@router.post("/{node_id}/enrich")
async def enrich_node(
    node_id: str,
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, str]:
    """Legacy endpoint — redirects to rebuild_node with mode=full, scope=all."""
    engine = ReadGraphEngine(session=session, qdrant_client=get_qdrant_client_cached())
    try:
        uid = uuid.UUID(node_id)
    except ValueError:
        uid = key_to_uuid(url_key_to_node_key(node_id))
    node = await engine.get_node(uid)
    if not node:
        raise HTTPException(status_code=404, detail="Node not found")

    from kt_api.dispatch import dispatch_with_graph

    await dispatch_with_graph("node_pipeline", {"mode": "rebuild_full", "node_id": node_id, "scope": "all"})
    return {"status": "started", "node_id": node_id}


# ── Quick actions ───────────────────────────────────────────────────────


@router.post("/quick-add", response_model=QuickAddNodeResponse)
async def quick_add_node(
    body: QuickAddNodeRequest,
    session: AsyncSession = Depends(get_db_session),
) -> QuickAddNodeResponse:
    """Add a node by concept name.

    If a node with a matching name already exists, trigger a full
    recalculate (refresh) instead.  New nodes are run through the full
    Hatchet node pipeline (create -> dimensions + edges + definition +
    ancestry + crystallization).  Costs 1 nav credit.
    """
    engine = ReadGraphEngine(session=session, qdrant_client=get_qdrant_client_cached())
    existing = await engine.search_nodes(body.concept.strip(), limit=1)

    # Exact-ish match: first result whose concept matches case-insensitively
    match = None
    for n in existing:
        if n.concept.lower() == body.concept.strip().lower():
            match = n
            break

    if match:
        # Trigger full rebuild via Hatchet
        from kt_api.dispatch import dispatch_with_graph

        await dispatch_with_graph(
            "node_pipeline",
            {
                "mode": "rebuild_full",
                "node_id": str(match.id),
                "scope": "all",
                "recalculate_pair": True,
            },
        )
        return QuickAddNodeResponse(
            status="started",
            action="refreshed",
            node_id=str(match.id),
            concept=match.concept,
        )

    # Trigger full node pipeline via Hatchet
    concept = body.concept.strip()
    # Generate a deterministic scope_id for tracking
    scope_id = f"quick-add-{uuid.uuid4().hex[:8]}"

    from kt_api.dependencies import get_write_session_factory_cached
    from kt_api.dispatch import dispatch_with_graph
    from kt_db.keys import make_seed_key
    from kt_db.repositories.write_seeds import WriteSeedRepository

    seed_key = make_seed_key("concept", concept)

    # Create seed in write-db (upsert — safe if it already exists)
    write_sf = get_write_session_factory_cached()
    async with write_sf() as ws:
        seed_repo = WriteSeedRepository(ws)
        await seed_repo.upsert_seed(seed_key, concept, "concept", None)
        await ws.commit()

    run_id = await dispatch_with_graph(
        "node_pipeline",
        {
            "scope_id": scope_id,
            "concept": concept,
            "node_type": "concept",
            "seed_key": seed_key,
            "message_id": scope_id,
            "conversation_id": scope_id,
        },
    )

    return QuickAddNodeResponse(
        status="started",
        action="created",
        node_id=run_id,
        concept=concept,
    )


@router.post("/quick-perspective/validate", response_model=QuickPerspectiveValidateResponse)
async def validate_perspective_pair(
    body: QuickPerspectiveRequest,
) -> QuickPerspectiveValidateResponse:
    """Validate that a thesis/antithesis pair forms a coherent dialectic.

    Uses a quick LLM call to check logical opposition.
    """
    from kt_models.gateway import ModelGateway

    settings = get_settings()
    gateway = ModelGateway()

    prompt = (
        "You are a logic validator. Given a thesis and an antithesis, determine whether "
        "the antithesis is a valid logical opposite, contradiction, or meaningful counter-position "
        "to the thesis. They must be about the same topic but hold genuinely opposing stances.\n\n"
        f"Thesis: {body.thesis}\n"
        f"Antithesis: {body.antithesis}\n\n"
        "Respond in exactly this JSON format (no markdown):\n"
        '{"valid": true/false, "feedback": "brief explanation"}'
    )

    try:
        response = await gateway.generate(
            model_id=settings.default_model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
            max_tokens=200,
        )

        import json

        # Try to parse JSON from response
        text = response.strip()
        # Handle potential markdown code fences
        if text.startswith("```"):
            text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()
        parsed = json.loads(text)
        return QuickPerspectiveValidateResponse(
            valid=bool(parsed.get("valid", False)),
            feedback=str(parsed.get("feedback", "No feedback provided")),
        )
    except Exception as e:
        logger.warning("LLM validation failed: %s", e)
        return QuickPerspectiveValidateResponse(
            valid=False,
            feedback=f"Validation failed: {e}",
        )


@router.post("/quick-perspective", response_model=QuickPerspectiveResponse)
async def quick_add_perspective(
    body: QuickPerspectiveRequest,
    session: AsyncSession = Depends(get_db_session),
) -> QuickPerspectiveResponse:
    """Create a thesis/antithesis perspective pair.

    Validates the antithesis via LLM first. If invalid, returns an error
    with feedback.  Costs 1 nav credit.
    """
    # Step 1: Validate
    validation = await validate_perspective_pair(body)
    if not validation.valid:
        return QuickPerspectiveResponse(
            status="rejected",
            thesis_concept=body.thesis,
            antithesis_concept=body.antithesis,
            validation=validation,
        )

    engine = ReadGraphEngine(session=session, qdrant_client=get_qdrant_client_cached())

    from kt_config.types import ALL_PERSPECTIVES_ID
    from kt_models.embeddings import EmbeddingService

    settings = get_settings()
    embedding_service = EmbeddingService() if settings.openrouter_api_key else None

    # Resolve parent concept if provided
    parent_id = ALL_PERSPECTIVES_ID
    if body.parent_concept:
        parent_nodes = await engine.search_nodes(body.parent_concept, limit=1)
        if parent_nodes:
            parent_id = parent_nodes[0].id

    # Create thesis
    thesis_embedding = await embedding_service.embed_text(body.thesis) if embedding_service else None
    thesis_node = await engine.create_node(
        concept=body.thesis,
        embedding=thesis_embedding,
        node_type="perspective",
        parent_id=ALL_PERSPECTIVES_ID,
        source_concept_id=parent_id,
        metadata_={"dialectic_role": "thesis"},
    )

    # Create antithesis
    anti_embedding = await embedding_service.embed_text(body.antithesis) if embedding_service else None
    anti_node = await engine.create_node(
        concept=body.antithesis,
        embedding=anti_embedding,
        node_type="perspective",
        parent_id=ALL_PERSPECTIVES_ID,
        source_concept_id=parent_id,
        metadata_={
            "dialectic_role": "antithesis",
            "dialectic_pair_id": str(thesis_node.id),
        },
    )

    # Update thesis with pair pointer
    await engine.update_node(
        thesis_node.id,
        metadata_={
            "dialectic_role": "thesis",
            "dialectic_pair_id": str(anti_node.id),
        },
    )

    # Create contradicts edge
    try:
        await engine.create_edge(
            thesis_node.id,
            anti_node.id,
            "contradicts",
            -0.7,
            justification=(f"Dialectic pair: thesis '{body.thesis}' vs antithesis '{body.antithesis}'"),
        )
    except Exception:
        logger.warning("Failed to create contradicts edge", exc_info=True)

    await session.commit()

    # Kick off full pipeline for both nodes via Hatchet
    from kt_api.dependencies import get_write_session_factory_cached as _gwsf
    from kt_api.dispatch import dispatch_with_graph
    from kt_db.keys import make_seed_key as _msk
    from kt_db.repositories.write_seeds import WriteSeedRepository as _WSR

    scope_id = f"quick-perspective-{uuid.uuid4().hex[:8]}"
    thesis_key = _msk("perspective", body.thesis)
    antithesis_key = _msk("perspective", body.antithesis)

    write_sf = _gwsf()
    async with write_sf() as ws:
        seed_repo = _WSR(ws)
        await seed_repo.upsert_seed(thesis_key, body.thesis, "perspective", None)
        await seed_repo.upsert_seed(antithesis_key, body.antithesis, "perspective", None)
        await ws.commit()

    try:
        import asyncio

        await asyncio.gather(
            dispatch_with_graph(
                "node_pipeline",
                {
                    "scope_id": scope_id,
                    "concept": body.thesis,
                    "node_type": "perspective",
                    "seed_key": thesis_key,
                    "message_id": scope_id,
                    "conversation_id": scope_id,
                },
            ),
            dispatch_with_graph(
                "node_pipeline",
                {
                    "scope_id": scope_id,
                    "concept": body.antithesis,
                    "node_type": "perspective",
                    "seed_key": antithesis_key,
                    "message_id": scope_id,
                    "conversation_id": scope_id,
                },
            ),
        )
    except Exception:
        logger.warning("Failed to trigger node pipeline for perspective pair", exc_info=True)

    return QuickPerspectiveResponse(
        status="created",
        thesis_id=str(thesis_node.id),
        antithesis_id=str(anti_node.id),
        thesis_concept=body.thesis,
        antithesis_concept=body.antithesis,
        validation=validation,
    )


# ── Composite node endpoints ─────────────────────────────────────────


@router.post("/{node_id}/regenerate")
async def regenerate_composite(
    node_id: str,
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, str]:
    """Regenerate a composite node (synthesis or perspective).

    Dispatches a Hatchet ``regenerate_composite`` task that re-runs the
    composite agent and saves a new version.
    """
    engine = ReadGraphEngine(session=session, qdrant_client=get_qdrant_client_cached())
    try:
        uid = uuid.UUID(node_id)
    except ValueError:
        uid = key_to_uuid(url_key_to_node_key(node_id))
    node = await engine.get_node(uid)
    if not node:
        raise HTTPException(status_code=404, detail="Node not found")

    from kt_config.types import COMPOSITE_NODE_TYPES

    if node.node_type not in COMPOSITE_NODE_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"Node type '{node.node_type}' is not a composite node. Only {sorted(COMPOSITE_NODE_TYPES)} can be regenerated.",
        )

    from kt_api.dispatch import dispatch_with_graph

    await dispatch_with_graph("regenerate_composite", {"node_id": node_id})
    return {"status": "started", "node_id": node_id}


@router.get("/{node_id}/source-nodes", response_model=list[NodeResponse])
async def get_source_nodes(
    node_id: str,
    session: AsyncSession = Depends(get_db_session),
) -> list[NodeResponse]:
    """Get the source nodes for a composite node (via draws_from edges)."""
    engine = ReadGraphEngine(session=session, qdrant_client=get_qdrant_client_cached())
    try:
        uid = uuid.UUID(node_id)
    except ValueError:
        uid = key_to_uuid(url_key_to_node_key(node_id))
    node = await engine.get_node(uid)
    if not node:
        raise HTTPException(status_code=404, detail="Node not found")

    # Find draws_from edges where this node is the source (composite draws from base nodes)
    edges = await engine.get_edges(uid, direction="outgoing")
    source_ids = [e.target_node_id for e in edges if e.relationship_type == "draws_from"]
    if not source_ids:
        return []

    # Fetch the source nodes
    source_nodes = []
    for sid in source_ids:
        n = await engine.get_node(sid)
        if n:
            source_nodes.append(n)

    if not source_nodes:
        return []

    parent_map = await _batch_parent_concepts(session, source_nodes)
    seed_fact_count_map = await _batch_seed_fact_counts(source_nodes)
    return [_build_node_response(n, parent_map, seed_fact_count_map) for n in source_nodes]
