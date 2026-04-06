"""Graph-scoped research (ingest) endpoints.

Mirrors /api/v1/research/... scoped to a specific graph via
/api/v1/graphs/{graph_slug}/research/.... Uses GraphContext for session routing.
"""

from __future__ import annotations

import logging
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse

from kt_api.auth.tokens import require_auth
from kt_api.dependencies import require_api_key
from kt_api.graph_context import GraphContext, get_graph_context, require_writer
from kt_api.research import (
    BottomUpProposedPerspective,
    ProposedNodeAmbiguityResponse,
    _auto_title,
    _build_summary_from_metadata,
    _conversation_to_response,
    _resolve_mime,
    _safe_filename,
)
from kt_api.schemas import (
    AgentSelectRequest,
    AgentSelectResponse,
    AgentSelectStatusResponse,
    BottomUpPrepareRequest,
    BottomUpPrepareResponse,
    BottomUpProposedNodeResponse,
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
    ResearchSummaryResponse,
)
from kt_config.settings import get_settings
from kt_db.models import User
from kt_db.repositories.conversations import ConversationRepository
from kt_db.repositories.ingest_sources import IngestSourceRepository
from kt_db.repositories.research_reports import ResearchReportRepository

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/graphs/{graph_slug}/research", tags=["graph-research"])


# ── Endpoints ────────────────────────────────────────────────────────


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

    if not files and not link_list:
        raise HTTPException(status_code=400, detail="At least one file or link is required")

    for f in files:
        mime = _resolve_mime(f)
        if mime is None:
            raise HTTPException(
                status_code=400,
                detail=f"Unsupported file type: {f.filename}. Accepted: .pdf, .txt, .png, .jpg, .jpeg, .webp",
            )

    async with ctx.graph_session_factory() as session:
        conv_repo = ConversationRepository(session)
        auto_title = title or _auto_title(files, link_list)
        conv = await conv_repo.create(title=auto_title, mode="ingest")
        conv_id = conv.id
        conv_id_str = str(conv_id)

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

        # Process sources inline (text extraction + chunking)
        from kt_providers.fetcher import FileDataStore

        file_data_store = FileDataStore()
        from kt_worker_ingest.ingest.pipeline import process_ingest_sources

        processed = await process_ingest_sources(conv_id, session, file_data_store)
        await session.commit()

        from kt_models.gateway import ModelGateway
        from kt_worker_ingest.ingest.pipeline import build_chunk_list, review_chunks

        chunk_list = build_chunk_list(processed)

        api_key = require_api_key(user)
        gateway = ModelGateway(api_key=api_key)
        chunk_list = await review_chunks(chunk_list, gateway)

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


@router.post("/{conversation_id}/confirm", response_model=ConversationResponse)
async def confirm_graph_ingest(
    conversation_id: str,
    body: IngestConfirmRequest,
    user: User = Depends(require_auth),
    ctx: GraphContext = Depends(get_graph_context),
) -> ConversationResponse:
    """Confirm an ingest in a specific graph."""
    require_writer(ctx)
    try:
        conv_uuid = uuid.UUID(conversation_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid conversation ID")

    async with ctx.graph_session_factory() as session:
        conv_repo = ConversationRepository(session)
        conv = await conv_repo.get_by_id(conv_uuid)
        if conv is None:
            raise HTTPException(status_code=404, detail="Conversation not found")
        if conv.mode != "ingest":
            raise HTTPException(status_code=400, detail="Conversation is not an ingest")

        ingest_repo = IngestSourceRepository(session)
        sources = await ingest_repo.get_by_conversation(conv_uuid)
        if not sources:
            raise HTTPException(status_code=400, detail="No sources found for this ingest")

        file_count = sum(1 for s in sources if s.source_type == "file")
        link_count = sum(1 for s in sources if s.source_type == "link")
        parts = []
        if file_count:
            parts.append(f"{file_count} file{'s' if file_count > 1 else ''}")
        if link_count:
            parts.append(f"{link_count} link{'s' if link_count > 1 else ''}")
        user_content = f"Ingesting {' and '.join(parts)} (max {body.nav_budget} nodes)"

        user_msg = await conv_repo.add_message(
            conversation_id=conv_uuid, turn_number=0, role="user", content=user_content
        )
        assistant_msg = await conv_repo.add_message(
            conversation_id=conv_uuid,
            turn_number=1,
            role="assistant",
            content="",
            nav_budget=body.nav_budget,
            explore_budget=0,
            status="pending",
        )
        await session.commit()

        from kt_hatchet.client import dispatch_workflow

        require_api_key(user)
        run_id = await dispatch_workflow(
            "ingest_confirm",
            {
                "nav_budget": body.nav_budget,
                "selected_chunks": body.selected_chunks,
                "conversation_id": conversation_id,
                "message_id": str(assistant_msg.id),
                "user_id": str(user.id),
                "graph_id": str(ctx.graph.id),
            },
            additional_metadata={
                "conversation_id": conversation_id,
                "message_id": str(assistant_msg.id),
            },
        )
        await conv_repo.update_message(assistant_msg.id, workflow_run_id=run_id)
        await session.commit()

        return _conversation_to_response(conv, [user_msg, assistant_msg])


@router.get("/{conversation_id}/sources", response_model=list[IngestSourceResponse])
async def get_graph_ingest_sources(
    conversation_id: str,
    ctx: GraphContext = Depends(get_graph_context),
) -> list[IngestSourceResponse]:
    """List ingest sources for a conversation in a specific graph."""
    try:
        conv_uuid = uuid.UUID(conversation_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid conversation ID")

    async with ctx.graph_session_factory() as session:
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


@router.get("/{conversation_id}/sources/{source_id}/download")
async def download_graph_ingest_source(
    conversation_id: str,
    source_id: str,
    ctx: GraphContext = Depends(get_graph_context),
) -> FileResponse:
    """Download the original file for an ingest source in a specific graph."""
    try:
        source_uuid = uuid.UUID(source_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid source ID")

    async with ctx.graph_session_factory() as session:
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


@router.post("/{conversation_id}/decompose", response_model=IngestDecomposeResponse)
async def decompose_graph_ingest(
    conversation_id: str,
    body: IngestDecomposeRequest,
    user: User = Depends(require_auth),
    ctx: GraphContext = Depends(get_graph_context),
) -> IngestDecomposeResponse:
    """Phase 1: Decompose selected chunks in a specific graph."""
    require_writer(ctx)
    try:
        conv_uuid = uuid.UUID(conversation_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid conversation ID")

    async with ctx.graph_session_factory() as session:
        conv_repo = ConversationRepository(session)
        conv = await conv_repo.get_by_id(conv_uuid)
        if conv is None:
            raise HTTPException(status_code=404, detail="Conversation not found")
        if conv.mode != "ingest":
            raise HTTPException(status_code=400, detail="Conversation is not an ingest")

        next_turn = await conv_repo.get_next_turn_number(conv_uuid)
        await conv_repo.add_message(
            conversation_id=conv_uuid, turn_number=next_turn, role="user", content="Decompose selected chunks"
        )
        assistant_msg = await conv_repo.add_message(
            conversation_id=conv_uuid, turn_number=next_turn + 1, role="assistant", content="", status="pending"
        )
        await session.commit()

        from kt_hatchet.client import dispatch_workflow

        require_api_key(user)
        run_id = await dispatch_workflow(
            "ingest_decompose",
            {
                "conversation_id": conversation_id,
                "message_id": str(assistant_msg.id),
                "selected_chunks": body.selected_chunks,
                "user_id": str(user.id),
                "graph_id": str(ctx.graph.id),
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


@router.get("/{conversation_id}/proposals", response_model=IngestProposalsResponse)
async def get_graph_ingest_proposals(
    conversation_id: str,
    ctx: GraphContext = Depends(get_graph_context),
) -> IngestProposalsResponse:
    """Fetch Phase 1 results (proposed nodes) in a specific graph."""
    try:
        conv_uuid = uuid.UUID(conversation_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid conversation ID")

    async with ctx.graph_session_factory() as session:
        conv_repo = ConversationRepository(session)
        conv = await conv_repo.get_by_id(conv_uuid)
        if conv is None:
            raise HTTPException(status_code=404, detail="Conversation not found")
        if conv.mode != "ingest":
            raise HTTPException(status_code=400, detail="Conversation is not an ingest")

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


@router.post("/{conversation_id}/build", response_model=IngestBuildResponse)
async def build_graph_ingest(
    conversation_id: str,
    body: IngestBuildRequest,
    user: User = Depends(require_auth),
    ctx: GraphContext = Depends(get_graph_context),
) -> IngestBuildResponse:
    """Phase 2: Build user-confirmed nodes in a specific graph."""
    require_writer(ctx)
    try:
        conv_uuid = uuid.UUID(conversation_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid conversation ID")

    async with ctx.graph_session_factory() as session:
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

        from kt_hatchet.client import dispatch_workflow
        from kt_hatchet.models import ConfirmedNode, IngestBuildInput, ProposedPerspective

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

        require_api_key(user)
        input_data = IngestBuildInput(
            selected_nodes=confirmed_nodes,
            conversation_id=str(conv_uuid),
            message_id=str(assistant_msg.id),
            user_id=str(user.id),
        ).model_dump()
        input_data["graph_id"] = str(ctx.graph.id)

        run_id = await dispatch_workflow(
            "ingest_build",
            input_data,
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
            workflow_run_id=run_id,
        )


@router.post("/bottom-up/prepare", response_model=ConversationResponse)
async def bottom_up_graph_prepare(
    body: BottomUpPrepareRequest,
    user: User = Depends(require_auth),
    ctx: GraphContext = Depends(get_graph_context),
) -> ConversationResponse:
    """Phase 1: Bottom-up discovery in a specific graph."""
    require_writer(ctx)
    async with ctx.graph_session_factory() as session:
        conv_repo = ConversationRepository(session)
        title = body.title or body.query[:200]
        conv = await conv_repo.create(title=title, mode="bottom_up_ingest")

        user_msg = await conv_repo.add_message(conversation_id=conv.id, turn_number=0, role="user", content=body.query)
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

        require_api_key(user)
        run_id = await dispatch_workflow(
            "bottom_up_prepare",
            {
                "query": body.query,
                "explore_budget": body.explore_budget,
                "conversation_id": str(conv.id),
                "message_id": str(assistant_msg.id),
                "user_id": str(user.id),
                "graph_id": str(ctx.graph.id),
            },
            additional_metadata={
                "conversation_id": str(conv.id),
                "message_id": str(assistant_msg.id),
            },
        )
        await conv_repo.update_message(assistant_msg.id, workflow_run_id=run_id)
        await session.commit()

        return _conversation_to_response(conv, [user_msg, assistant_msg])


@router.get("/{conversation_id}/bottom-up/proposals", response_model=BottomUpPrepareResponse)
async def get_graph_bottom_up_proposals(
    conversation_id: str,
    ctx: GraphContext = Depends(get_graph_context),
) -> BottomUpPrepareResponse:
    """Fetch bottom-up proposals in a specific graph."""
    try:
        conv_uuid = uuid.UUID(conversation_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid conversation ID")

    async with ctx.graph_session_factory() as session:
        conv_repo = ConversationRepository(session)
        conv = await conv_repo.get_by_id(conv_uuid)
        if conv is None:
            raise HTTPException(status_code=404, detail="Conversation not found")
        if conv.mode != "bottom_up_ingest":
            raise HTTPException(status_code=400, detail="Conversation is not a bottom-up ingest")

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


@router.post("/{conversation_id}/agent-select", response_model=AgentSelectResponse)
async def graph_agent_select(
    conversation_id: str,
    body: AgentSelectRequest,
    user: User = Depends(require_auth),
    ctx: GraphContext = Depends(get_graph_context),
) -> AgentSelectResponse:
    """Dispatch agent-assisted node selection in a specific graph."""
    require_writer(ctx)
    try:
        conv_uuid = uuid.UUID(conversation_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid conversation ID")

    async with ctx.graph_session_factory() as session:
        conv_repo = ConversationRepository(session)
        conv = await conv_repo.get_by_id(conv_uuid)
        if conv is None:
            raise HTTPException(status_code=404, detail="Conversation not found")

        messages = await conv_repo.get_messages(conv_uuid)
        metadata = None
        msg_id = None
        for msg in reversed(messages):
            if msg.role == "assistant" and msg.metadata_json:
                metadata = dict(msg.metadata_json)
                msg_id = str(msg.id)
                break

        if metadata is None or not metadata.get("proposed_nodes"):
            raise HTTPException(status_code=404, detail="No proposed nodes found — Phase 1 may still be running")

        from kt_hatchet.models import ProposedNode, ProposedPerspective

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

        instructions = body.instructions
        if not instructions:
            instructions = conv.title or ""

        metadata["agent_select_status"] = "running"
        await conv_repo.update_message(uuid.UUID(msg_id), metadata_json=metadata)
        await session.commit()

        from kt_hatchet.client import dispatch_workflow

        require_api_key(user)
        await dispatch_workflow(
            "agent_select",
            {
                "proposed_nodes": proposed_nodes,
                "max_select": body.max_select,
                "instructions": instructions,
                "conversation_id": str(conv_uuid),
                "message_id": msg_id or "",
                "user_id": str(user.id),
                "graph_id": str(ctx.graph.id),
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


@router.get("/{conversation_id}/agent-select/status", response_model=AgentSelectStatusResponse)
async def graph_agent_select_status(
    conversation_id: str,
    ctx: GraphContext = Depends(get_graph_context),
) -> AgentSelectStatusResponse:
    """Check agent-assisted node selection status in a specific graph."""
    try:
        conv_uuid = uuid.UUID(conversation_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid conversation ID")

    async with ctx.graph_session_factory() as session:
        conv_repo = ConversationRepository(session)
        messages = await conv_repo.get_messages(conv_uuid)
        for msg in reversed(messages):
            if msg.role == "assistant" and msg.metadata_json:
                status = msg.metadata_json.get("agent_select_status", "not_started")
                return AgentSelectStatusResponse(status=status)

    return AgentSelectStatusResponse(status="not_started")


@router.get("/{conversation_id}/summary", response_model=ResearchSummaryResponse)
async def get_graph_research_summary(
    conversation_id: str,
    ctx: GraphContext = Depends(get_graph_context),
) -> ResearchSummaryResponse:
    """Fetch research summary in a specific graph."""
    try:
        conv_uuid = uuid.UUID(conversation_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid conversation ID")

    async with ctx.graph_session_factory() as session:
        conv_repo = ConversationRepository(session)
        conv = await conv_repo.get_by_id(conv_uuid)
        if conv is None:
            raise HTTPException(status_code=404, detail="Conversation not found")

        report_repo = ResearchReportRepository(session)
        report = await report_repo.get_latest_by_conversation_id(conv_uuid)

        if report is not None and report.summary_data:
            return _build_summary_from_metadata(
                report.summary_data, conversation_id, str(report.message_id) if report.message_id else ""
            )

        messages = await conv_repo.get_messages(conv_uuid)
        for msg in reversed(messages):
            if msg.role == "assistant" and msg.metadata_json:
                return _build_summary_from_metadata(msg.metadata_json, conversation_id, str(msg.id))

        if report is not None:
            content_summary = "\n\n".join(report.scope_summaries or [])
            return ResearchSummaryResponse(
                conversation_id=conversation_id,
                message_id=str(report.message_id) if report.message_id else "",
                fact_count=0,
                source_count=0,
                source_urls=[],
                seeds=[],
                content_summary=content_summary,
                explore_used=report.explore_used,
            )

        for msg in reversed(messages):
            if msg.role == "assistant" and msg.status in ("pending", "running"):
                raise HTTPException(status_code=404, detail="Research is still running — please wait")

        raise HTTPException(status_code=404, detail="No summary available — research may have failed")
