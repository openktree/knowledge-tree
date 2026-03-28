"""Progress and report endpoints for workflow tracking.

These endpoints provide real-time progress feedback for research and
synthesis workflows by querying Hatchet's native run/task status API.
"""

from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from kt_api.dependencies import get_db_session
from kt_api.schemas import (
    PipelineSnapshotResponse,
    PipelineTaskItem,
    ProgressResponse,
    ResearchReportResponse,
)
from kt_db.repositories.conversations import ConversationRepository
from kt_db.repositories.research_reports import ResearchReportRepository

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["progress"])


def _build_task_items(details: object) -> list[PipelineTaskItem]:
    """Convert Hatchet V1WorkflowRunDetails.tasks to PipelineTaskItem list."""
    from kt_hatchet.progress import map_task_summary

    tasks: list = getattr(details, "tasks", []) or []
    return [PipelineTaskItem(**map_task_summary(t)) for t in tasks]


@router.get(
    "/conversations/{conversation_id}/messages/{message_id}/progress",
    response_model=ProgressResponse,
)
async def get_message_progress(
    conversation_id: str,
    message_id: str,
    session: AsyncSession = Depends(get_db_session),
) -> ProgressResponse:
    """Get live progress for a workflow attached to a conversation message.

    Merges database message state with Hatchet task tree status.
    """
    conv_repo = ConversationRepository(session)
    msg = await conv_repo.get_message(uuid.UUID(message_id))
    if msg is None:
        raise HTTPException(status_code=404, detail="Message not found")
    if str(msg.conversation_id) != conversation_id:
        raise HTTPException(status_code=404, detail="Message not found in this conversation")

    # Base response from DB fields
    status = msg.status or "completed"
    error: str | None = msg.error
    task_items: list[PipelineTaskItem] = []

    # Query Hatchet for live task tree if we have a workflow run ID
    if msg.workflow_run_id:
        try:
            from kt_hatchet.client import get_workflow_run_details
            from kt_hatchet.progress import map_run_error, map_run_status

            details = await get_workflow_run_details(msg.workflow_run_id)
            status = map_run_status(details)
            error = error or map_run_error(details)
            task_items = _build_task_items(details)
        except RuntimeError:
            logger.debug(
                "Hatchet unavailable for run %s, falling back to DB status",
                msg.workflow_run_id,
            )

    return ProgressResponse(
        message_id=str(msg.id),
        status=status,
        content=msg.content or "",
        error=error,
        subgraph=None,
        nav_budget=msg.nav_budget,
        explore_budget=msg.explore_budget,
        nav_used=msg.nav_used,
        explore_used=msg.explore_used,
        visited_nodes=msg.visited_nodes,
        created_nodes=msg.created_nodes,
        created_edges=msg.created_edges,
        tasks=task_items,
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
    """Get the persisted research report for a completed workflow."""
    report_repo = ResearchReportRepository(session)
    report = await report_repo.get_by_message_id(uuid.UUID(message_id))
    if report is None:
        raise HTTPException(status_code=404, detail="Report not found")
    if str(report.conversation_id) != conversation_id:
        raise HTTPException(status_code=404, detail="Report not found in this conversation")

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
    "/workflows/{workflow_run_id}/progress",
    response_model=PipelineSnapshotResponse,
)
async def get_workflow_progress(
    workflow_run_id: str,
) -> PipelineSnapshotResponse:
    """Get live task progress for any workflow run by its Hatchet run ID.

    Useful for synthesis and other workflows not tied to a conversation message.
    """
    try:
        from kt_hatchet.client import get_workflow_run_details
        from kt_hatchet.progress import map_run_status

        details = await get_workflow_run_details(workflow_run_id)
        return PipelineSnapshotResponse(
            message_id="",
            workflow_run_id=workflow_run_id,
            status=map_run_status(details),
            tasks=_build_task_items(details),
        )
    except RuntimeError:
        raise HTTPException(
            status_code=502,
            detail="Unable to fetch workflow status from Hatchet",
        )
