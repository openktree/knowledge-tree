"""Conversation endpoints — create, list, follow-up, and progress polling."""

from __future__ import annotations

import logging
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from kt_api.auth.tokens import require_auth
from kt_api.dependencies import get_db_session, require_api_key
from kt_api.schemas import (
    ConversationListItem,
    ConversationMessageResponse,
    ConversationResponse,
    CreateConversationRequest,
    DeleteResponse,
    PaginatedConversationsResponse,
    PipelineSnapshotResponse,
    PipelineTaskItem,
    ProgressResponse,
    ResearchReportResponse,
    ResynthesizeResponse,
    SendMessageRequest,
    SubgraphResponse,
    TaskLogLineResponse,
    UpdateConversationRequest,
)
from kt_db.models import User
from kt_db.repositories.conversations import ConversationRepository

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["conversations"])


# Task display names that map to pipeline scopes (top-level scope tasks only)
_SCOPE_TASKS = frozenset(
    {
        "synthesize",
        "resynthesize",
        "handle_follow_up",
        "handle_ingest",
        "handle_query",
        "bottom_up_orchestrate",
        "bottom_up_scope",
        "bottom_up_prepare_scope",
        "bottom_up_prepare",
        "node_pipeline",
        "build_composite",
    }
)


# ── Helpers ──────────────────────────────────────────────────────────


def _message_to_response(msg: Any) -> ConversationMessageResponse:
    """Convert a ConversationMessage ORM model to response schema."""
    subgraph = None
    if msg.subgraph:
        subgraph = SubgraphResponse(**msg.subgraph)
    return ConversationMessageResponse(
        id=str(msg.id),
        turn_number=msg.turn_number,
        role=msg.role,
        content=msg.content,
        nav_budget=msg.nav_budget,
        explore_budget=msg.explore_budget,
        nav_used=msg.nav_used,
        explore_used=msg.explore_used,
        visited_nodes=msg.visited_nodes,
        created_nodes=msg.created_nodes,
        created_edges=msg.created_edges,
        subgraph=subgraph,
        status=msg.status,
        error=msg.error,
        workflow_run_id=getattr(msg, "workflow_run_id", None),
        created_at=msg.created_at,
    )


def _conversation_to_response(conv: Any, messages: list[Any] | None = None) -> ConversationResponse:
    """Convert a Conversation ORM model to response schema."""
    msgs = messages if messages is not None else getattr(conv, "messages", [])
    return ConversationResponse(
        id=str(conv.id),
        title=conv.title,
        mode=getattr(conv, "mode", "research"),
        messages=[_message_to_response(m) for m in msgs],
        created_at=conv.created_at,
        updated_at=conv.updated_at,
    )


# ── Endpoints ────────────────────────────────────────────────────────


@router.post("/conversations", response_model=ConversationResponse)
async def create_conversation(
    request: CreateConversationRequest,
    user: User = Depends(require_auth),
    session: AsyncSession = Depends(get_db_session),
) -> ConversationResponse:
    """Create a new conversation and start initial exploration."""
    repo = ConversationRepository(session)

    # Create conversation
    title = request.title or request.message[:200]
    mode = request.mode
    conv = await repo.create(title=title, mode=mode)

    # All conversations now use query_wf
    explore_budget = 0

    # User message (turn 0)
    user_msg = await repo.add_message(
        conversation_id=conv.id,
        turn_number=0,
        role="user",
        content=request.message,
    )

    # Pending assistant message (turn 1)
    assistant_msg = await repo.add_message(
        conversation_id=conv.id,
        turn_number=1,
        role="assistant",
        content="",
        nav_budget=request.nav_budget,
        explore_budget=explore_budget,
        status="pending",
    )

    await session.commit()

    from hatchet_sdk import TriggerWorkflowOptions

    wf_options = TriggerWorkflowOptions(
        additional_metadata={
            "conversation_id": str(conv.id),
            "message_id": str(assistant_msg.id),
        }
    )

    from kt_hatchet.models import QueryInput
    from kt_worker_query.workflows.query import query_wf

    api_key = require_api_key(user)
    ref = await query_wf.aio_run_no_wait(
        QueryInput(
            query=request.message,
            nav_budget=request.nav_budget,
            conversation_id=str(conv.id),
            message_id=str(assistant_msg.id),
            api_key=api_key,
        ),
        options=wf_options,
    )
    await repo.update_message(assistant_msg.id, workflow_run_id=ref.workflow_run_id)
    await session.commit()

    return _conversation_to_response(conv, [user_msg, assistant_msg])


@router.get("/conversations", response_model=PaginatedConversationsResponse)
async def list_conversations(
    offset: int = 0,
    limit: int = 20,
    mode: str | None = None,
    session: AsyncSession = Depends(get_db_session),
) -> PaginatedConversationsResponse:
    """List conversations with pagination. Optionally filter by mode."""
    repo = ConversationRepository(session)
    conversations = await repo.list_recent(limit=limit, offset=offset, mode=mode)
    total = await repo.count(mode=mode)

    items: list[ConversationListItem] = []
    for conv in conversations:
        msg_count = await repo.get_message_count(conv.id)
        items.append(
            ConversationListItem(
                id=str(conv.id),
                title=conv.title,
                mode=getattr(conv, "mode", "research"),
                message_count=msg_count,
                created_at=conv.created_at,
                updated_at=conv.updated_at,
            )
        )

    return PaginatedConversationsResponse(
        items=items,
        total=total,
        offset=offset,
        limit=limit,
    )


@router.get("/conversations/{conversation_id}", response_model=ConversationResponse)
async def get_conversation(
    conversation_id: str,
    session: AsyncSession = Depends(get_db_session),
) -> ConversationResponse:
    """Get a conversation with all messages."""
    try:
        conv_uuid = uuid.UUID(conversation_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid conversation ID")

    repo = ConversationRepository(session)
    conv = await repo.get_with_messages(conv_uuid)
    if conv is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return _conversation_to_response(conv)


@router.patch("/conversations/{conversation_id}", response_model=ConversationResponse)
async def update_conversation(
    conversation_id: str,
    request: UpdateConversationRequest,
    session: AsyncSession = Depends(get_db_session),
) -> ConversationResponse:
    """Update a conversation (e.g. edit the query title)."""
    try:
        conv_uuid = uuid.UUID(conversation_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid conversation ID")

    repo = ConversationRepository(session)
    conv = await repo.get_by_id(conv_uuid)
    if conv is None:
        raise HTTPException(status_code=404, detail="Conversation not found")

    await repo.update_title(conv_uuid, request.title)
    await session.commit()

    conv = await repo.get_with_messages(conv_uuid)
    if conv is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return _conversation_to_response(conv)


@router.delete("/conversations/{conversation_id}", response_model=DeleteResponse)
async def delete_conversation(
    conversation_id: str,
    session: AsyncSession = Depends(get_db_session),
) -> DeleteResponse:
    """Delete a conversation and all associated messages/pipeline data.

    Knowledge graph data (nodes, facts, edges) is preserved.
    """
    try:
        conv_uuid = uuid.UUID(conversation_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid conversation ID")

    repo = ConversationRepository(session)
    conv = await repo.get_with_messages(conv_uuid)
    if conv is None:
        raise HTTPException(status_code=404, detail="Conversation not found")

    # Reject deletion if any message is currently running
    for msg in conv.messages:
        if msg.status == "running":
            raise HTTPException(
                status_code=409,
                detail="Cannot delete a conversation with a running query",
            )

    await repo.delete(conv_uuid)
    await session.commit()
    return DeleteResponse(deleted=True, id=conversation_id)


@router.post(
    "/conversations/{conversation_id}/messages",
    response_model=ConversationMessageResponse,
)
async def send_message(
    conversation_id: str,
    request: SendMessageRequest,
    user: User = Depends(require_auth),
    session: AsyncSession = Depends(get_db_session),
) -> ConversationMessageResponse:
    """Send a follow-up message in a conversation."""
    try:
        conv_uuid = uuid.UUID(conversation_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid conversation ID")

    repo = ConversationRepository(session)
    conv = await repo.get_by_id(conv_uuid)
    if conv is None:
        raise HTTPException(status_code=404, detail="Conversation not found")

    mode = getattr(conv, "mode", "research")
    next_turn = await repo.get_next_turn_number(conv_uuid)

    # Force explore_budget=0 for query mode
    explore_budget = 0 if mode == "query" else request.explore_budget

    # User message
    await repo.add_message(
        conversation_id=conv_uuid,
        turn_number=next_turn,
        role="user",
        content=request.message,
    )

    # Pending assistant message
    assistant_msg = await repo.add_message(
        conversation_id=conv_uuid,
        turn_number=next_turn + 1,
        role="assistant",
        content="",
        nav_budget=request.nav_budget,
        explore_budget=explore_budget,
        status="pending",
    )

    await session.commit()

    # Use conversation title as original query (editable by user via PATCH)
    original_query = conv.title or ""

    from hatchet_sdk import TriggerWorkflowOptions

    from kt_hatchet.models import FollowUpInput
    from kt_worker_conv.workflows.conversations import follow_up_wf

    api_key = require_api_key(user)
    ref = await follow_up_wf.aio_run_no_wait(
        FollowUpInput(
            follow_up_query=request.message,
            original_query=original_query,
            nav_budget=request.nav_budget,
            explore_budget=explore_budget,
            mode=mode,
            conversation_id=str(conv_uuid),
            message_id=str(assistant_msg.id),
            api_key=api_key,
        ),
        options=TriggerWorkflowOptions(
            additional_metadata={
                "conversation_id": str(conv_uuid),
                "message_id": str(assistant_msg.id),
            }
        ),
    )
    await repo.update_message(assistant_msg.id, workflow_run_id=ref.workflow_run_id)
    await session.commit()

    return _message_to_response(assistant_msg)


@router.post(
    "/conversations/{conversation_id}/messages/{message_id}/resynthesize",
    response_model=ResynthesizeResponse,
)
async def resynthesize_message(
    conversation_id: str,
    message_id: str,
    user: User = Depends(require_auth),
    session: AsyncSession = Depends(get_db_session),
) -> ResynthesizeResponse:
    """Re-run synthesis on an existing assistant message without re-exploring."""
    try:
        conv_uuid = uuid.UUID(conversation_id)
        msg_uuid = uuid.UUID(message_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid ID format")

    repo = ConversationRepository(session)
    conv = await repo.get_by_id(conv_uuid)
    if conv is None:
        raise HTTPException(status_code=404, detail="Conversation not found")

    msg = await repo.get_message(msg_uuid)
    if msg is None or msg.conversation_id != conv_uuid:
        raise HTTPException(status_code=404, detail="Message not found")

    if msg.role != "assistant":
        raise HTTPException(status_code=400, detail="Only assistant messages can be re-synthesized")

    if msg.status not in ("completed", "failed"):
        raise HTTPException(status_code=400, detail="Message must be completed or failed")

    if not msg.visited_nodes:
        raise HTTPException(status_code=400, detail="Message has no visited nodes to re-synthesize from")

    # Find the user query for this turn: look for the preceding user message
    messages = await repo.get_messages(conv_uuid)
    query = conv.title or ""
    for m in messages:
        if m.role == "user" and m.turn_number < msg.turn_number:
            query = m.content

    original_content = msg.content
    original_status = msg.status

    # Reset message status
    await repo.update_message(msg_uuid, status="running", content="", error=None)
    await session.commit()

    try:
        from hatchet_sdk import TriggerWorkflowOptions

        from kt_hatchet.models import ResynthesizeInput
        from kt_worker_conv.workflows.conversations import resynthesize_task

        api_key = require_api_key(user)
        ref = await resynthesize_task.aio_run_no_wait(  # type: ignore[union-attr]
            ResynthesizeInput(
                query=query,
                conversation_id=str(conv_uuid),
                message_id=str(msg_uuid),
                api_key=api_key,
            ),
            options=TriggerWorkflowOptions(
                additional_metadata={
                    "conversation_id": str(conv_uuid),
                    "message_id": str(msg_uuid),
                }
            ),
        )
        await repo.update_message(msg_uuid, workflow_run_id=ref.workflow_run_id)
        await session.commit()
    except Exception:
        # Dispatch failed — restore the message so it isn't stuck in "running"
        await repo.update_message(msg_uuid, status=original_status or "failed", content=original_content or "")
        await session.commit()
        raise

    return ResynthesizeResponse(message_id=message_id, status="running")


@router.post(
    "/conversations/{conversation_id}/messages/{message_id}/stop",
    response_model=ResynthesizeResponse,
)
async def stop_message(
    conversation_id: str,
    message_id: str,
    session: AsyncSession = Depends(get_db_session),
) -> ResynthesizeResponse:
    """Force-stop a running or pending assistant message."""
    try:
        conv_uuid = uuid.UUID(conversation_id)
        msg_uuid = uuid.UUID(message_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid ID format")

    repo = ConversationRepository(session)
    conv = await repo.get_by_id(conv_uuid)
    if conv is None:
        raise HTTPException(status_code=404, detail="Conversation not found")

    msg = await repo.get_message(msg_uuid)
    if msg is None or msg.conversation_id != conv_uuid:
        raise HTTPException(status_code=404, detail="Message not found")

    if msg.status not in ("pending", "running"):
        raise HTTPException(status_code=400, detail="Message is not active")

    await repo.update_message(msg_uuid, status="failed", error="Stopped by user")
    await session.commit()
    logger.info("Message %s stopped by user", message_id)
    # TODO: Hatchet workflow cancellation via workflow_run_id
    return ResynthesizeResponse(message_id=message_id, status="failed")


@router.get(
    "/conversations/{conversation_id}/messages/{message_id}/pipeline",
    response_model=PipelineSnapshotResponse,
)
async def get_pipeline_snapshot(
    conversation_id: str,
    message_id: str,
    session: AsyncSession = Depends(get_db_session),
) -> PipelineSnapshotResponse:
    """Return a historical pipeline snapshot for a completed message.

    Fetches the Hatchet workflow run details via workflow_run_id stored on the
    message, maps the V1TaskSummary tree to PipelineTaskItem[], and returns it.
    The frontend uses this to render the pipeline panel for past conversations.
    """
    try:
        msg_uuid = uuid.UUID(message_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid message ID")

    repo = ConversationRepository(session)
    msg = await repo.get_message(msg_uuid)
    if msg is None:
        raise HTTPException(status_code=404, detail="Message not found")

    workflow_run_id = getattr(msg, "workflow_run_id", None)
    if not workflow_run_id:
        return PipelineSnapshotResponse(
            message_id=message_id,
            workflow_run_id=None,
            status=msg.status or "unknown",
            tasks=[],
        )

    try:
        from kt_hatchet.client import get_hatchet

        h = get_hatchet()
        # aio_list returns ALL runs tagged with message_id — including child
        # workflow runs (sub_explore, synthesize) that were spawned with the
        # same additional_metadata.
        run_list = await h.runs.aio_list(
            additional_metadata={"message_id": message_id},
            limit=200,
        )
        rows = getattr(run_list, "rows", run_list) if not isinstance(run_list, list) else run_list
        tasks = [_task_summary_to_item(t) for t in rows if _bare_name(t.display_name or "") in _SCOPE_TASKS]
        # Sort by start time so the pipeline view is in execution order
        tasks.sort(key=lambda t: t.started_at or "")
    except Exception:
        logger.exception("Failed to fetch pipeline snapshot for message %s", message_id)
        tasks = []

    return PipelineSnapshotResponse(
        message_id=message_id,
        workflow_run_id=workflow_run_id,
        status=msg.status or "unknown",
        tasks=tasks,
    )


@router.get(
    "/conversations/{conversation_id}/messages/{message_id}/report",
    response_model=ResearchReportResponse,
)
async def get_message_report(
    conversation_id: str,
    message_id: str,
    session: AsyncSession = Depends(get_db_session),
) -> ResearchReportResponse:
    """Return the persisted research report for a message."""
    from kt_db.repositories.research_reports import ResearchReportRepository

    try:
        msg_uuid = uuid.UUID(message_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid message ID")

    repo = ResearchReportRepository(session)
    report = await repo.get_by_message_id(msg_uuid)
    if report is None:
        raise HTTPException(status_code=404, detail="Report not found")

    return ResearchReportResponse(
        message_id=str(report.message_id),
        nodes_created=report.nodes_created,
        edges_created=report.edges_created,
        waves_completed=report.waves_completed,
        explore_budget=report.explore_budget,
        explore_used=report.explore_used,
        nav_budget=report.nav_budget,
        nav_used=report.nav_used,
        scope_summaries=report.scope_summaries or [],
        super_sources=report.super_sources,
        total_prompt_tokens=report.total_prompt_tokens,
        total_completion_tokens=report.total_completion_tokens,
        total_cost_usd=report.total_cost_usd,
        created_at=report.created_at,
    )


@router.get(
    "/conversations/{conversation_id}/messages/{message_id}/progress",
    response_model=ProgressResponse,
)
async def get_message_progress(
    conversation_id: str,
    message_id: str,
    session: AsyncSession = Depends(get_db_session),
) -> ProgressResponse:
    """Combined progress endpoint for polling-based updates.

    Merges message state (status, content, subgraph, budgets) with Hatchet
    pipeline task state into a single response.  Frontend polls this every
    30s during active turns instead of using SSE.
    """
    try:
        msg_uuid = uuid.UUID(message_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid message ID")

    repo = ConversationRepository(session)
    msg = await repo.get_message(msg_uuid)
    if msg is None:
        raise HTTPException(status_code=404, detail="Message not found")

    subgraph = None
    if msg.subgraph:
        subgraph = SubgraphResponse(**msg.subgraph)

    # Fetch pipeline tasks from Hatchet
    tasks: list[PipelineTaskItem] = []
    workflow_run_id = getattr(msg, "workflow_run_id", None)
    if workflow_run_id:
        try:
            from kt_hatchet.client import get_hatchet

            h = get_hatchet()
            run_list = await h.runs.aio_list(
                additional_metadata={"message_id": message_id},
                limit=200,
            )
            rows = getattr(run_list, "rows", run_list) if not isinstance(run_list, list) else run_list
            tasks = [_task_summary_to_item(t) for t in rows if _bare_name(t.display_name or "") in _SCOPE_TASKS]
            tasks.sort(key=lambda t: t.started_at or "")
        except Exception:
            logger.warning("Failed to fetch pipeline tasks for message %s", message_id, exc_info=True)

    return ProgressResponse(
        message_id=message_id,
        status=msg.status or "unknown",
        content=msg.content or "",
        error=msg.error,
        subgraph=subgraph,
        nav_budget=msg.nav_budget,
        explore_budget=msg.explore_budget,
        nav_used=msg.nav_used,
        explore_used=msg.explore_used,
        visited_nodes=msg.visited_nodes,
        created_nodes=msg.created_nodes,
        created_edges=msg.created_edges,
        tasks=tasks,
    )


@router.get(
    "/tasks/{task_run_id}/logs",
    response_model=list[TaskLogLineResponse],
)
async def get_task_logs(
    task_run_id: str,
) -> list[TaskLogLineResponse]:
    """Return Hatchet task logs for a given task run ID.

    Used by the pipeline panel Logs tab to display task-level stdout/stderr
    captured by Hatchet during workflow execution.
    """
    from kt_hatchet.client import get_hatchet

    h = get_hatchet()
    try:
        log_list = await h.logs.aio_list(task_run_id=task_run_id, limit=1000)
        rows = log_list.rows or []
        return [
            TaskLogLineResponse(
                message=row.message,
                created_at=row.created_at,
                level=str(row.level.value) if row.level is not None else None,
            )
            for row in rows
        ]
    except Exception:
        logger.warning("Failed to fetch task logs for %s", task_run_id, exc_info=True)
        return []


def _bare_name(display_name: str) -> str:
    """Strip the numeric suffix Hatchet appends to display names (e.g. 'orchestrate-123' → 'orchestrate')."""
    return display_name.rsplit("-", 1)[0] if "-" in display_name else display_name


def _task_summary_to_item(task: Any) -> PipelineTaskItem:
    """Map a V1TaskSummary to a PipelineTaskItem."""
    children = [_task_summary_to_item(c) for c in (task.children or [])]
    status = str(task.status.value) if hasattr(task.status, "value") else str(task.status)
    started = str(task.started_at) if getattr(task, "started_at", None) else ""
    bare = _bare_name(task.display_name or "")

    # Extract wave_number from task input for explore_scope tasks
    wave_number: int | None = None
    if bare == "explore_scope":
        inp: dict[str, Any] = getattr(task, "input", {}) or {}
        raw_wave = inp.get("wave_number")
        if raw_wave is not None:
            try:
                wave_number = int(raw_wave)
            except (TypeError, ValueError):
                pass

    # For node_pipeline / build_composite, use concept from input as display_name
    effective_name = bare
    node_type: str | None = None
    if bare in ("node_pipeline", "build_composite"):
        inp = getattr(task, "input", {}) or {}
        concept = inp.get("concept")
        if concept:
            effective_name = concept
        node_type = inp.get("node_type") or None

    return PipelineTaskItem(
        task_id=str(task.metadata.id) if hasattr(task, "metadata") and task.metadata else "",
        display_name=effective_name,
        status=status,
        duration_ms=task.duration,
        started_at=started,
        wave_number=wave_number,
        node_type=node_type,
        children=children,
    )
