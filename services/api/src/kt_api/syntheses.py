"""Synthesis and super-synthesis endpoints.

Synthesis documents are stored as JSON in the node's metadata_ field
under the key "synthesis_document". This follows the project's dual-database
architecture where pipelines write to write-db and the sync worker
propagates to graph-db.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from kt_api.dependencies import get_db_session
from kt_db.models import Node

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["syntheses"])


# ── Request/Response schemas ───────────────────────────────────────


class CreateSynthesisRequest(BaseModel):
    topic: str = ""
    starting_node_ids: list[str] = Field(default_factory=list)
    exploration_budget: int = 20
    visibility: str = "public"
    model_id: str | None = None


class CreateSuperSynthesisRequest(BaseModel):
    topic: str = ""
    sub_configs: list[CreateSynthesisRequest] = Field(default_factory=list)
    existing_synthesis_ids: list[str] = Field(default_factory=list)
    scope_count: int = 0  # 0 = let the LLM decide
    visibility: str = "public"
    distance_threshold: float = 0.7
    model_id: str | None = None


class SentenceFactLink(BaseModel):
    fact_id: str
    distance: float = 0.0


class SynthesisSentenceResponse(BaseModel):
    position: int
    text: str
    fact_count: int = 0
    node_ids: list[str] = Field(default_factory=list)


class SynthesisNodeResponse(BaseModel):
    node_id: str
    concept: str
    node_type: str = "concept"


class SynthesisDocumentResponse(BaseModel):
    id: str
    concept: str
    node_type: str
    visibility: str = "public"
    definition: str | None = None
    model_id: str | None = None
    sentences: list[SynthesisSentenceResponse] = Field(default_factory=list)
    referenced_nodes: list[SynthesisNodeResponse] = Field(default_factory=list)
    sub_syntheses: list[SynthesisNodeResponse] = Field(default_factory=list)
    created_at: str | None = None


class SynthesisListItem(BaseModel):
    id: str
    concept: str
    node_type: str
    visibility: str = "public"
    model_id: str | None = None
    sentence_count: int = 0
    sub_synthesis_ids: list[str] = Field(default_factory=list)
    created_at: str | None = None


class PaginatedSynthesesResponse(BaseModel):
    items: list[SynthesisListItem]
    total: int
    offset: int
    limit: int


class SentenceFactResponse(BaseModel):
    fact_id: str
    content: str = ""
    fact_type: str = ""
    embedding_distance: float = 0.0
    source_title: str = ""
    source_uri: str = ""
    author: str = ""


class SentenceFactsBySourceResponse(BaseModel):
    source_id: str = ""
    source_uri: str = ""
    source_title: str = ""
    facts: list[SentenceFactResponse]


# ── Helpers ────────────────────────────────────────────────────────


def _get_synthesis_doc(node: Node) -> dict[str, Any]:
    """Extract synthesis_document from node metadata, or empty dict."""
    meta = node.metadata_ or {}
    return meta.get("synthesis_document", {})


# ── Endpoints ──────────────────────────────────────────────────────


@router.post("/syntheses")
async def create_synthesis(
    body: CreateSynthesisRequest,
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    """Create a new synthesis by dispatching the synthesizer workflow."""
    from kt_api.config_api import SYNTHESIS_MODEL_IDS
    from kt_hatchet.client import dispatch_workflow

    if body.model_id and body.model_id not in SYNTHESIS_MODEL_IDS:
        raise HTTPException(status_code=400, detail=f"Unsupported model_id: {body.model_id}")

    try:
        run_id = await dispatch_workflow(
            "synthesizer_wf",
            {
                "topic": body.topic,
                "starting_node_ids": body.starting_node_ids,
                "exploration_budget": body.exploration_budget,
                "visibility": body.visibility,
                "model_id": body.model_id,
            },
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    return {"status": "pending", "workflow_run_id": run_id, "topic": body.topic}


@router.post("/super-syntheses")
async def create_super_synthesis(
    body: CreateSuperSynthesisRequest,
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    """Create a new super-synthesis by dispatching the super-synthesizer workflow."""
    from kt_api.config_api import SYNTHESIS_MODEL_IDS
    from kt_hatchet.client import dispatch_workflow

    if body.model_id and body.model_id not in SYNTHESIS_MODEL_IDS:
        raise HTTPException(status_code=400, detail=f"Unsupported model_id: {body.model_id}")

    sub_configs = [
        {
            "topic": c.topic,
            "starting_node_ids": c.starting_node_ids,
            "exploration_budget": c.exploration_budget,
            "visibility": c.visibility,
            "model_id": body.model_id,
        }
        for c in body.sub_configs
    ]
    try:
        run_id = await dispatch_workflow(
            "super_synthesizer_wf",
            {
                "topic": body.topic,
                "sub_configs": sub_configs,
                "existing_synthesis_ids": body.existing_synthesis_ids,
                "scope_count": body.scope_count,
                "visibility": body.visibility,
                "distance_threshold": body.distance_threshold,
                "model_id": body.model_id,
            },
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    return {"status": "pending", "workflow_run_id": run_id, "topic": body.topic}


async def _list_syntheses_impl(
    session: AsyncSession,
    offset: int,
    limit: int,
    visibility: str | None,
) -> PaginatedSynthesesResponse:
    """Shared implementation for listing synthesis documents."""
    base_filter = Node.node_type.in_(["synthesis", "supersynthesis"])
    if visibility:
        base_filter = base_filter & (Node.visibility == visibility)

    count_q = select(func.count()).select_from(Node).where(base_filter)
    total = (await session.execute(count_q)).scalar_one()

    q = select(Node).where(base_filter).order_by(Node.created_at.desc()).offset(offset).limit(limit)
    nodes = (await session.execute(q)).scalars().all()

    items = []
    for n in nodes:
        doc = _get_synthesis_doc(n)
        meta = n.metadata_ or {}
        items.append(
            SynthesisListItem(
                id=str(n.id),
                concept=n.concept,
                node_type=n.node_type,
                visibility=n.visibility,
                model_id=meta.get("model_id"),
                sentence_count=doc.get("stats", {}).get("sentences_count", 0),
                sub_synthesis_ids=doc.get("sub_synthesis_ids", []),
                created_at=n.created_at.isoformat() if n.created_at else None,
            )
        )

    return PaginatedSynthesesResponse(items=items, total=total, offset=offset, limit=limit)


@router.get("/syntheses", response_model=PaginatedSynthesesResponse)
async def list_syntheses(
    offset: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    visibility: str | None = None,
    session: AsyncSession = Depends(get_db_session),
) -> PaginatedSynthesesResponse:
    """List all synthesis and supersynthesis documents."""
    return await _list_syntheses_impl(session, offset, limit, visibility)


async def _get_synthesis_impl(
    session: AsyncSession,
    synthesis_id: str,
) -> SynthesisDocumentResponse:
    """Shared implementation for getting a synthesis document."""
    try:
        nid = uuid.UUID(synthesis_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid synthesis ID")

    node = (await session.execute(select(Node).where(Node.id == nid))).scalar_one_or_none()
    if not node or node.node_type not in ("synthesis", "supersynthesis"):
        raise HTTPException(status_code=404, detail="Synthesis not found")

    doc = _get_synthesis_doc(node)

    sentences = [
        SynthesisSentenceResponse(
            position=s.get("position", i),
            text=s.get("text", ""),
            fact_count=len(s.get("fact_links", [])),
            node_ids=s.get("node_ids", []),
        )
        for i, s in enumerate(doc.get("sentences", []))
    ]

    referenced_nodes = [
        SynthesisNodeResponse(
            node_id=rn.get("node_id", ""),
            concept=rn.get("concept", "unknown"),
            node_type=rn.get("node_type", "concept"),
        )
        for rn in doc.get("referenced_nodes", [])
    ]

    # For supersynthesis, resolve sub-synthesis nodes in a single batch query
    sub_syntheses: list[SynthesisNodeResponse] = []
    sub_ids = doc.get("sub_synthesis_ids", [])
    if sub_ids:
        sub_uuids = []
        for sid in sub_ids:
            try:
                sub_uuids.append(uuid.UUID(sid))
            except ValueError:
                pass
        if sub_uuids:
            sub_result = await session.execute(select(Node).where(Node.id.in_(sub_uuids)))
            for sub_node in sub_result.scalars().all():
                sub_syntheses.append(
                    SynthesisNodeResponse(
                        node_id=str(sub_node.id),
                        concept=sub_node.concept,
                        node_type=sub_node.node_type,
                    )
                )

    return SynthesisDocumentResponse(
        id=str(node.id),
        concept=node.concept,
        node_type=node.node_type,
        visibility=node.visibility,
        definition=node.definition,
        model_id=(node.metadata_ or {}).get("model_id"),
        sentences=sentences,
        referenced_nodes=referenced_nodes,
        sub_syntheses=sub_syntheses,
        created_at=node.created_at.isoformat() if node.created_at else None,
    )


@router.get("/syntheses/{synthesis_id}", response_model=SynthesisDocumentResponse)
async def get_synthesis(
    synthesis_id: str,
    session: AsyncSession = Depends(get_db_session),
) -> SynthesisDocumentResponse:
    """Get a synthesis document with sentences, node links, and fact counts."""
    return await _get_synthesis_impl(session, synthesis_id)


async def _get_sentence_facts_impl(
    session: AsyncSession,
    synthesis_id: str,
    position: int,
) -> list[SentenceFactResponse]:
    """Shared implementation for getting sentence fact links."""
    try:
        nid = uuid.UUID(synthesis_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid synthesis ID")

    node = (await session.execute(select(Node).where(Node.id == nid))).scalar_one_or_none()
    if not node:
        raise HTTPException(status_code=404, detail="Synthesis not found")

    doc = _get_synthesis_doc(node)
    sentences = doc.get("sentences", [])

    if position < 0 or position >= len(sentences):
        raise HTTPException(status_code=404, detail="Sentence not found")

    sentence = sentences[position]
    fact_links = sentence.get("fact_links", [])
    if not fact_links:
        return []

    # Look up fact content and sources from the DB
    from kt_db.models import Fact, FactSource, RawSource

    fact_ids = [uuid.UUID(fl["fact_id"]) for fl in fact_links if fl.get("fact_id")]
    distance_map = {fl["fact_id"]: fl.get("distance", 0.0) for fl in fact_links}

    facts_result = await session.execute(select(Fact).where(Fact.id.in_(fact_ids)))
    facts_by_id = {str(f.id): f for f in facts_result.scalars().all()}

    # Get first source for each fact
    sources_result = await session.execute(
        select(FactSource, RawSource)
        .join(RawSource, FactSource.raw_source_id == RawSource.id)
        .where(FactSource.fact_id.in_(fact_ids))
    )
    source_map: dict[str, tuple[str, str, str]] = {}  # title, uri, author
    for fs, rs in sources_result.all():
        fid = str(fs.fact_id)
        if fid not in source_map:
            author = getattr(fs, "author_person", None) or getattr(fs, "author_org", None) or ""
            source_map[fid] = (rs.title or "", rs.uri or "", author)

    return [
        SentenceFactResponse(
            fact_id=fl.get("fact_id", ""),
            content=facts_by_id.get(fl["fact_id"], None).content if facts_by_id.get(fl["fact_id"]) else "",
            fact_type=facts_by_id.get(fl["fact_id"], None).fact_type if facts_by_id.get(fl["fact_id"]) else "",
            embedding_distance=distance_map.get(fl["fact_id"], 0.0),
            source_title=source_map.get(fl["fact_id"], ("", "", ""))[0],
            source_uri=source_map.get(fl["fact_id"], ("", "", ""))[1],
            author=source_map.get(fl["fact_id"], ("", "", ""))[2],
        )
        for fl in fact_links
        if fl.get("fact_id")
    ]


@router.get("/syntheses/{synthesis_id}/sentences/{position}/facts")
async def get_sentence_facts(
    synthesis_id: str,
    position: int,
    session: AsyncSession = Depends(get_db_session),
) -> list[SentenceFactResponse]:
    """Get fact links for a specific sentence."""
    return await _get_sentence_facts_impl(session, synthesis_id, position)


async def _get_synthesis_nodes_impl(
    session: AsyncSession,
    synthesis_id: str,
) -> list[SynthesisNodeResponse]:
    """Shared implementation for getting synthesis referenced nodes."""
    try:
        nid = uuid.UUID(synthesis_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid synthesis ID")

    node = (await session.execute(select(Node).where(Node.id == nid))).scalar_one_or_none()
    if not node:
        raise HTTPException(status_code=404, detail="Synthesis not found")

    doc = _get_synthesis_doc(node)
    return [
        SynthesisNodeResponse(
            node_id=rn.get("node_id", ""),
            concept=rn.get("concept", "unknown"),
            node_type=rn.get("node_type", "concept"),
        )
        for rn in doc.get("referenced_nodes", [])
    ]


@router.get("/syntheses/{synthesis_id}/nodes")
async def get_synthesis_nodes(
    synthesis_id: str,
    session: AsyncSession = Depends(get_db_session),
) -> list[SynthesisNodeResponse]:
    """Get all nodes referenced in a synthesis document."""
    return await _get_synthesis_nodes_impl(session, synthesis_id)


@router.delete("/syntheses/{synthesis_id}")
async def delete_synthesis(
    synthesis_id: str,
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    """Delete a synthesis node."""
    try:
        nid = uuid.UUID(synthesis_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid synthesis ID")

    node = (await session.execute(select(Node).where(Node.id == nid))).scalar_one_or_none()
    if not node or node.node_type not in ("synthesis", "supersynthesis"):
        raise HTTPException(status_code=404, detail="Synthesis not found")

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
