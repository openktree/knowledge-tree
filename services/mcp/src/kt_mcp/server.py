"""Knowledge Tree MCP Server.

Exposes read-only tools for navigating the knowledge graph:
- search_graph: Find nodes by text search
- get_node: Load node core info (definition or fallback dimension)
- get_dimensions: Load dimensions for a node (paginated)
- get_edges: Load edges for a node (paginated, filterable)
- get_facts: Load facts for a node (grouped by source, paginated)
- get_fact_sources: Load sources for a node's facts
- search_facts: Search the global fact pool by text query
- get_node_paths: Find shortest paths between two nodes
"""

from __future__ import annotations

import logging
import uuid

from fastapi import FastAPI
from fastmcp import FastMCP
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from kt_db.models import EdgeFact, Fact, FactSource, Node, NodeFact, RawSource
from kt_graph.engine import GraphEngine
from kt_mcp.dependencies import (
    get_qdrant_client_cached,
    get_session_factory_cached,
)
from kt_mcp.oauth_provider import create_oauth_provider

logger = logging.getLogger(__name__)

mcp = FastMCP("Knowledge Tree", auth=create_oauth_provider())


def _extract_published_date(raw_source: RawSource) -> str | None:
    """Extract publication date from provider_metadata.

    Checks (in priority order):
    1. html_metadata.date — from full-text fetch via trafilatura
    2. date — from Serper search results
    3. age — from Brave search results (relative, e.g. "2 days ago")
    """
    meta = raw_source.provider_metadata
    if not isinstance(meta, dict):
        return None
    html_meta = meta.get("html_metadata")
    if isinstance(html_meta, dict) and html_meta.get("date"):
        return str(html_meta["date"])
    if meta.get("date"):
        return str(meta["date"])
    if meta.get("age"):
        return str(meta["age"])
    return None


# ── Helpers ──────────────────────────────────────────────────────────


def _parse_uuid(node_id: str) -> uuid.UUID | None:
    try:
        return uuid.UUID(node_id)
    except ValueError:
        return None


# ── Tool: search_graph ───────────────────────────────────────────────


@mcp.tool()
async def search_graph(
    query: str,
    limit: int = 20,
    node_type: str | None = None,
) -> dict:
    """Search the knowledge graph for nodes matching a text query.

    Returns lightweight results with node ID, concept name, type, and
    fact count. Use get_node to load full details for nodes you care about.

    Args:
        query: Search term for concept names.
        limit: Max results (1-100, default 20).
        node_type: Optional filter: concept, entity, perspective, event.
    """
    limit = max(1, min(100, limit))
    factory = get_session_factory_cached()
    async with factory() as session:
        engine = GraphEngine(session, qdrant_client=get_qdrant_client_cached())
        nodes = await engine.search_nodes(query, limit=limit, node_type=node_type)
        if not nodes:
            return {"nodes": [], "total": 0}

        node_ids = [n.id for n in nodes]

        # Batch fact counts
        fact_stmt = (
            select(NodeFact.node_id, func.count(NodeFact.fact_id))
            .where(NodeFact.node_id.in_(node_ids))
            .group_by(NodeFact.node_id)
        )
        fact_result = await session.execute(fact_stmt)
        fact_counts = {row[0]: row[1] for row in fact_result.all()}

        items = []
        for n in nodes:
            meta = n.metadata_ or {}
            aliases = meta.get("aliases", [])
            merged_from = meta.get("merged_from", [])
            also_known_as = list({*aliases, *merged_from}) if aliases or merged_from else []
            item: dict = {
                "node_id": str(n.id),
                "concept": n.concept,
                "node_type": n.node_type,
                "fact_count": fact_counts.get(n.id, 0),
            }
            if also_known_as:
                item["also_known_as"] = also_known_as
            items.append(item)
        return {"nodes": items, "total": len(items)}


# ── Tool: get_node ───────────────────────────────────────────────────


@mcp.tool()
async def get_node(node_id: str) -> dict:
    """Load a node's core details.

    Returns concept, type, definition, parent, creation date, and counts.
    If the node has no definition, a single dimension is included as a
    fallback so there is always some descriptive content.

    Args:
        node_id: UUID of the node to load.
    """
    uid = _parse_uuid(node_id)
    if uid is None:
        return {"error": "Invalid node ID format"}

    factory = get_session_factory_cached()
    async with factory() as session:
        engine = GraphEngine(session, qdrant_client=get_qdrant_client_cached())

        node = await engine.get_node(uid)
        if not node:
            return {"error": "Node not found"}

        # Use denormalized counts from Node model — no extra queries
        meta = node.metadata_ or {}
        aliases = meta.get("aliases", [])
        merged_from = meta.get("merged_from", [])
        seed_ambiguity = meta.get("seed_ambiguity")
        dialectic_role = meta.get("dialectic_role")
        dialectic_pair_id = meta.get("dialectic_pair_id")

        # Resolve parent concept name
        parent_concept: str | None = None
        if node.parent_id:
            parent_node = await engine.get_node(node.parent_id)
            if parent_node:
                parent_concept = parent_node.concept

        result = {
            "node_id": str(node.id),
            "concept": node.concept,
            "node_type": node.node_type,
            "definition": node.definition,
            "parent_id": str(node.parent_id) if node.parent_id else None,
            "parent_concept": parent_concept,
            "created_at": node.created_at.isoformat() if node.created_at else None,
            "fact_count": node.fact_count,
            "edge_count": node.edge_count,
            "dimension_count": node.dimension_count,
            "aliases": aliases,
            "merged_from": merged_from,
            "seed_ambiguity": seed_ambiguity,
            "dialectic_role": dialectic_role,
            "dialectic_pair_id": dialectic_pair_id,
        }

        # Fallback: include one dimension if no definition
        if not node.definition and node.dimension_count > 0:
            dims = await engine.get_dimensions(uid)
            if dims:
                d = dims[0]
                result["fallback_dimension"] = {
                    "model_id": d.model_id,
                    "content": d.content,
                    "confidence": d.confidence,
                    "generated_at": d.generated_at.isoformat() if d.generated_at else None,
                }

        return result


# ── Tool: get_dimensions ─────────────────────────────────────────────


@mcp.tool()
async def get_dimensions(node_id: str, limit: int = 10, offset: int = 0) -> dict:
    """Load dimensions (model perspectives) for a node.

    Each dimension is a different AI model's analysis of the node,
    with content and confidence score. Paginated.

    Args:
        node_id: UUID of the node.
        limit: Max dimensions to return (1-50, default 10).
        offset: Number of dimensions to skip (default 0).
    """
    uid = _parse_uuid(node_id)
    if uid is None:
        return {"error": "Invalid node ID format"}

    limit = max(1, min(50, limit))
    offset = max(0, offset)

    factory = get_session_factory_cached()
    async with factory() as session:
        engine = GraphEngine(session, qdrant_client=get_qdrant_client_cached())

        node = await engine.get_node(uid)
        if not node:
            return {"error": "Node not found"}

        dims = await engine.get_dimensions(uid)
        total = len(dims)
        page = dims[offset : offset + limit]

        return {
            "node_id": str(uid),
            "concept": node.concept,
            "dimensions": [
                {
                    "model_id": d.model_id,
                    "content": d.content,
                    "confidence": d.confidence,
                    "generated_at": d.generated_at.isoformat() if d.generated_at else None,
                }
                for d in page
            ],
            "returned": len(page),
            "total": total,
            "offset": offset,
        }


# ── Tool: get_edges ──────────────────────────────────────────────────


@mcp.tool()
async def get_edges(
    node_id: str,
    limit: int = 10,
    offset: int = 0,
    edge_type: str | None = None,
) -> dict:
    """Load edges (relationships) for a node, sorted by fact count (most evidence first).

    Returns connected nodes with relationship type, weight, justification,
    and fact count. Paginated and optionally filtered by edge type.
    Use offset/limit to page through results.

    Args:
        node_id: UUID of the node.
        limit: Max edges to return (1-100, default 10).
        offset: Number of edges to skip (default 0).
        edge_type: Optional filter: 'related' or 'cross_type'.
    """
    uid = _parse_uuid(node_id)
    if uid is None:
        return {"error": "Invalid node ID format"}

    limit = max(1, min(100, limit))
    offset = max(0, offset)

    factory = get_session_factory_cached()
    async with factory() as session:
        engine = GraphEngine(session, qdrant_client=get_qdrant_client_cached())

        node = await engine.get_node(uid)
        if not node:
            return {"error": "Node not found"}

        edges = await engine.get_edges(uid, direction="both")

        # Filter by type if requested
        if edge_type:
            edges = [e for e in edges if e.relationship_type == edge_type]

        # Batch fact counts for all edges
        edge_ids = [e.id for e in edges]
        edge_fact_counts: dict[uuid.UUID, int] = {}
        if edge_ids:
            fact_stmt = (
                select(EdgeFact.edge_id, func.count(EdgeFact.fact_id))
                .where(EdgeFact.edge_id.in_(edge_ids))
                .group_by(EdgeFact.edge_id)
            )
            fact_result = await session.execute(fact_stmt)
            edge_fact_counts = {row[0]: row[1] for row in fact_result.all()}

        # Sort by fact count descending
        edges.sort(key=lambda e: edge_fact_counts.get(e.id, 0), reverse=True)

        total = len(edges)
        page = edges[offset : offset + limit]

        # Batch-fetch target nodes for this page
        target_ids = set()
        for e in page:
            target_ids.add(e.source_node_id if e.source_node_id != uid else e.target_node_id)

        target_nodes = {}
        if target_ids:
            stmt = select(Node.id, Node.concept, Node.node_type).where(Node.id.in_(list(target_ids)))
            result = await session.execute(stmt)
            for row in result.all():
                target_nodes[row.id] = {"concept": row.concept, "node_type": row.node_type}

        edge_items = []
        for e in page:
            other_id = e.target_node_id if e.source_node_id == uid else e.source_node_id
            target_info = target_nodes.get(other_id, {})
            edge_items.append(
                {
                    "edge_id": str(e.id),
                    "other_node_id": str(other_id),
                    "other_concept": target_info.get("concept"),
                    "other_node_type": target_info.get("node_type"),
                    "relationship_type": e.relationship_type,
                    "weight": e.weight,
                    "justification": e.justification,
                    "fact_count": edge_fact_counts.get(e.id, 0),
                }
            )

        return {
            "node_id": str(uid),
            "concept": node.concept,
            "edges": edge_items,
            "returned": len(edge_items),
            "total": total,
            "offset": offset,
        }


# ── Tool: get_facts ──────────────────────────────────────────────────


@mcp.tool()
async def get_facts(
    node_id: str,
    limit: int = 50,
    offset: int = 0,
    source_node_id: str | None = None,
    author_org: str | None = None,
    source_domain: str | None = None,
    search: str | None = None,
    fact_type: str | None = None,
) -> dict:
    """Load facts linked to a node, grouped by source.

    Returns facts organized by their primary source, with author
    information and provenance. Each source group contains facts
    extracted from that source. Sources are sorted by fact count
    (most facts first). Paginated via offset/limit over the flat
    fact list — use the returned offset to fetch the next page.

    Supports two powerful filtering strategies:

    1. **Node intersection** (``source_node_id``): Return only facts
       linked to BOTH ``node_id`` AND ``source_node_id``.  This is
       the most reliable way to answer questions like "what does CNN
       say about Epstein" — pass Epstein as ``node_id`` and CNN as
       ``source_node_id`` (or vice versa).  Works with any two nodes
       that share facts.

    2. **Source metadata** (``author_org``, ``source_domain``):
       Filter by FactSource fields.  Useful as a fallback when the
       source entity doesn't have its own node.

    Both strategies can be combined.

    Args:
        node_id: UUID of the subject node.
        limit: Max facts to return (1-200, default 50).
        offset: Number of facts to skip (default 0). Use the
            returned next_offset to fetch the next page.
        source_node_id: UUID of a second node — only facts linked
            to BOTH nodes are returned.  Use this for "what does X
            say about Y" queries.
        author_org: Filter by author organization name (case-insensitive
            partial match). E.g. "CNN", "New York Times", "Reuters".
        source_domain: Filter by source URL domain (case-insensitive
            partial match). E.g. "cnn.com", "reuters.com".
        search: Filter by fact content text (case-insensitive).
        fact_type: Filter by fact type: claim, account, measurement,
            formula, quote, procedure, reference, code, perspective.
    """
    uid = _parse_uuid(node_id)
    if uid is None:
        return {"error": "Invalid node ID format"}

    source_uid: uuid.UUID | None = None
    if source_node_id is not None:
        source_uid = _parse_uuid(source_node_id)
        if source_uid is None:
            return {"error": "Invalid source_node_id format"}

    limit = max(1, min(200, limit))
    offset = max(0, offset)

    has_filters = source_uid or author_org or source_domain or search or fact_type

    factory = get_session_factory_cached()
    async with factory() as session:
        engine = GraphEngine(session, qdrant_client=get_qdrant_client_cached())

        node = await engine.get_node(uid)
        if not node:
            return {"error": "Node not found"}

        # Resolve source node name for the response
        source_node_concept: str | None = None
        if source_uid:
            source_node = await engine.get_node(source_uid)
            if not source_node:
                return {"error": "Source node not found"}
            source_node_concept = source_node.concept

        # When any filters are active, use the filtered query path
        if has_filters:
            from kt_db.repositories.facts import FactRepository

            repo = FactRepository(session)
            # Fetch limit+1 to detect has_more without a separate COUNT query;
            # skip eager source loading — we batch-load first source per fact below
            facts = await repo.get_node_facts_by_source(
                uid,
                source_node_id=source_uid,
                author_org=author_org,
                source_domain=source_domain,
                search=search,
                fact_type=fact_type,
                limit=limit + 1,
                offset=offset,
                load_sources=False,
            )
            has_more = len(facts) > limit
            if has_more:
                facts = facts[:limit]

            # Batch-load first source per fact in a single DISTINCT ON query
            fact_ids = [f.id for f in facts]
            first_sources = await repo.get_first_sources_for_facts(fact_ids)

            # Build grouped response
            source_groups: dict[str, dict] = {}
            for f in facts:
                pair = first_sources.get(f.id)
                fs, rs = pair if pair else (None, None)
                key = str(rs.id) if rs else "__none__"

                if key not in source_groups:
                    source_groups[key] = {
                        "source_id": str(rs.id) if rs else None,
                        "uri": rs.uri if rs else None,
                        "title": rs.title if rs else None,
                        "provider_id": rs.provider_id if rs else None,
                        "published_date": _extract_published_date(rs) if rs else None,
                        "retrieved_at": (rs.retrieved_at.isoformat() if rs and rs.retrieved_at else None),
                        "author_person": fs.author_person if fs else None,
                        "author_org": fs.author_org if fs else None,
                        "attribution": fs.attribution if fs else None,
                        "facts": [],
                    }

                source_groups[key]["facts"].append(
                    {
                        "fact_id": str(f.id),
                        "content": f.content,
                        "fact_type": f.fact_type,
                        "created_at": f.created_at.isoformat() if f.created_at else None,
                    }
                )

            groups = sorted(
                source_groups.values(),
                key=lambda g: len(g["facts"]),
                reverse=True,
            )
            # Add fact_count to each group
            for g in groups:
                g["fact_count"] = len(g["facts"])

            returned = len(facts)

            return {
                "node_id": str(uid),
                "concept": node.concept,
                "source_groups": groups,
                "total_sources": len(source_groups),
                "returned_facts": returned,
                "offset": offset,
                "next_offset": offset + returned if has_more else None,
                "filters": {
                    k: v
                    for k, v in {
                        "source_node_id": source_node_id,
                        "source_node_concept": source_node_concept,
                        "author_org": author_org,
                        "source_domain": source_domain,
                        "search": search,
                        "fact_type": fact_type,
                    }.items()
                    if v is not None
                },
            }

        # Unfiltered path — load all facts and paginate in memory
        facts = await engine.get_node_facts_with_sources(uid)
        total = len(facts)

        # Group ALL facts by primary source (first source), like the wiki frontend
        source_groups: dict[str, dict] = {}
        # Also build a flat ordered list for offset/limit slicing
        flat_facts: list[tuple[str, dict]] = []
        for f in facts:
            src = f.sources[0] if f.sources else None
            key = str(src.raw_source.id) if src else "__none__"

            if key not in source_groups:
                source_groups[key] = {
                    "source_id": str(src.raw_source.id) if src else None,
                    "uri": src.raw_source.uri if src else None,
                    "title": src.raw_source.title if src else None,
                    "provider_id": src.raw_source.provider_id if src else None,
                    "published_date": _extract_published_date(src.raw_source) if src else None,
                    "retrieved_at": (
                        src.raw_source.retrieved_at.isoformat() if src and src.raw_source.retrieved_at else None
                    ),
                    "author_person": src.author_person if src else None,
                    "author_org": src.author_org if src else None,
                    "attribution": src.attribution if src else None,
                    "fact_count": 0,
                }

            source_groups[key]["fact_count"] += 1
            flat_facts.append(
                (
                    key,
                    {
                        "fact_id": str(f.id),
                        "content": f.content,
                        "fact_type": f.fact_type,
                        "created_at": f.created_at.isoformat() if f.created_at else None,
                    },
                )
            )

        # Slice the flat list by offset/limit
        page = flat_facts[offset : offset + limit]

        # Rebuild groups for this page only
        page_groups: dict[str, dict] = {}
        for key, fact_item in page:
            if key not in page_groups:
                group_meta = source_groups[key]
                page_groups[key] = {**group_meta, "facts": []}
            page_groups[key]["facts"].append(fact_item)

        # Sort page groups by total fact count descending
        groups = sorted(page_groups.values(), key=lambda g: g["fact_count"], reverse=True)

        returned = len(page)
        has_more = offset + returned < total

        return {
            "node_id": str(uid),
            "concept": node.concept,
            "source_groups": groups,
            "total_sources": len(source_groups),
            "returned_facts": returned,
            "total_facts": total,
            "offset": offset,
            "next_offset": offset + returned if has_more else None,
        }


# ── Tool: get_fact_sources ───────────────────────────────────────────


@mcp.tool()
async def get_fact_sources(node_id: str) -> dict:
    """Load all sources for a node's facts.

    Returns a deduplicated list of raw sources (URI, title, provider,
    retrieval date) that back the facts linked to this node.
    Full provenance chain: Node -> Facts -> Sources.

    Args:
        node_id: UUID of the node.
    """
    uid = _parse_uuid(node_id)
    if uid is None:
        return {"error": "Invalid node ID format"}

    factory = get_session_factory_cached()
    async with factory() as session:
        engine = GraphEngine(session, qdrant_client=get_qdrant_client_cached())

        node = await engine.get_node(uid)
        if not node:
            return {"error": "Node not found"}

        facts = await engine.get_node_facts_with_sources(uid)

        # Deduplicate sources by URI
        seen: set[str] = set()
        sources: list[dict] = []
        for f in facts:
            for fs in f.sources:
                uri = fs.raw_source.uri
                if uri in seen:
                    continue
                seen.add(uri)
                sources.append(
                    {
                        "source_id": str(fs.raw_source.id),
                        "uri": uri,
                        "title": fs.raw_source.title,
                        "provider_id": fs.raw_source.provider_id,
                        "published_date": _extract_published_date(fs.raw_source),
                        "retrieved_at": fs.raw_source.retrieved_at.isoformat() if fs.raw_source.retrieved_at else None,
                        "context_snippet": fs.context_snippet,
                        "author_person": fs.author_person,
                        "author_org": fs.author_org,
                        "attribution": fs.attribution,
                    }
                )

        return {
            "node_id": str(uid),
            "concept": node.concept,
            "total_facts": len(facts),
            "total_unique_sources": len(sources),
            "sources": sources,
        }


# ── Tool: search_facts ─────────────────────────────────────────────


@mcp.tool()
async def search_facts(
    query: str | None = None,
    node_id: str | None = None,
    limit: int = 30,
    offset: int = 0,
    fact_type: str | None = None,
    author_org: str | None = None,
    source_domain: str | None = None,
) -> dict:
    """Search the global fact pool by text query or node context.

    Searches across ALL facts in the knowledge graph, not just those
    linked to a specific node. Useful for exploring topics that may
    not yet be fully reflected in nodes, or for finding evidence
    across multiple concepts.

    Accepts either a text ``query`` or a ``node_id``.  When
    ``node_id`` is provided, the node's concept name and aliases
    are used as the search query automatically — this is more
    reliable than manually typing the name.  If both are provided,
    ``query`` takes precedence.

    Each result includes the fact content, its sources with author
    info, and the nodes it is linked to.

    Args:
        query: Text to search for in fact content.  Optional if
            ``node_id`` is provided.
        node_id: UUID of a node — its concept name and aliases are
            used as the search query.  More reliable than a text query
            because it accounts for all known names of the concept.
        limit: Max facts to return (1-100, default 30).
        offset: Number of facts to skip (default 0).
        fact_type: Optional filter: claim, account, measurement,
            formula, quote, procedure, reference, code, perspective.
        author_org: Filter by author organization name (case-insensitive
            partial match). E.g. "CNN", "New York Times", "Reuters".
        source_domain: Filter by source URL domain (case-insensitive
            partial match). E.g. "cnn.com", "reuters.com".
    """
    limit = max(1, min(100, limit))
    offset = max(0, offset)

    factory = get_session_factory_cached()
    async with factory() as session:
        engine = GraphEngine(session, qdrant_client=get_qdrant_client_cached())

        # Resolve node_id to a search query if no explicit query given
        resolved_from_node: str | None = None
        if not query and node_id:
            uid = _parse_uuid(node_id)
            if uid is None:
                return {"error": "Invalid node_id format"}
            node = await engine.get_node(uid)
            if not node:
                return {"error": "Node not found"}
            # Build search terms from concept + aliases
            meta = node.metadata_ or {}
            aliases = meta.get("aliases", [])
            merged_from = meta.get("merged_from", [])
            all_names = [node.concept, *aliases, *merged_from]
            # Use the concept as the primary query
            query = node.concept
            resolved_from_node = node.concept
            # If there are aliases, search for any of them
            if len(all_names) > 1:
                # Use the concept name — ILIKE already does partial matching
                # but include aliases info in the response for transparency
                pass

        if not query:
            return {"error": "Either query or node_id must be provided"}

        # Get total count for pagination
        total = await engine.count_facts(
            search=query,
            fact_type=fact_type,
            author_org=author_org,
            source_domain=source_domain,
        )

        # Get paginated results
        facts = await engine.list_facts(
            offset=offset,
            limit=limit,
            search=query,
            fact_type=fact_type,
            author_org=author_org,
            source_domain=source_domain,
        )

        if not facts:
            return {
                "facts": [],
                "returned": 0,
                "total": total,
                "offset": offset,
                "next_offset": None,
            }

        fact_ids = [f.id for f in facts]

        # Batch-load sources for these facts
        source_stmt = (
            select(Fact)
            .where(Fact.id.in_(fact_ids))
            .options(selectinload(Fact.sources).selectinload(FactSource.raw_source))
        )
        source_result = await session.execute(source_stmt)
        facts_with_sources = {f.id: f for f in source_result.scalars().all()}

        # Batch-load linked nodes for these facts
        node_link_stmt = (
            select(NodeFact.fact_id, Node.id, Node.concept, Node.node_type)
            .join(Node, Node.id == NodeFact.node_id)
            .where(NodeFact.fact_id.in_(fact_ids))
        )
        node_link_result = await session.execute(node_link_stmt)
        fact_nodes: dict[uuid.UUID, list[dict]] = {}
        for row in node_link_result.all():
            fid = row[0]
            if fid not in fact_nodes:
                fact_nodes[fid] = []
            fact_nodes[fid].append(
                {
                    "node_id": str(row[1]),
                    "concept": row[2],
                    "node_type": row[3],
                }
            )

        items = []
        for f in facts:
            # Build sources list
            sources: list[dict] = []
            rich_fact = facts_with_sources.get(f.id)
            if rich_fact and rich_fact.sources:
                for fs in rich_fact.sources:
                    sources.append(
                        {
                            "uri": fs.raw_source.uri,
                            "title": fs.raw_source.title,
                            "provider_id": fs.raw_source.provider_id,
                            "published_date": _extract_published_date(fs.raw_source),
                            "author_person": fs.author_person,
                            "author_org": fs.author_org,
                        }
                    )

            items.append(
                {
                    "fact_id": str(f.id),
                    "content": f.content,
                    "fact_type": f.fact_type,
                    "created_at": f.created_at.isoformat() if f.created_at else None,
                    "sources": sources,
                    "linked_nodes": fact_nodes.get(f.id, []),
                }
            )

        returned = len(items)
        has_more = offset + returned < total

        result = {
            "facts": items,
            "returned": returned,
            "total": total,
            "offset": offset,
            "next_offset": offset + returned if has_more else None,
        }
        if resolved_from_node:
            result["resolved_query"] = {
                "node_id": node_id,
                "concept": resolved_from_node,
                "search_text": query,
            }
        return result


# ── Tool: get_node_paths ───────────────────────────────────────────


@mcp.tool()
async def get_node_paths(
    source_node_id: str,
    target_node_id: str,
    max_depth: int = 6,
    limit: int = 5,
) -> dict:
    """Find shortest paths between two nodes in the knowledge graph.

    Uses breadth-first search to discover how two concepts are
    connected through the graph's edge relationships. Returns all
    shortest paths (same hop count) up to the limit. Each path is
    a sequence of steps: node → edge → node → edge → ... → node.

    Edges are bidirectional — the algorithm traverses in both
    directions regardless of canonical source/target ordering.

    Args:
        source_node_id: UUID of the starting node.
        target_node_id: UUID of the destination node.
        max_depth: Maximum path length in hops (1-10, default 6).
        limit: Maximum number of paths to return (1-20, default 5).
    """
    source_uid = _parse_uuid(source_node_id)
    if source_uid is None:
        return {"error": "Invalid source node ID format"}

    target_uid = _parse_uuid(target_node_id)
    if target_uid is None:
        return {"error": "Invalid target node ID format"}

    max_depth = max(1, min(10, max_depth))
    limit = max(1, min(20, limit))

    factory = get_session_factory_cached()
    async with factory() as session:
        engine = GraphEngine(session, qdrant_client=get_qdrant_client_cached())

        source_node = await engine.get_node(source_uid)
        if not source_node:
            return {"error": "Source node not found"}

        target_node = await engine.get_node(target_uid)
        if not target_node:
            return {"error": "Target node not found"}

        raw_paths = await engine.find_shortest_paths(
            source_uid,
            target_uid,
            max_depth=max_depth,
            limit=limit,
        )

        if not raw_paths:
            return {
                "source": {
                    "node_id": str(source_uid),
                    "concept": source_node.concept,
                    "node_type": source_node.node_type,
                },
                "target": {
                    "node_id": str(target_uid),
                    "concept": target_node.concept,
                    "node_type": target_node.node_type,
                },
                "paths": [],
                "total_found": 0,
                "message": "No path found between these nodes within the depth limit.",
            }

        # Bulk-fetch all node concepts
        all_node_ids: set[uuid.UUID] = set()
        for path in raw_paths:
            for step in path:
                all_node_ids.add(step.node_id)

        nodes_by_id: dict[uuid.UUID, Node] = {}
        if all_node_ids:
            fetched = await engine.get_nodes_by_ids(list(all_node_ids))
            nodes_by_id = {n.id: n for n in fetched}

        paths: list[dict] = []
        for raw_path in raw_paths:
            steps: list[dict] = []
            for step in raw_path:
                node = nodes_by_id.get(step.node_id)
                step_dict: dict = {
                    "node_id": str(step.node_id),
                    "concept": node.concept if node else "Unknown",
                    "node_type": node.node_type if node else "concept",
                }
                if step.edge is not None:
                    step_dict["edge"] = {
                        "edge_id": str(step.edge.id),
                        "relationship_type": step.edge.relationship_type,
                        "weight": step.edge.weight,
                        "justification": step.edge.justification,
                    }
                steps.append(step_dict)
            paths.append(
                {
                    "steps": steps,
                    "length": len(raw_path) - 1,  # Number of hops
                }
            )

        return {
            "source": {"node_id": str(source_uid), "concept": source_node.concept, "node_type": source_node.node_type},
            "target": {"node_id": str(target_uid), "concept": target_node.concept, "node_type": target_node.node_type},
            "paths": paths,
            "total_found": len(paths),
            "truncated": len(raw_paths) >= limit,
        }


# ── FastAPI app with OAuth 2.1 ─────────────────────────────────────

import asyncio  # noqa: E402
from contextlib import asynccontextmanager  # noqa: E402

from kt_mcp.oauth_login import oauth_login_router  # noqa: E402

mcp_http = mcp.http_app(path="/mcp", stateless_http=True)

_CLEANUP_INTERVAL_SECONDS = 60 * 60  # Run cleanup every hour


@asynccontextmanager
async def _lifespan(app_instance: FastAPI):  # type: ignore[no-untyped-def]
    """Wrap the MCP lifespan to add periodic OAuth token cleanup."""
    cleanup_task: asyncio.Task[None] | None = None

    async def _periodic_cleanup() -> None:
        provider = create_oauth_provider()
        while True:
            await asyncio.sleep(_CLEANUP_INTERVAL_SECONDS)
            try:
                await provider.cleanup_expired()
            except Exception:
                logger.exception("OAuth cleanup failed")

    async with mcp_http.lifespan(app_instance):
        cleanup_task = asyncio.create_task(_periodic_cleanup())
        try:
            yield
        finally:
            cleanup_task.cancel()
            try:
                await cleanup_task
            except asyncio.CancelledError:
                pass


app = FastAPI(title="Knowledge Tree MCP", lifespan=_lifespan)

# Login page for OAuth authorize flow
app.include_router(oauth_login_router)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


# Mount MCP + OAuth routes (/.well-known/*, /authorize, /token, /register, /mcp)
app.mount("/", mcp_http)
