"""Graph-scoped synthesis endpoints.

Mirrors /api/v1/syntheses scoped to a specific graph via
/api/v1/graphs/{graph_slug}/syntheses.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from kt_api.graph_context import GraphContext, get_graph_context, require_writer
from kt_api.syntheses import (
    CreateSuperSynthesisRequest,
    CreateSynthesisRequest,
    PaginatedSynthesesResponse,
    SentenceFactResponse,
    SynthesisDocumentResponse,
    SynthesisNodeResponse,
    _get_sentence_facts_impl,
    _get_synthesis_impl,
    _get_synthesis_nodes_impl,
    _list_syntheses_impl,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/graphs/{graph_slug}", tags=["graph-syntheses"])


@router.post("/syntheses")
async def create_graph_synthesis(
    body: CreateSynthesisRequest,
    ctx: GraphContext = Depends(get_graph_context),
) -> dict[str, Any]:
    """Create a new synthesis in a specific graph."""
    require_writer(ctx)
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
    require_writer(ctx)
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
            "graph_id": str(ctx.graph.id),
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
        return await _list_syntheses_impl(session, offset, limit, visibility)


@router.get("/syntheses/{synthesis_id}", response_model=SynthesisDocumentResponse)
async def get_graph_synthesis(
    synthesis_id: str,
    ctx: GraphContext = Depends(get_graph_context),
) -> SynthesisDocumentResponse:
    """Get a synthesis document from a specific graph."""
    async with ctx.graph_session_factory() as session:
        return await _get_synthesis_impl(session, synthesis_id)


@router.get("/syntheses/{synthesis_id}/sentences/{position}/facts")
async def get_graph_sentence_facts(
    synthesis_id: str,
    position: int,
    ctx: GraphContext = Depends(get_graph_context),
) -> list[SentenceFactResponse]:
    """Get fact links for a specific sentence in a graph-scoped synthesis."""
    async with ctx.graph_session_factory() as session:
        return await _get_sentence_facts_impl(session, synthesis_id, position)


@router.get("/syntheses/{synthesis_id}/nodes")
async def get_graph_synthesis_nodes(
    synthesis_id: str,
    ctx: GraphContext = Depends(get_graph_context),
) -> list[SynthesisNodeResponse]:
    """Get all nodes referenced in a graph-scoped synthesis document."""
    async with ctx.graph_session_factory() as session:
        return await _get_synthesis_nodes_impl(session, synthesis_id)
