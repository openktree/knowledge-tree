"""Graph-scoped research (ingest) endpoints.

Mirrors /api/v1/research/... scoped to a specific graph via
/api/v1/graphs/{graph_slug}/research/.... Each endpoint is a thin wrapper
that delegates to the ``_impl`` functions in ``kt_api.research``.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, UploadFile
from fastapi.responses import FileResponse

from kt_api.auth.permissions import require_graph_permission
from kt_api.auth.tokens import require_auth
from kt_api.graph_context import GraphContext, get_graph_context
from kt_api.progress import _get_message_progress_impl
from kt_api.research import (
    _bottom_up_prepare_impl,
    _confirm_ingest_impl,
    _decompose_ingest_impl,
    _download_ingest_source_impl,
    _get_bottom_up_proposals_impl,
    _get_ingest_sources_impl,
    _get_research_summary_impl,
    _prepare_ingest_impl,
)
from kt_api.schemas import (
    BottomUpPrepareRequest,
    BottomUpPrepareResponse,
    ConversationResponse,
    IngestConfirmRequest,
    IngestDecomposeRequest,
    IngestDecomposeResponse,
    IngestPrepareResponse,
    IngestSourceResponse,
    ProgressResponse,
    ResearchSummaryResponse,
)
from kt_db.models import User
from kt_rbac import Permission

router = APIRouter(prefix="/api/v1/graphs/{graph_slug}/research", tags=["graph-research"])


@router.post("/prepare", response_model=IngestPrepareResponse)
async def prepare_graph_ingest(
    files: list[UploadFile] = File(default=[]),
    links: str = Form(default=""),
    title: str = Form(default=""),
    user: User = Depends(require_auth),
    ctx: GraphContext = Depends(require_graph_permission(Permission.GRAPH_WRITE)),
) -> IngestPrepareResponse:
    """Prepare an ingest in a specific graph."""
    async with ctx.graph_session_factory() as session:
        return await _prepare_ingest_impl(session, files, links, title, user, graph_id=str(ctx.graph.id))


@router.post("/{conversation_id}/confirm", response_model=ConversationResponse)
async def confirm_graph_ingest(
    conversation_id: str,
    body: IngestConfirmRequest,
    user: User = Depends(require_auth),
    ctx: GraphContext = Depends(require_graph_permission(Permission.GRAPH_WRITE)),
) -> ConversationResponse:
    """Confirm an ingest in a specific graph."""
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
    ctx: GraphContext = Depends(require_graph_permission(Permission.GRAPH_WRITE)),
) -> IngestDecomposeResponse:
    """Decompose sources and auto-build nodes in a specific graph."""
    async with ctx.graph_session_factory() as session:
        return await _decompose_ingest_impl(session, conversation_id, body, user, graph_id=str(ctx.graph.id))


@router.post("/bottom-up/prepare", response_model=ConversationResponse)
async def bottom_up_graph_prepare(
    body: BottomUpPrepareRequest,
    user: User = Depends(require_auth),
    ctx: GraphContext = Depends(require_graph_permission(Permission.GRAPH_WRITE)),
) -> ConversationResponse:
    """Phase 1: Bottom-up discovery in a specific graph."""
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


@router.get("/{conversation_id}/summary", response_model=ResearchSummaryResponse)
async def get_graph_research_summary(
    conversation_id: str,
    ctx: GraphContext = Depends(get_graph_context),
) -> ResearchSummaryResponse:
    """Fetch research summary in a specific graph."""
    async with ctx.graph_session_factory() as session:
        return await _get_research_summary_impl(session, conversation_id)


@router.get(
    "/{conversation_id}/messages/{message_id}/progress",
    response_model=ProgressResponse,
)
async def get_graph_message_progress(
    conversation_id: str,
    message_id: str,
    user: User = Depends(require_auth),
    ctx: GraphContext = Depends(require_graph_permission(Permission.GRAPH_READ)),
) -> ProgressResponse:
    """Get live progress for a workflow in a specific graph."""
    async with ctx.graph_session_factory() as session:
        return await _get_message_progress_impl(conversation_id, message_id, session)
