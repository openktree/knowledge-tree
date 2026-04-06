"""Graph-scoped research (ingest) endpoints.

Mirrors /api/v1/research/... scoped to a specific graph via
/api/v1/graphs/{graph_slug}/research/.... Each endpoint is a thin wrapper
that delegates to the ``_impl`` functions in ``kt_api.research``.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, UploadFile
from fastapi.responses import FileResponse

from kt_api.auth.tokens import require_auth
from kt_api.graph_context import GraphContext, get_graph_context, require_writer
from kt_api.research import (
    _agent_select_impl,
    _agent_select_status_impl,
    _bottom_up_prepare_impl,
    _build_ingest_impl,
    _confirm_ingest_impl,
    _decompose_ingest_impl,
    _download_ingest_source_impl,
    _get_bottom_up_proposals_impl,
    _get_ingest_proposals_impl,
    _get_ingest_sources_impl,
    _get_research_summary_impl,
    _prepare_ingest_impl,
)
from kt_api.schemas import (
    AgentSelectRequest,
    AgentSelectResponse,
    AgentSelectStatusResponse,
    BottomUpPrepareRequest,
    BottomUpPrepareResponse,
    ConversationResponse,
    IngestBuildRequest,
    IngestBuildResponse,
    IngestConfirmRequest,
    IngestDecomposeRequest,
    IngestDecomposeResponse,
    IngestPrepareResponse,
    IngestProposalsResponse,
    IngestSourceResponse,
    ResearchSummaryResponse,
)
from kt_db.models import User

router = APIRouter(prefix="/api/v1/graphs/{graph_slug}/research", tags=["graph-research"])


@router.post("/prepare", response_model=IngestPrepareResponse)
async def prepare_graph_ingest(
    files: list[UploadFile] = File(default=[]),
    links: str = Form(default=""),
    title: str = Form(default=""),
    user: User = Depends(require_auth),
    ctx: GraphContext = Depends(get_graph_context),
) -> IngestPrepareResponse:
    """Prepare an ingest in a specific graph."""
    require_writer(ctx)
    async with ctx.graph_session_factory() as session:
        return await _prepare_ingest_impl(session, files, links, title, user, graph_id=str(ctx.graph.id))


@router.post("/{conversation_id}/confirm", response_model=ConversationResponse)
async def confirm_graph_ingest(
    conversation_id: str,
    body: IngestConfirmRequest,
    user: User = Depends(require_auth),
    ctx: GraphContext = Depends(get_graph_context),
) -> ConversationResponse:
    """Confirm an ingest in a specific graph."""
    require_writer(ctx)
    async with ctx.graph_session_factory() as session:
        return await _confirm_ingest_impl(session, conversation_id, body, user, graph_id=str(ctx.graph.id))


@router.get("/{conversation_id}/sources", response_model=list[IngestSourceResponse])
async def get_graph_ingest_sources(
    conversation_id: str,
    ctx: GraphContext = Depends(get_graph_context),
) -> list[IngestSourceResponse]:
    """List ingest sources for a conversation in a specific graph."""
    async with ctx.graph_session_factory() as session:
        return await _get_ingest_sources_impl(session, conversation_id)


@router.get("/{conversation_id}/sources/{source_id}/download")
async def download_graph_ingest_source(
    conversation_id: str,
    source_id: str,
    ctx: GraphContext = Depends(get_graph_context),
) -> FileResponse:
    """Download the original file for an ingest source in a specific graph."""
    async with ctx.graph_session_factory() as session:
        return await _download_ingest_source_impl(session, conversation_id, source_id)


@router.post("/{conversation_id}/decompose", response_model=IngestDecomposeResponse)
async def decompose_graph_ingest(
    conversation_id: str,
    body: IngestDecomposeRequest,
    user: User = Depends(require_auth),
    ctx: GraphContext = Depends(get_graph_context),
) -> IngestDecomposeResponse:
    """Phase 1: Decompose selected chunks in a specific graph."""
    require_writer(ctx)
    async with ctx.graph_session_factory() as session:
        return await _decompose_ingest_impl(session, conversation_id, body, user, graph_id=str(ctx.graph.id))


@router.get("/{conversation_id}/proposals", response_model=IngestProposalsResponse)
async def get_graph_ingest_proposals(
    conversation_id: str,
    ctx: GraphContext = Depends(get_graph_context),
) -> IngestProposalsResponse:
    """Fetch Phase 1 results (proposed nodes) in a specific graph."""
    async with ctx.graph_session_factory() as session:
        return await _get_ingest_proposals_impl(session, conversation_id)


@router.post("/{conversation_id}/build", response_model=IngestBuildResponse)
async def build_graph_ingest(
    conversation_id: str,
    body: IngestBuildRequest,
    user: User = Depends(require_auth),
    ctx: GraphContext = Depends(get_graph_context),
) -> IngestBuildResponse:
    """Phase 2: Build user-confirmed nodes in a specific graph."""
    require_writer(ctx)
    async with ctx.graph_session_factory() as session:
        return await _build_ingest_impl(session, conversation_id, body, user, graph_id=str(ctx.graph.id))


@router.post("/bottom-up/prepare", response_model=ConversationResponse)
async def bottom_up_graph_prepare(
    body: BottomUpPrepareRequest,
    user: User = Depends(require_auth),
    ctx: GraphContext = Depends(get_graph_context),
) -> ConversationResponse:
    """Phase 1: Bottom-up discovery in a specific graph."""
    require_writer(ctx)
    async with ctx.graph_session_factory() as session:
        return await _bottom_up_prepare_impl(session, body, user, graph_id=str(ctx.graph.id))


@router.get("/{conversation_id}/bottom-up/proposals", response_model=BottomUpPrepareResponse)
async def get_graph_bottom_up_proposals(
    conversation_id: str,
    ctx: GraphContext = Depends(get_graph_context),
) -> BottomUpPrepareResponse:
    """Fetch bottom-up proposals in a specific graph."""
    async with ctx.graph_session_factory() as session:
        return await _get_bottom_up_proposals_impl(session, conversation_id)


@router.post("/{conversation_id}/agent-select", response_model=AgentSelectResponse)
async def graph_agent_select(
    conversation_id: str,
    body: AgentSelectRequest,
    user: User = Depends(require_auth),
    ctx: GraphContext = Depends(get_graph_context),
) -> AgentSelectResponse:
    """Dispatch agent-assisted node selection in a specific graph."""
    require_writer(ctx)
    async with ctx.graph_session_factory() as session:
        return await _agent_select_impl(session, conversation_id, body, user, graph_id=str(ctx.graph.id))


@router.get("/{conversation_id}/agent-select/status", response_model=AgentSelectStatusResponse)
async def graph_agent_select_status(
    conversation_id: str,
    ctx: GraphContext = Depends(get_graph_context),
) -> AgentSelectStatusResponse:
    """Check agent-assisted node selection status in a specific graph."""
    async with ctx.graph_session_factory() as session:
        return await _agent_select_status_impl(session, conversation_id)


@router.get("/{conversation_id}/summary", response_model=ResearchSummaryResponse)
async def get_graph_research_summary(
    conversation_id: str,
    ctx: GraphContext = Depends(get_graph_context),
) -> ResearchSummaryResponse:
    """Fetch research summary in a specific graph."""
    async with ctx.graph_session_factory() as session:
        return await _get_research_summary_impl(session, conversation_id)
