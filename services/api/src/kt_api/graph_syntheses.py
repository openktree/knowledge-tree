"""Graph-scoped synthesis endpoints.

Mirrors /api/v1/syntheses scoped to a specific graph via
/api/v1/graphs/{graph_slug}/syntheses.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select

from kt_api.graph_context import GraphContext, get_graph_context
from kt_api.syntheses import (
    CreateSuperSynthesisRequest,
    CreateSynthesisRequest,
    PaginatedSynthesesResponse,
    SentenceFactResponse,
    SynthesisDocumentResponse,
    SynthesisListItem,
    SynthesisNodeResponse,
    SynthesisSentenceResponse,
    _get_synthesis_doc,
)
from kt_db.models import Node

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/graphs/{graph_slug}", tags=["graph-syntheses"])


@router.post("/syntheses")
async def create_graph_synthesis(
    body: CreateSynthesisRequest,
    ctx: GraphContext = Depends(get_graph_context),
) -> dict[str, Any]:
    """Create a new synthesis in a specific graph."""
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
                "graph_id": str(ctx.graph.id),
            },
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    return {"status": "pending", "workflow_run_id": run_id, "topic": body.topic}


@router.post("/super-syntheses")
async def create_graph_super_synthesis(
    body: CreateSuperSynthesisRequest,
    ctx: GraphContext = Depends(get_graph_context),
) -> dict[str, Any]:
    """Create a new super-synthesis in a specific graph."""
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
                "graph_id": str(ctx.graph.id),
            },
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    return {"status": "pending", "workflow_run_id": run_id, "topic": body.topic}


@router.get("/syntheses", response_model=PaginatedSynthesesResponse)
async def list_graph_syntheses(
    ctx: GraphContext = Depends(get_graph_context),
    offset: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    visibility: str | None = None,
) -> PaginatedSynthesesResponse:
    """List synthesis documents in a specific graph."""
    async with ctx.graph_session_factory() as session:
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


@router.get("/syntheses/{synthesis_id}", response_model=SynthesisDocumentResponse)
async def get_graph_synthesis(
    synthesis_id: str,
    ctx: GraphContext = Depends(get_graph_context),
) -> SynthesisDocumentResponse:
    """Get a synthesis document from a specific graph."""
    try:
        nid = uuid.UUID(synthesis_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid synthesis ID")

    async with ctx.graph_session_factory() as session:
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


@router.get("/syntheses/{synthesis_id}/sentences/{position}/facts")
async def get_graph_sentence_facts(
    synthesis_id: str,
    position: int,
    ctx: GraphContext = Depends(get_graph_context),
) -> list[SentenceFactResponse]:
    """Get fact links for a specific sentence in a graph-scoped synthesis."""
    try:
        nid = uuid.UUID(synthesis_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid synthesis ID")

    async with ctx.graph_session_factory() as session:
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

        from kt_db.models import Fact, FactSource, RawSource

        fact_ids = [uuid.UUID(fl["fact_id"]) for fl in fact_links if fl.get("fact_id")]
        distance_map = {fl["fact_id"]: fl.get("distance", 0.0) for fl in fact_links}

        facts_result = await session.execute(select(Fact).where(Fact.id.in_(fact_ids)))
        facts_by_id = {str(f.id): f for f in facts_result.scalars().all()}

        sources_result = await session.execute(
            select(FactSource, RawSource)
            .join(RawSource, FactSource.raw_source_id == RawSource.id)
            .where(FactSource.fact_id.in_(fact_ids))
        )
        source_map: dict[str, tuple[str, str, str]] = {}
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


@router.get("/syntheses/{synthesis_id}/nodes")
async def get_graph_synthesis_nodes(
    synthesis_id: str,
    ctx: GraphContext = Depends(get_graph_context),
) -> list[SynthesisNodeResponse]:
    """Get all nodes referenced in a graph-scoped synthesis document."""
    try:
        nid = uuid.UUID(synthesis_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid synthesis ID")

    async with ctx.graph_session_factory() as session:
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
