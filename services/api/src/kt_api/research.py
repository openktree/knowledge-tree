"""Ingest endpoints — upload files/links and process into the knowledge graph.

Two-step flow:
1. POST /ingest/prepare — upload files + links, process sources (chunking), return chunk counts
2. POST /ingest/{conversation_id}/confirm — user sets nav_budget, starts decomposition + agent
"""

from __future__ import annotations

import logging
import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession

from kt_api.auth.tokens import require_auth
from kt_api.dependencies import get_db_session, require_api_key
from kt_api.schemas import (
    AgentSelectRequest,
    AgentSelectResponse,
    AgentSelectStatusResponse,
    BottomUpPrepareRequest,
    BottomUpPrepareResponse,
    BottomUpProposedNodeResponse,
    BottomUpProposedPerspective,
    BottomUpSourceUrl,
    ChunkInfoResponse,
    ConversationResponse,
    IngestBuildRequest,
    IngestBuildResponse,
    IngestConfirmRequest,
    IngestDecomposeRequest,
    IngestDecomposeResponse,
    IngestPrepareResponse,
    IngestProposalsResponse,
    IngestSourceResponse,
    ProposedNodeAmbiguityResponse,
    ResearchSeedResponse,
    ResearchSummaryResponse,
)
from kt_config.settings import get_settings
from kt_db.models import User
from kt_db.repositories.conversations import ConversationRepository
from kt_db.repositories.ingest_sources import IngestSourceRepository

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["research"])

ALLOWED_MIME_TYPES = {
    "application/pdf",
    "text/plain",
    "image/png",
    "image/jpeg",
    "image/webp",
}

EXTENSION_TO_MIME = {
    ".pdf": "application/pdf",
    ".txt": "text/plain",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
}


def _resolve_mime(upload: UploadFile) -> str | None:
    """Resolve MIME type from upload, falling back to extension-based detection."""
    if upload.content_type and upload.content_type in ALLOWED_MIME_TYPES:
        return upload.content_type
    # Fall back to extension
    if upload.filename:
        ext = Path(upload.filename).suffix.lower()
        return EXTENSION_TO_MIME.get(ext)
    return None


def _conversation_to_response(conv: Any, messages: list[Any] | None = None) -> ConversationResponse:
    """Convert a Conversation ORM model to response schema."""
    from kt_api.conversations import _conversation_to_response as _orig

    return _orig(conv, messages)


# ── Endpoints ────────────────────────────────────────────────────────


@router.post("/research/prepare", response_model=IngestPrepareResponse)
async def prepare_ingest(
    files: list[UploadFile] = File(default=[]),
    links: str = Form(default=""),
    title: str = Form(default=""),
    user: User = Depends(require_auth),
    session: AsyncSession = Depends(get_db_session),
) -> IngestPrepareResponse:
    """Prepare an ingest: upload files, process sources, return chunk counts.

    This does file saving, text extraction, and chunking (fast, no LLM).
    Returns chunk counts so the user can see the decomposition cost before confirming.
    """
    settings = get_settings()
    max_file_size = settings.ingest_max_file_size_mb * 1024 * 1024

    # Parse links
    link_list: list[str] = []
    if links.strip():
        raw = links.strip()
        if raw.startswith("["):
            import json

            try:
                link_list = json.loads(raw)
            except json.JSONDecodeError:
                raise HTTPException(status_code=400, detail="Invalid JSON in links field")
        else:
            link_list = [l.strip() for l in raw.split("\n") if l.strip()]

    # Validate we have at least one source
    if not files and not link_list:
        raise HTTPException(status_code=400, detail="At least one file or link is required")

    # Validate file types
    for f in files:
        mime = _resolve_mime(f)
        if mime is None:
            raise HTTPException(
                status_code=400,
                detail=f"Unsupported file type: {f.filename}. Accepted: .pdf, .txt, .png, .jpg, .jpeg, .webp",
            )

    # Create conversation (mode=ingest, no messages yet)
    conv_repo = ConversationRepository(session)
    auto_title = title or _auto_title(files, link_list)
    conv = await conv_repo.create(title=auto_title, mode="ingest")
    conv_id = conv.id
    conv_id_str = str(conv_id)

    # Save files and create IngestSource records
    ingest_repo = IngestSourceRepository(session)
    upload_dir = Path(settings.ingest_upload_dir) / conv_id_str
    upload_dir.mkdir(parents=True, exist_ok=True)

    for f in files:
        mime = _resolve_mime(f)
        file_bytes = await f.read()

        if len(file_bytes) > max_file_size:
            raise HTTPException(
                status_code=400,
                detail=f"File {f.filename} exceeds {settings.ingest_max_file_size_mb}MB limit",
            )

        safe_name = _safe_filename(f.filename or "upload")
        file_path = upload_dir / safe_name
        file_path.write_bytes(file_bytes)

        stored_path = f"{conv_id_str}/{safe_name}"
        await ingest_repo.create(
            conversation_id=conv_id,
            source_type="file",
            original_name=f.filename or safe_name,
            stored_path=stored_path,
            mime_type=mime,
            file_size=len(file_bytes),
        )

    for link in link_list:
        await ingest_repo.create(
            conversation_id=conv_id,
            source_type="link",
            original_name=link,
        )

    await session.commit()

    # Process sources inline (text extraction + chunking, fast, no LLM)
    from kt_providers.fetcher import FileDataStore

    file_data_store = FileDataStore()
    from kt_worker_ingest.ingest.pipeline import process_ingest_sources

    processed = await process_ingest_sources(
        conv_id,
        session,
        file_data_store,
    )
    await session.commit()

    # Build chunk list and run LLM review
    from kt_models.gateway import ModelGateway
    from kt_worker_ingest.ingest.pipeline import build_chunk_list, review_chunks

    chunk_list = build_chunk_list(processed)

    # Review chunks with LLM (single fast call)
    api_key = require_api_key(user)
    gateway = ModelGateway(api_key=api_key)
    chunk_list = await review_chunks(chunk_list, gateway)

    # Build response data
    source_responses: list[IngestSourceResponse] = []
    db_sources = await ingest_repo.get_by_conversation(conv_id)
    for s in db_sources:
        source_responses.append(
            IngestSourceResponse(
                id=str(s.id),
                conversation_id=str(s.conversation_id),
                source_type=s.source_type,
                original_name=s.original_name,
                mime_type=s.mime_type,
                file_size=s.file_size,
                section_count=s.section_count,
                summary=s.summary,
                status=s.status,
                error=s.error,
                created_at=s.created_at,
            )
        )

    chunk_responses = [
        ChunkInfoResponse(
            source_id=c.source_id,
            source_name=c.source_name,
            chunk_index=c.chunk_index,
            char_count=c.char_count,
            preview=c.preview,
            is_image=c.is_image,
            recommended=c.recommended,
            reason=c.reason,
        )
        for c in chunk_list
    ]

    image_count = sum(1 for c in chunk_list if c.is_image)
    total_chunks = len(chunk_list) - image_count
    recommended_count = sum(1 for c in chunk_list if c.recommended)

    total_chars = sum(c.char_count for c in chunk_list if not c.is_image)
    total_token_estimate = total_chars // 4
    suggested_nav_budget = max(10, total_token_estimate // 1000)

    return IngestPrepareResponse(
        conversation_id=conv_id_str,
        sources=source_responses,
        chunks=chunk_responses,
        total_chunks=total_chunks,
        image_count=image_count,
        recommended_chunks=recommended_count,
        estimated_decompose_calls=len(chunk_list),
        title=auto_title,
        suggested_nav_budget=suggested_nav_budget,
        total_token_estimate=total_token_estimate,
    )


@router.post("/research/{conversation_id}/confirm", response_model=ConversationResponse)
async def confirm_ingest(
    conversation_id: str,
    body: IngestConfirmRequest,
    user: User = Depends(require_auth),
    session: AsyncSession = Depends(get_db_session),
) -> ConversationResponse:
    """Confirm an ingest: set nav_budget and start decomposition + agent."""
    try:
        conv_uuid = uuid.UUID(conversation_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid conversation ID")

    conv_repo = ConversationRepository(session)
    conv = await conv_repo.get_by_id(conv_uuid)
    if conv is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    if conv.mode != "ingest":
        raise HTTPException(status_code=400, detail="Conversation is not an ingest")

    # Check that sources exist
    ingest_repo = IngestSourceRepository(session)
    sources = await ingest_repo.get_by_conversation(conv_uuid)
    if not sources:
        raise HTTPException(status_code=400, detail="No sources found for this ingest")

    # Create user message (turn 0)
    file_count = sum(1 for s in sources if s.source_type == "file")
    link_count = sum(1 for s in sources if s.source_type == "link")
    parts = []
    if file_count:
        parts.append(f"{file_count} file{'s' if file_count > 1 else ''}")
    if link_count:
        parts.append(f"{link_count} link{'s' if link_count > 1 else ''}")
    user_content = f"Ingesting {' and '.join(parts)} (max {body.nav_budget} nodes)"

    user_msg = await conv_repo.add_message(
        conversation_id=conv_uuid,
        turn_number=0,
        role="user",
        content=user_content,
    )

    # Pending assistant message (turn 1)
    assistant_msg = await conv_repo.add_message(
        conversation_id=conv_uuid,
        turn_number=1,
        role="assistant",
        content="",
        nav_budget=body.nav_budget,
        explore_budget=0,  # no explore budget in new model
        status="pending",
    )

    await session.commit()

    from kt_hatchet.client import dispatch_workflow

    api_key = require_api_key(user)
    run_id = await dispatch_workflow(
        "ingest_confirm_wf",
        {
            "nav_budget": body.nav_budget,
            "selected_chunks": body.selected_chunks,
            "conversation_id": conversation_id,
            "message_id": str(assistant_msg.id),
            "api_key": api_key,
        },
        additional_metadata={
            "conversation_id": conversation_id,
            "message_id": str(assistant_msg.id),
        },
    )
    await conv_repo.update_message(assistant_msg.id, workflow_run_id=run_id)
    await session.commit()

    return _conversation_to_response(conv, [user_msg, assistant_msg])


@router.get("/research/{conversation_id}/sources", response_model=list[IngestSourceResponse])
async def get_ingest_sources(
    conversation_id: str,
    session: AsyncSession = Depends(get_db_session),
) -> list[IngestSourceResponse]:
    """List ingest sources for a conversation."""
    try:
        conv_uuid = uuid.UUID(conversation_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid conversation ID")

    repo = IngestSourceRepository(session)
    sources = await repo.get_by_conversation(conv_uuid)

    return [
        IngestSourceResponse(
            id=str(s.id),
            conversation_id=str(s.conversation_id),
            source_type=s.source_type,
            original_name=s.original_name,
            mime_type=s.mime_type,
            file_size=s.file_size,
            section_count=s.section_count,
            summary=s.summary,
            status=s.status,
            error=s.error,
            created_at=s.created_at,
        )
        for s in sources
    ]


@router.get("/research/{conversation_id}/sources/{source_id}/download")
async def download_ingest_source(
    conversation_id: str,
    source_id: str,
    session: AsyncSession = Depends(get_db_session),
) -> FileResponse:
    """Download the original file for an ingest source."""
    try:
        source_uuid = uuid.UUID(source_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid source ID")

    repo = IngestSourceRepository(session)
    source = await repo.get_by_id(source_uuid)

    if source is None:
        raise HTTPException(status_code=404, detail="Source not found")

    if source.source_type == "link":
        raise HTTPException(status_code=400, detail="Link sources cannot be downloaded")

    if not source.stored_path:
        raise HTTPException(status_code=404, detail="File not found")

    settings = get_settings()
    file_path = Path(settings.ingest_upload_dir) / source.stored_path

    if not file_path.exists():
        raise HTTPException(status_code=404, detail="File not found on disk")

    return FileResponse(
        path=str(file_path),
        filename=source.original_name,
        media_type=source.mime_type or "application/octet-stream",
    )


# ── Phased document ingest endpoints ─────────────────────────────────


@router.post(
    "/research/{conversation_id}/decompose",
    response_model=IngestDecomposeResponse,
)
async def decompose_ingest(
    conversation_id: str,
    body: IngestDecomposeRequest,
    user: User = Depends(require_auth),
    session: AsyncSession = Depends(get_db_session),
) -> IngestDecomposeResponse:
    """Phase 1: Decompose selected chunks, extract nodes, prioritize.

    Creates a new message pair and dispatches the decompose workflow.
    Returns an ack; frontend polls progress then fetches proposals.
    """
    try:
        conv_uuid = uuid.UUID(conversation_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid conversation ID")

    conv_repo = ConversationRepository(session)
    conv = await conv_repo.get_by_id(conv_uuid)
    if conv is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    if conv.mode != "ingest":
        raise HTTPException(status_code=400, detail="Conversation is not an ingest")

    next_turn = await conv_repo.get_next_turn_number(conv_uuid)

    # User message
    await conv_repo.add_message(
        conversation_id=conv_uuid,
        turn_number=next_turn,
        role="user",
        content="Decompose selected chunks",
    )

    # Pending assistant message — will hold proposals in metadata
    assistant_msg = await conv_repo.add_message(
        conversation_id=conv_uuid,
        turn_number=next_turn + 1,
        role="assistant",
        content="",
        status="pending",
    )

    await session.commit()

    from kt_hatchet.client import dispatch_workflow

    api_key = require_api_key(user)
    run_id = await dispatch_workflow(
        "ingest_decompose_wf",
        {
            "conversation_id": conversation_id,
            "message_id": str(assistant_msg.id),
            "selected_chunks": body.selected_chunks,
            "api_key": api_key,
        },
        additional_metadata={
            "conversation_id": conversation_id,
            "message_id": str(assistant_msg.id),
        },
    )
    await conv_repo.update_message(assistant_msg.id, workflow_run_id=run_id)
    await session.commit()

    return IngestDecomposeResponse(
        conversation_id=conversation_id,
        message_id=str(assistant_msg.id),
        status="running",
    )


@router.get(
    "/research/{conversation_id}/proposals",
    response_model=IngestProposalsResponse,
)
async def get_ingest_proposals(
    conversation_id: str,
    session: AsyncSession = Depends(get_db_session),
) -> IngestProposalsResponse:
    """Fetch Phase 1 results (proposed nodes) from completed decompose workflow."""
    try:
        conv_uuid = uuid.UUID(conversation_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid conversation ID")

    conv_repo = ConversationRepository(session)
    conv = await conv_repo.get_by_id(conv_uuid)
    if conv is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    if conv.mode != "ingest":
        raise HTTPException(status_code=400, detail="Conversation is not an ingest")

    # Find the latest assistant message with metadata
    messages = await conv_repo.get_messages(conv_uuid)
    metadata = None
    msg_id = None
    for msg in reversed(messages):
        if msg.role == "assistant" and msg.metadata_json:
            metadata = msg.metadata_json
            msg_id = str(msg.id)
            break

    if metadata is None:
        raise HTTPException(
            status_code=404,
            detail="No proposals found — Phase 1 may still be running",
        )

    # Parse proposed nodes from metadata
    proposed = metadata.get("proposed_nodes", [])
    proposed_nodes = []
    for n in proposed:
        if not isinstance(n, dict) or not n.get("name"):
            continue
        ambiguity_raw = n.get("ambiguity")
        ambiguity = ProposedNodeAmbiguityResponse(**ambiguity_raw) if isinstance(ambiguity_raw, dict) else None
        perspectives_raw = n.get("perspectives", [])
        perspectives = [
            BottomUpProposedPerspective(claim=p["claim"], antithesis=p["antithesis"])
            for p in perspectives_raw
            if isinstance(p, dict) and p.get("claim") and p.get("antithesis")
        ]
        proposed_nodes.append(
            BottomUpProposedNodeResponse(
                name=n.get("name", ""),
                node_type=n.get("node_type", "concept"),
                entity_subtype=n.get("entity_subtype"),
                priority=n.get("priority", 5),
                selected=n.get("selected", True),
                seed_key=n.get("seed_key", ""),
                existing_node_id=n.get("existing_node_id"),
                fact_count=n.get("fact_count", 0),
                aliases=n.get("aliases", []),
                ambiguity=ambiguity,
                perspectives=perspectives,
            )
        )

    return IngestProposalsResponse(
        conversation_id=conversation_id,
        message_id=msg_id or "",
        fact_count=metadata.get("fact_count", 0),
        proposed_nodes=proposed_nodes,
        content_summary=metadata.get("content_summary", ""),
        key_topics=metadata.get("key_topics", []),
        fact_type_counts=metadata.get("fact_type_counts", {}),
        agent_select_status=metadata.get("agent_select_status"),
    )


@router.post(
    "/research/{conversation_id}/build",
    response_model=IngestBuildResponse,
)
async def build_ingest(
    conversation_id: str,
    body: IngestBuildRequest,
    user: User = Depends(require_auth),
    session: AsyncSession = Depends(get_db_session),
) -> IngestBuildResponse:
    """Phase 2: Build user-confirmed nodes from document ingest."""
    try:
        conv_uuid = uuid.UUID(conversation_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid conversation ID")

    conv_repo = ConversationRepository(session)
    conv = await conv_repo.get_by_id(conv_uuid)
    if conv is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    if conv.mode != "ingest":
        raise HTTPException(status_code=400, detail="Conversation is not an ingest")

    if not body.selected_nodes:
        raise HTTPException(status_code=400, detail="No nodes selected")

    next_turn = await conv_repo.get_next_turn_number(conv_uuid)
    node_count = len(body.selected_nodes)

    await conv_repo.add_message(
        conversation_id=conv_uuid,
        turn_number=next_turn,
        role="user",
        content=f"Build {node_count} selected node{'s' if node_count != 1 else ''}",
    )

    assistant_msg = await conv_repo.add_message(
        conversation_id=conv_uuid,
        turn_number=next_turn + 1,
        role="assistant",
        content="",
        nav_budget=node_count,
        explore_budget=0,
        status="pending",
    )

    await session.commit()

    from kt_hatchet.models import ConfirmedNode, IngestBuildInput, ProposedPerspective
    from kt_hatchet.client import dispatch_workflow

    confirmed_nodes = [
        ConfirmedNode(
            name=n.name,
            node_type=n.node_type,
            entity_subtype=n.entity_subtype,
            seed_key=n.seed_key,
            existing_node_id=n.existing_node_id,
            perspectives=[ProposedPerspective(claim=p.claim, antithesis=p.antithesis) for p in n.perspectives],
        )
        for n in body.selected_nodes
    ]

    api_key = require_api_key(user)
    run_id = await dispatch_workflow(
        "ingest_build_wf",
        IngestBuildInput(
            selected_nodes=confirmed_nodes,
            conversation_id=str(conv_uuid),
            message_id=str(assistant_msg.id),
            api_key=api_key,
        ).model_dump(),
        additional_metadata={
            "conversation_id": str(conv_uuid),
            "message_id": str(assistant_msg.id),
        },
    )
    await conv_repo.update_message(assistant_msg.id, workflow_run_id=run_id)
    await session.commit()

    return IngestBuildResponse(
        conversation_id=conversation_id,
        message_id=str(assistant_msg.id),
        node_count=node_count,
        status="running",
    )


# ── Bottom-up ingest endpoints ───────────────────────────────────────


@router.post("/research/bottom-up/prepare", response_model=ConversationResponse)
async def bottom_up_prepare(
    body: BottomUpPrepareRequest,
    user: User = Depends(require_auth),
    session: AsyncSession = Depends(get_db_session),
) -> ConversationResponse:
    """Phase 1: Create conversation, dispatch fact gathering + node extraction.

    Returns a conversation. Frontend polls progress via SSE/progress endpoints.
    When complete, the assistant message metadata_json contains proposed nodes.
    """
    conv_repo = ConversationRepository(session)
    title = body.title or body.query[:200]
    conv = await conv_repo.create(title=title, mode="bottom_up_ingest")

    # User message (turn 0)
    user_msg = await conv_repo.add_message(
        conversation_id=conv.id,
        turn_number=0,
        role="user",
        content=body.query,
    )

    # Pending assistant message (turn 1) — will hold proposals in metadata
    assistant_msg = await conv_repo.add_message(
        conversation_id=conv.id,
        turn_number=1,
        role="assistant",
        content="",
        nav_budget=0,
        explore_budget=body.explore_budget,
        status="pending",
    )

    await session.commit()

    from kt_hatchet.client import dispatch_workflow

    api_key = require_api_key(user)
    run_id = await dispatch_workflow(
        "bottom_up_prepare_wf",
        {
            "query": body.query,
            "explore_budget": body.explore_budget,
            "conversation_id": str(conv.id),
            "message_id": str(assistant_msg.id),
            "api_key": api_key,
        },
        additional_metadata={
            "conversation_id": str(conv.id),
            "message_id": str(assistant_msg.id),
        },
    )
    await conv_repo.update_message(assistant_msg.id, workflow_run_id=run_id)
    await session.commit()

    return _conversation_to_response(conv, [user_msg, assistant_msg])


@router.get(
    "/research/{conversation_id}/bottom-up/proposals",
    response_model=BottomUpPrepareResponse,
)
async def get_bottom_up_proposals(
    conversation_id: str,
    session: AsyncSession = Depends(get_db_session),
) -> BottomUpPrepareResponse:
    """Fetch Phase 1 results (proposed nodes) from completed prepare workflow.

    Reads the metadata_json stored on the assistant message.
    """
    try:
        conv_uuid = uuid.UUID(conversation_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid conversation ID")

    conv_repo = ConversationRepository(session)
    conv = await conv_repo.get_by_id(conv_uuid)
    if conv is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    if conv.mode != "bottom_up_ingest":
        raise HTTPException(status_code=400, detail="Conversation is not a bottom-up ingest")

    # Find the latest assistant message with metadata
    messages = await conv_repo.get_messages(conv_uuid)
    metadata = None
    msg_id = None
    for msg in reversed(messages):
        if msg.role == "assistant" and msg.metadata_json:
            metadata = msg.metadata_json
            msg_id = str(msg.id)
            break

    if metadata is None:
        raise HTTPException(status_code=404, detail="No proposals found — Phase 1 may still be running")

    # Parse proposed nodes from metadata
    proposed = metadata.get("proposed_nodes", [])
    proposed_nodes = []
    for n in proposed:
        if not isinstance(n, dict) or not n.get("name"):
            continue
        ambiguity_raw = n.get("ambiguity")
        ambiguity = ProposedNodeAmbiguityResponse(**ambiguity_raw) if isinstance(ambiguity_raw, dict) else None
        perspectives_raw = n.get("perspectives", [])
        perspectives = [
            BottomUpProposedPerspective(claim=p["claim"], antithesis=p["antithesis"])
            for p in perspectives_raw
            if isinstance(p, dict) and p.get("claim") and p.get("antithesis")
        ]
        proposed_nodes.append(
            BottomUpProposedNodeResponse(
                name=n.get("name", ""),
                node_type=n.get("node_type", "concept"),
                entity_subtype=n.get("entity_subtype"),
                priority=n.get("priority", 5),
                selected=n.get("selected", True),
                seed_key=n.get("seed_key", ""),
                existing_node_id=n.get("existing_node_id"),
                fact_count=n.get("fact_count", 0),
                aliases=n.get("aliases", []),
                ambiguity=ambiguity,
                perspectives=perspectives,
            )
        )

    # Parse source URLs from metadata
    raw_sources = metadata.get("source_urls", [])
    source_urls = [
        BottomUpSourceUrl(url=s.get("url", ""), title=s.get("title", ""))
        for s in raw_sources
        if isinstance(s, dict) and s.get("url")
    ]

    return BottomUpPrepareResponse(
        conversation_id=conversation_id,
        message_id=msg_id or "",
        fact_count=metadata.get("fact_count", 0),
        source_count=len(source_urls) or metadata.get("source_count", 0),
        fact_previews=metadata.get("fact_previews", []),
        proposed_nodes=proposed_nodes,
        content_summary=metadata.get("content_summary", ""),
        explore_used=metadata.get("explore_used", 0),
        source_urls=source_urls,
        agent_select_status=metadata.get("agent_select_status"),
    )


@router.get(
    "/research/{conversation_id}/summary",
    response_model=ResearchSummaryResponse,
)
async def get_research_summary(
    conversation_id: str,
    session: AsyncSession = Depends(get_db_session),
) -> ResearchSummaryResponse:
    """Fetch research summary (facts + seeds) from completed prepare workflow.

    Works for both bottom_up_ingest and ingest conversations.
    Reads the metadata_json stored on the assistant message.
    """
    try:
        conv_uuid = uuid.UUID(conversation_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid conversation ID")

    conv_repo = ConversationRepository(session)
    conv = await conv_repo.get_by_id(conv_uuid)
    if conv is None:
        raise HTTPException(status_code=404, detail="Conversation not found")

    # Find the latest assistant message with metadata
    messages = await conv_repo.get_messages(conv_uuid)
    metadata = None
    msg_id = None
    for msg in reversed(messages):
        if msg.role == "assistant" and msg.metadata_json:
            metadata = msg.metadata_json
            msg_id = str(msg.id)
            break

    if metadata is None:
        raise HTTPException(
            status_code=404,
            detail="No summary found — research may still be running",
        )

    # Parse seeds from metadata
    raw_seeds = metadata.get("seeds", [])
    seeds = [
        ResearchSeedResponse(
            key=s.get("key", ""),
            name=s.get("name", ""),
            node_type=s.get("node_type", "concept"),
            fact_count=s.get("fact_count", 0),
            aliases=s.get("aliases", []),
            status=s.get("status", "active"),
            entity_subtype=s.get("entity_subtype"),
        )
        for s in raw_seeds
        if isinstance(s, dict) and s.get("key")
    ]

    # Parse source URLs
    raw_sources = metadata.get("source_urls", [])
    source_urls = [
        BottomUpSourceUrl(url=s.get("url", ""), title=s.get("title", ""))
        for s in raw_sources
        if isinstance(s, dict) and s.get("url")
    ]

    return ResearchSummaryResponse(
        conversation_id=conversation_id,
        message_id=msg_id or "",
        fact_count=metadata.get("fact_count", 0),
        source_count=len(source_urls) or metadata.get("source_count", 0),
        source_urls=source_urls,
        seeds=seeds,
        content_summary=metadata.get("content_summary", ""),
        explore_used=metadata.get("explore_used", 0),
    )


@router.post(
    "/research/{conversation_id}/agent-select",
    response_model=AgentSelectResponse,
)
async def agent_select(
    conversation_id: str,
    body: AgentSelectRequest,
    user: User = Depends(require_auth),
    session: AsyncSession = Depends(get_db_session),
) -> AgentSelectResponse:
    """Dispatch agent-assisted node selection for proposed nodes.

    The agent processes the proposed nodes in batches of 100, using LLM
    tool calling to select relevant nodes, skip duplicates, and edit
    names/types. Results are stored back on the conversation message metadata.
    """
    try:
        conv_uuid = uuid.UUID(conversation_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid conversation ID")

    conv_repo = ConversationRepository(session)
    conv = await conv_repo.get_by_id(conv_uuid)
    if conv is None:
        raise HTTPException(status_code=404, detail="Conversation not found")

    # Find the assistant message with proposed nodes
    messages = await conv_repo.get_messages(conv_uuid)
    metadata = None
    msg_id = None
    for msg in reversed(messages):
        if msg.role == "assistant" and msg.metadata_json:
            metadata = dict(msg.metadata_json)  # Copy to avoid SQLAlchemy identity issue
            msg_id = str(msg.id)
            break

    if metadata is None or not metadata.get("proposed_nodes"):
        raise HTTPException(
            status_code=404,
            detail="No proposed nodes found — Phase 1 may still be running",
        )

    # Parse proposed nodes from metadata
    from kt_hatchet.models import AgentSelectInput, ProposedNode, ProposedPerspective

    proposed_nodes = []
    for n in metadata.get("proposed_nodes", []):
        if not isinstance(n, dict) or not n.get("name"):
            continue
        perspectives = [
            ProposedPerspective(claim=p["claim"], antithesis=p["antithesis"])
            for p in n.get("perspectives", [])
            if isinstance(p, dict) and p.get("claim") and p.get("antithesis")
        ]
        proposed_nodes.append(
            ProposedNode(
                name=n["name"],
                node_type=n.get("node_type", "concept"),
                entity_subtype=n.get("entity_subtype"),
                priority=n.get("priority", 5),
                selected=n.get("selected", True),
                seed_key=n.get("seed_key", ""),
                existing_node_id=n.get("existing_node_id"),
                perspectives=perspectives,
            )
        )

    if not proposed_nodes:
        raise HTTPException(status_code=400, detail="No valid proposed nodes found")

    # Use the query or conversation title as fallback instructions
    instructions = body.instructions
    if not instructions:
        instructions = conv.title or ""

    # Mark agent selection as running in metadata so frontend can poll
    metadata["agent_select_status"] = "running"
    await conv_repo.update_message(uuid.UUID(msg_id), metadata_json=metadata)
    await session.commit()

    from kt_hatchet.client import dispatch_workflow

    api_key = require_api_key(user)
    await dispatch_workflow(
        "agent_select_wf",
        {
            "proposed_nodes": proposed_nodes,
            "max_select": body.max_select,
            "instructions": instructions,
            "conversation_id": str(conv_uuid),
            "message_id": msg_id or "",
            "api_key": api_key,
        },
        additional_metadata={
            "conversation_id": str(conv_uuid),
            "message_id": msg_id,
        },
    )

    return AgentSelectResponse(
        conversation_id=conversation_id,
        message_id=msg_id or "",
        status="running",
    )


@router.get(
    "/research/{conversation_id}/agent-select/status",
    response_model=AgentSelectStatusResponse,
)
async def agent_select_status(
    conversation_id: str,
    session: AsyncSession = Depends(get_db_session),
) -> AgentSelectStatusResponse:
    """Check agent-assisted node selection status."""
    try:
        conv_uuid = uuid.UUID(conversation_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid conversation ID")

    conv_repo = ConversationRepository(session)
    messages = await conv_repo.get_messages(conv_uuid)

    for msg in reversed(messages):
        if msg.role == "assistant" and msg.metadata_json:
            status = msg.metadata_json.get("agent_select_status", "not_started")
            return AgentSelectStatusResponse(status=status)

    return AgentSelectStatusResponse(status="not_started")


# ── Helpers ──────────────────────────────────────────────────────────


def _auto_title(files: list[UploadFile], links: list[str]) -> str:
    """Generate an auto-title from file names and links."""
    parts: list[str] = []
    for f in files[:2]:
        parts.append(f.filename or "upload")
    for link in links[:2]:
        # Shorten URL for title
        short = link.split("//")[-1][:50]
        parts.append(short)

    title = ", ".join(parts)
    total = len(files) + len(links)
    if total > 2:
        title += f" (+{total - 2} more)"
    return title[:200]


def _safe_filename(name: str) -> str:
    """Sanitize a filename for safe storage."""
    import re

    # Keep only alphanumeric, dots, hyphens, underscores
    safe = re.sub(r"[^\w.\-]", "_", name)
    # Prevent directory traversal
    safe = safe.replace("..", "_")
    return safe or "upload"
