"""Reports API — primary endpoint for research report retrieval.

Provides access to persisted research reports by ID, conversation, or
workflow run ID. Designed as the primary data source for completed
research results, replacing the fragile metadata_json approach.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from kt_api.dependencies import get_db_session
from kt_api.schemas import ReportResponse
from kt_db.models import ResearchReport
from kt_db.repositories.research_reports import ResearchReportRepository

router = APIRouter(prefix="/api/v1", tags=["reports"])


def _report_to_response(report: ResearchReport) -> ReportResponse:
    """Convert a ResearchReport ORM object to the API response."""
    return ReportResponse(
        id=str(report.id),
        message_id=str(report.message_id) if report.message_id else None,
        conversation_id=str(report.conversation_id) if report.conversation_id else None,
        workflow_run_id=report.workflow_run_id,
        report_type=report.report_type,
        nodes_created=report.nodes_created,
        edges_created=report.edges_created,
        waves_completed=report.waves_completed,
        explore_budget=report.explore_budget,
        explore_used=report.explore_used,
        nav_budget=report.nav_budget,
        nav_used=report.nav_used,
        scope_summaries=report.scope_summaries or [],
        super_sources=report.super_sources,
        summary_data=report.summary_data,
        total_prompt_tokens=report.total_prompt_tokens,
        total_completion_tokens=report.total_completion_tokens,
        total_cost_usd=report.total_cost_usd,
        created_at=report.created_at,
    )


@router.get("/reports/{report_id}", response_model=ReportResponse)
async def get_report(
    report_id: str,
    session: AsyncSession = Depends(get_db_session),
) -> ReportResponse:
    """Get a research report by its ID."""
    try:
        report_uuid = uuid.UUID(report_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid report ID")

    repo = ResearchReportRepository(session)
    report = await repo.get_by_id(report_uuid)

    if report is None:
        raise HTTPException(status_code=404, detail="Report not found")

    return _report_to_response(report)


@router.get("/reports/lookup", response_model=ReportResponse)
async def get_report_by_query(
    conversation_id: str | None = Query(None, description="Get latest report for a conversation"),
    workflow_run_id: str | None = Query(None, description="Get report by Hatchet workflow run ID"),
    session: AsyncSession = Depends(get_db_session),
) -> ReportResponse:
    """Get a research report by conversation ID or workflow run ID."""
    if not conversation_id and not workflow_run_id:
        raise HTTPException(status_code=400, detail="Provide conversation_id or workflow_run_id")

    repo = ResearchReportRepository(session)

    if workflow_run_id:
        report = await repo.get_by_workflow_run_id(workflow_run_id)
    else:
        try:
            conv_uuid = uuid.UUID(conversation_id)  # type: ignore[arg-type]
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid conversation ID")
        report = await repo.get_latest_by_conversation_id(conv_uuid)

    if report is None:
        raise HTTPException(status_code=404, detail="Report not found")

    return _report_to_response(report)
