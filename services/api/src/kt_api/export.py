"""Export endpoints — download nodes, facts, or full conversations as JSON."""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from kt_api.dependencies import get_db_session, get_qdrant_client_cached
from kt_api.schemas import (
    ConversationExportResponse,
    ConversationResponse,
    EdgeResponse,
    ExportMetadata,
    FactResponse,
    FactsExportResponse,
    FactSourceInfo,
    NodeFactLinkItem,
    NodeResponse,
    NodesExportResponse,
)
from kt_config.settings import get_settings
from kt_db.models import NodeFact
from kt_db.repositories.conversations import ConversationRepository
from kt_graph.engine import GraphEngine

router = APIRouter(prefix="/api/v1/export", tags=["export"])


# ── Helpers ──────────────────────────────────────────────────────────────


def _node_to_response(
    n: object,
    embedding_map: dict[uuid.UUID, list[float]] | None = None,
) -> NodeResponse:
    return NodeResponse(
        id=str(n.id),  # type: ignore[attr-defined]
        concept=n.concept,  # type: ignore[attr-defined]
        node_type=n.node_type,  # type: ignore[attr-defined]
        parent_id=str(n.parent_id) if n.parent_id else None,  # type: ignore[attr-defined]
        attractor=n.attractor,  # type: ignore[attr-defined]
        filter_id=n.filter_id,  # type: ignore[attr-defined]
        max_content_tokens=n.max_content_tokens,  # type: ignore[attr-defined]
        created_at=n.created_at,  # type: ignore[attr-defined]
        updated_at=n.updated_at,  # type: ignore[attr-defined]
        update_count=n.update_count,  # type: ignore[attr-defined]
        access_count=n.access_count,  # type: ignore[attr-defined]
        convergence_score=getattr(n, "convergence_score", 0.0),  # type: ignore[attr-defined]
        definition=n.definition,  # type: ignore[attr-defined]
        definition_generated_at=n.definition_generated_at.isoformat() if n.definition_generated_at else None,  # type: ignore[attr-defined]
        metadata=n.metadata_,  # type: ignore[attr-defined]
        embedding=embedding_map.get(n.id) if embedding_map else None,  # type: ignore[attr-defined]
    )


def _edge_to_response(e: object) -> EdgeResponse:
    return EdgeResponse(
        id=str(e.id),  # type: ignore[attr-defined]
        source_node_id=str(e.source_node_id),  # type: ignore[attr-defined]
        target_node_id=str(e.target_node_id),  # type: ignore[attr-defined]
        relationship_type=e.relationship_type,  # type: ignore[attr-defined]
        weight=e.weight,  # type: ignore[attr-defined]
        justification=e.justification,  # type: ignore[attr-defined]
        supporting_fact_ids=[str(ef.fact_id) for ef in e.edge_facts],  # type: ignore[attr-defined]
        created_at=e.created_at,  # type: ignore[attr-defined]
    )


def _fact_to_response(
    f: object,
    *,
    include_raw_content: bool = False,
    embedding_map: dict[uuid.UUID, list[float]] | None = None,
) -> FactResponse:
    return FactResponse(
        id=str(f.id),  # type: ignore[attr-defined]
        content=f.content,  # type: ignore[attr-defined]
        fact_type=f.fact_type,  # type: ignore[attr-defined]
        metadata=f.metadata_,  # type: ignore[attr-defined]
        created_at=f.created_at,  # type: ignore[attr-defined]
        embedding=embedding_map.get(f.id) if embedding_map else None,  # type: ignore[attr-defined]
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
                raw_content=fs.raw_source.raw_content if include_raw_content else None,
                content_hash=fs.raw_source.content_hash,
                is_full_text=getattr(fs.raw_source, "is_full_text", False),
                content_type=getattr(fs.raw_source, "content_type", None),
                provider_metadata=getattr(fs.raw_source, "provider_metadata", None),
            )
            for fs in f.sources  # type: ignore[attr-defined]
        ],
    )


async def _get_node_fact_links(
    session: AsyncSession,
    node_ids: list[uuid.UUID],
) -> list[NodeFactLinkItem]:
    """Query NodeFact junction table to get links with relevance_score and stance."""
    if not node_ids:
        return []
    stmt = select(NodeFact).where(NodeFact.node_id.in_(node_ids))
    result = await session.execute(stmt)
    return [
        NodeFactLinkItem(
            node_id=str(nf.node_id),
            fact_id=str(nf.fact_id),
            relevance_score=nf.relevance_score,
            stance=nf.stance,
        )
        for nf in result.scalars().all()
    ]


async def _fetch_embeddings(
    qdrant_client: object,
    node_ids: list[uuid.UUID],
    fact_ids: list[uuid.UUID],
) -> tuple[dict[uuid.UUID, list[float]], dict[uuid.UUID, list[float]]]:
    """Fetch node and fact embeddings from Qdrant. Returns (node_map, fact_map)."""
    from kt_qdrant.repositories.facts import QdrantFactRepository
    from kt_qdrant.repositories.nodes import QdrantNodeRepository

    node_map: dict[uuid.UUID, list[float]] = {}
    fact_map: dict[uuid.UUID, list[float]] = {}

    if node_ids:
        try:
            node_repo = QdrantNodeRepository(qdrant_client)
            node_map = await node_repo.get_vectors(node_ids)
        except Exception:
            logger.warning("Failed to fetch node embeddings from Qdrant", exc_info=True)

    if fact_ids:
        try:
            fact_repo = QdrantFactRepository(qdrant_client)
            fact_map = await fact_repo.get_vectors(fact_ids)
        except Exception:
            logger.warning("Failed to fetch fact embeddings from Qdrant", exc_info=True)

    return node_map, fact_map


def _conv_to_response(conv: object) -> ConversationResponse:
    from kt_api.research import _conversation_to_response

    return _conversation_to_response(conv)


# ── Endpoints ────────────────────────────────────────────────────────────


@router.get("/nodes", response_model=NodesExportResponse)
async def export_nodes(
    include_raw_content: bool = False,
    include_embeddings: bool = False,
    session: AsyncSession = Depends(get_db_session),
) -> NodesExportResponse:
    """Export all nodes in the graph as JSON, including linked facts."""
    qdrant = get_qdrant_client_cached()
    engine = GraphEngine(session, qdrant_client=qdrant)
    nodes = await engine.list_all_nodes()

    # Collect edges
    edges = await engine.list_all_edges()

    # Collect facts via node-fact links (with junction metadata)
    node_ids = [n.id for n in nodes]
    node_fact_links = await _get_node_fact_links(session, node_ids)

    # Load all referenced facts with sources
    seen_fact_ids: set[uuid.UUID] = set()
    all_fact_ids: list[uuid.UUID] = []
    for link in node_fact_links:
        fid = uuid.UUID(link.fact_id)
        if fid not in seen_fact_ids:
            seen_fact_ids.add(fid)
            all_fact_ids.append(fid)

    # Also collect facts linked to edges (for justification {fact:UUID} tokens)
    for e in edges:
        for ef in e.edge_facts:
            if ef.fact_id not in seen_fact_ids:
                seen_fact_ids.add(ef.fact_id)
                all_fact_ids.append(ef.fact_id)

    all_facts = []
    if all_fact_ids:
        from kt_db.repositories.facts import FactRepository

        fact_repo = FactRepository(session)
        all_facts = await fact_repo.get_by_ids_with_sources(all_fact_ids)

    # Optionally fetch embeddings from Qdrant
    node_emb_map: dict[uuid.UUID, list[float]] | None = None
    fact_emb_map: dict[uuid.UUID, list[float]] | None = None
    embedding_model: str | None = None
    if include_embeddings and qdrant is not None:
        node_emb_map, fact_emb_map = await _fetch_embeddings(
            qdrant,
            node_ids,
            all_fact_ids,
        )
        embedding_model = get_settings().embedding_model

    return NodesExportResponse(
        metadata=ExportMetadata(
            exported_at=datetime.now(timezone.utc),
            export_type="nodes",
            total_items=len(nodes),
            embedding_model=embedding_model,
        ),
        nodes=[_node_to_response(n, embedding_map=node_emb_map) for n in nodes],
        edges=[_edge_to_response(e) for e in edges],
        facts=[
            _fact_to_response(f, include_raw_content=include_raw_content, embedding_map=fact_emb_map) for f in all_facts
        ],
        node_fact_links=node_fact_links,
    )


@router.get("/facts", response_model=FactsExportResponse)
async def export_facts(
    include_raw_content: bool = False,
    include_embeddings: bool = False,
    session: AsyncSession = Depends(get_db_session),
) -> FactsExportResponse:
    """Export all facts with their sources as JSON."""
    qdrant = get_qdrant_client_cached()
    engine = GraphEngine(session, qdrant_client=qdrant)
    facts = await engine.list_all_facts_with_sources()

    # Optionally fetch embeddings
    fact_emb_map: dict[uuid.UUID, list[float]] | None = None
    embedding_model: str | None = None
    if include_embeddings and qdrant is not None:
        _, fact_emb_map = await _fetch_embeddings(
            qdrant,
            [],
            [f.id for f in facts],
        )
        embedding_model = get_settings().embedding_model

    return FactsExportResponse(
        metadata=ExportMetadata(
            exported_at=datetime.now(timezone.utc),
            export_type="facts",
            total_items=len(facts),
            embedding_model=embedding_model,
        ),
        facts=[
            _fact_to_response(f, include_raw_content=include_raw_content, embedding_map=fact_emb_map) for f in facts
        ],
    )


@router.get("/conversations/{conversation_id}", response_model=ConversationExportResponse)
async def export_conversation(
    conversation_id: str,
    include_raw_content: bool = False,
    include_embeddings: bool = False,
    session: AsyncSession = Depends(get_db_session),
) -> ConversationExportResponse:
    """Export a conversation with all associated nodes, edges, and facts."""
    try:
        conv_uuid = uuid.UUID(conversation_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid conversation ID")

    repo = ConversationRepository(session)
    conv = await repo.get_with_messages(conv_uuid)
    if conv is None:
        raise HTTPException(status_code=404, detail="Conversation not found")

    # Collect all node IDs referenced across messages
    all_node_ids: set[str] = set()
    for msg in conv.messages:
        if msg.visited_nodes:
            all_node_ids.update(msg.visited_nodes)
        if msg.created_nodes:
            all_node_ids.update(msg.created_nodes)

    # Parse valid UUIDs
    node_uuids: list[uuid.UUID] = []
    for nid in all_node_ids:
        try:
            node_uuids.append(uuid.UUID(nid))
        except ValueError:
            continue

    # Get subgraph (nodes + edges)
    engine = GraphEngine(session, qdrant_client=get_qdrant_client_cached())
    subgraph = await engine.get_subgraph(node_uuids) if node_uuids else {"nodes": [], "edges": []}
    nodes = subgraph["nodes"]
    edges = subgraph["edges"]
    # Fetch node-fact links with junction metadata
    conv_node_ids = [n.id for n in nodes]
    node_fact_links = await _get_node_fact_links(session, conv_node_ids)

    # Load all referenced facts with sources
    seen_fact_ids: set[uuid.UUID] = set()
    all_fact_ids: list[uuid.UUID] = []
    for link in node_fact_links:
        fid = uuid.UUID(link.fact_id)
        if fid not in seen_fact_ids:
            seen_fact_ids.add(fid)
            all_fact_ids.append(fid)

    # Also collect facts linked to edges (for justification {fact:UUID} tokens)
    for e in edges:
        for ef in e.edge_facts:  # type: ignore[attr-defined]
            if ef.fact_id not in seen_fact_ids:
                seen_fact_ids.add(ef.fact_id)
                all_fact_ids.append(ef.fact_id)

    all_facts = []
    if all_fact_ids:
        from kt_db.repositories.facts import FactRepository

        fact_repo = FactRepository(session)
        all_facts = await fact_repo.get_by_ids_with_sources(all_fact_ids)

    # Optionally fetch embeddings from Qdrant
    qdrant = get_qdrant_client_cached()
    node_emb_map: dict[uuid.UUID, list[float]] | None = None
    fact_emb_map: dict[uuid.UUID, list[float]] | None = None
    embedding_model: str | None = None
    if include_embeddings and qdrant is not None:
        node_emb_map, fact_emb_map = await _fetch_embeddings(
            qdrant,
            conv_node_ids,
            all_fact_ids,
        )
        embedding_model = get_settings().embedding_model

    total_items = len(nodes) + len(edges) + len(all_facts)

    return ConversationExportResponse(
        metadata=ExportMetadata(
            exported_at=datetime.now(timezone.utc),
            export_type="conversation",
            total_items=total_items,
            embedding_model=embedding_model,
        ),
        conversation=_conv_to_response(conv),
        nodes=[_node_to_response(n, embedding_map=node_emb_map) for n in nodes],
        edges=[_edge_to_response(e) for e in edges],
        facts=[
            _fact_to_response(f, include_raw_content=include_raw_content, embedding_map=fact_emb_map) for f in all_facts
        ],
        node_fact_links=node_fact_links,
    )
