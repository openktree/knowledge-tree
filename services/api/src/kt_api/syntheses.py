"""Synthesis and super-synthesis endpoints."""

from __future__ import annotations

import logging
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from kt_api.dependencies import get_db_session
from kt_db.models import Node, SynthesisSentence

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["syntheses"])


# ── Request/Response schemas ───────────────────────────────────────


class CreateSynthesisRequest(BaseModel):
    topic: str = ""
    starting_node_ids: list[str] = Field(default_factory=list)
    exploration_budget: int = 20
    visibility: str = "public"


class CreateSuperSynthesisRequest(BaseModel):
    topic: str = ""
    sub_configs: list[CreateSynthesisRequest] = Field(default_factory=list)
    visibility: str = "public"
    distance_threshold: float = 0.7


class SynthesisSentenceResponse(BaseModel):
    position: int
    text: str
    fact_count: int = 0
    node_ids: list[str] = Field(default_factory=list)


class SynthesisNodeResponse(BaseModel):
    node_id: str
    concept: str
    node_type: str


class SynthesisDocumentResponse(BaseModel):
    id: str
    concept: str
    node_type: str  # "synthesis" or "supersynthesis"
    visibility: str = "public"
    definition: str | None = None
    sentences: list[SynthesisSentenceResponse] = Field(default_factory=list)
    referenced_nodes: list[SynthesisNodeResponse] = Field(default_factory=list)
    sub_syntheses: list[SynthesisNodeResponse] = Field(default_factory=list)
    created_at: str | None = None


class SynthesisListItem(BaseModel):
    id: str
    concept: str
    node_type: str
    visibility: str = "public"
    sentence_count: int = 0
    created_at: str | None = None


class PaginatedSynthesesResponse(BaseModel):
    items: list[SynthesisListItem]
    total: int
    offset: int
    limit: int


class SentenceFactResponse(BaseModel):
    fact_id: str
    content: str
    fact_type: str
    embedding_distance: float


class SentenceFactsBySourceResponse(BaseModel):
    source_id: str
    source_uri: str
    source_title: str
    facts: list[SentenceFactResponse]


# ── Endpoints ──────────────────────────────────────────────────────


@router.post("/syntheses")
async def create_synthesis(
    body: CreateSynthesisRequest,
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    """Create a new synthesis by dispatching the synthesizer workflow."""
    from kt_hatchet.client import dispatch_workflow

    run_id = await dispatch_workflow("synthesizer_wf", {
        "topic": body.topic,
        "starting_node_ids": body.starting_node_ids,
        "exploration_budget": body.exploration_budget,
        "visibility": body.visibility,
    })
    return {"status": "pending", "workflow_run_id": run_id, "topic": body.topic}


@router.post("/super-syntheses")
async def create_super_synthesis(
    body: CreateSuperSynthesisRequest,
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    """Create a new super-synthesis by dispatching the super-synthesizer workflow."""
    from kt_hatchet.client import dispatch_workflow

    sub_configs = [
        {
            "topic": c.topic,
            "starting_node_ids": c.starting_node_ids,
            "exploration_budget": c.exploration_budget,
            "visibility": c.visibility,
        }
        for c in body.sub_configs
    ]
    run_id = await dispatch_workflow("super_synthesizer_wf", {
        "topic": body.topic,
        "sub_configs": sub_configs,
        "visibility": body.visibility,
        "distance_threshold": body.distance_threshold,
    })
    return {"status": "pending", "workflow_run_id": run_id, "topic": body.topic}


@router.get("/syntheses", response_model=PaginatedSynthesesResponse)
async def list_syntheses(
    offset: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    visibility: str | None = None,
    session: AsyncSession = Depends(get_db_session),
) -> PaginatedSynthesesResponse:
    """List all synthesis and supersynthesis documents."""
    base_filter = Node.node_type.in_(["synthesis", "supersynthesis"])
    if visibility:
        base_filter = base_filter & (Node.visibility == visibility)

    # Count
    count_q = select(func.count()).select_from(Node).where(base_filter)
    total = (await session.execute(count_q)).scalar_one()

    # Fetch
    q = select(Node).where(base_filter).order_by(Node.created_at.desc()).offset(offset).limit(limit)
    nodes = (await session.execute(q)).scalars().all()

    items = []
    for n in nodes:
        # Get sentence count
        sc = (
            await session.execute(
                select(func.count()).select_from(SynthesisSentence).where(SynthesisSentence.synthesis_node_id == n.id)
            )
        ).scalar_one()
        items.append(
            SynthesisListItem(
                id=str(n.id),
                concept=n.concept,
                node_type=n.node_type,
                visibility=n.visibility,
                sentence_count=sc,
                created_at=n.created_at.isoformat() if n.created_at else None,
            )
        )

    return PaginatedSynthesesResponse(items=items, total=total, offset=offset, limit=limit)


@router.get("/syntheses/{synthesis_id}", response_model=SynthesisDocumentResponse)
async def get_synthesis(
    synthesis_id: str,
    session: AsyncSession = Depends(get_db_session),
) -> SynthesisDocumentResponse:
    """Get a synthesis document with sentences, node links, and fact counts."""
    try:
        nid = uuid.UUID(synthesis_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid synthesis ID")

    node = (await session.execute(select(Node).where(Node.id == nid))).scalar_one_or_none()
    if not node or node.node_type not in ("synthesis", "supersynthesis"):
        raise HTTPException(status_code=404, detail="Synthesis not found")

    from kt_db.repositories.synthesis_documents import SynthesisDocumentRepository

    repo = SynthesisDocumentRepository(session)
    sentences = await repo.get_sentences(nid)
    fact_counts = await repo.get_sentence_fact_counts(nid)
    node_links = await repo.get_sentence_node_links(nid)
    referenced_nodes = await repo.get_all_referenced_nodes(nid)

    # Build sentence-level node link map
    sent_node_map: dict[str, list[str]] = {}
    for link in node_links:
        sid = link["sentence_id"]
        if sid not in sent_node_map:
            sent_node_map[sid] = []
        sent_node_map[sid].append(link["node_id"])

    sentence_responses = [
        SynthesisSentenceResponse(
            position=s.position,
            text=s.sentence_text,
            fact_count=fact_counts.get(s.id, 0),
            node_ids=sent_node_map.get(str(s.id), []),
        )
        for s in sentences
    ]

    node_responses = [
        SynthesisNodeResponse(
            node_id=str(n.id),
            concept=n.concept,
            node_type=n.node_type,
        )
        for n in referenced_nodes
    ]

    # For supersynthesis, get children
    sub_syntheses: list[SynthesisNodeResponse] = []
    if node.node_type == "supersynthesis":
        children = await repo.get_synthesis_children(nid)
        sub_syntheses = [
            SynthesisNodeResponse(
                node_id=str(c.id),
                concept=c.concept,
                node_type=c.node_type,
            )
            for c in children
        ]

    return SynthesisDocumentResponse(
        id=str(node.id),
        concept=node.concept,
        node_type=node.node_type,
        visibility=node.visibility,
        definition=node.definition,
        sentences=sentence_responses,
        referenced_nodes=node_responses,
        sub_syntheses=sub_syntheses,
        created_at=node.created_at.isoformat() if node.created_at else None,
    )


@router.get("/syntheses/{synthesis_id}/sentences/{position}/facts")
async def get_sentence_facts(
    synthesis_id: str,
    position: int,
    session: AsyncSession = Depends(get_db_session),
) -> list[SentenceFactsBySourceResponse]:
    """Get facts for a specific sentence, grouped by source."""
    try:
        nid = uuid.UUID(synthesis_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid synthesis ID")

    # Find sentence by position
    result = await session.execute(
        select(SynthesisSentence).where(
            SynthesisSentence.synthesis_node_id == nid,
            SynthesisSentence.position == position,
        )
    )
    sentence = result.scalar_one_or_none()
    if not sentence:
        raise HTTPException(status_code=404, detail="Sentence not found")

    from kt_db.repositories.synthesis_documents import SynthesisDocumentRepository

    repo = SynthesisDocumentRepository(session)
    facts_by_source = await repo.get_sentence_facts(sentence.id)

    return [
        SentenceFactsBySourceResponse(
            source_id=group["source_id"],
            source_uri=group["source_uri"],
            source_title=group["source_title"],
            facts=[
                SentenceFactResponse(
                    fact_id=f["fact_id"],
                    content=f["content"],
                    fact_type=f["fact_type"],
                    embedding_distance=f["embedding_distance"],
                )
                for f in group["facts"]
            ],
        )
        for group in facts_by_source
    ]


@router.get("/syntheses/{synthesis_id}/nodes")
async def get_synthesis_nodes(
    synthesis_id: str,
    session: AsyncSession = Depends(get_db_session),
) -> list[SynthesisNodeResponse]:
    """Get all nodes referenced in a synthesis document."""
    try:
        nid = uuid.UUID(synthesis_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid synthesis ID")

    from kt_db.repositories.synthesis_documents import SynthesisDocumentRepository

    repo = SynthesisDocumentRepository(session)
    nodes = await repo.get_all_referenced_nodes(nid)
    return [
        SynthesisNodeResponse(
            node_id=str(n.id),
            concept=n.concept,
            node_type=n.node_type,
        )
        for n in nodes
    ]


@router.delete("/syntheses/{synthesis_id}")
async def delete_synthesis(
    synthesis_id: str,
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    """Delete a synthesis document and its sentence data."""
    try:
        nid = uuid.UUID(synthesis_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid synthesis ID")

    node = (await session.execute(select(Node).where(Node.id == nid))).scalar_one_or_none()
    if not node or node.node_type not in ("synthesis", "supersynthesis"):
        raise HTTPException(status_code=404, detail="Synthesis not found")

    from kt_db.repositories.synthesis_documents import SynthesisDocumentRepository

    repo = SynthesisDocumentRepository(session)
    await repo.delete_document(nid)
    await session.delete(node)
    await session.commit()

    return {"deleted": True, "id": synthesis_id}


@router.patch("/syntheses/{synthesis_id}")
async def update_synthesis(
    synthesis_id: str,
    body: dict[str, Any],
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    """Update synthesis visibility."""
    try:
        nid = uuid.UUID(synthesis_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid synthesis ID")

    node = (await session.execute(select(Node).where(Node.id == nid))).scalar_one_or_none()
    if not node or node.node_type not in ("synthesis", "supersynthesis"):
        raise HTTPException(status_code=404, detail="Synthesis not found")

    if "visibility" in body:
        node.visibility = body["visibility"]
    await session.commit()

    return {"id": synthesis_id, "visibility": node.visibility}
