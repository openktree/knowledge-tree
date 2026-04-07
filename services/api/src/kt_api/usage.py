"""Usage tracking API endpoints.

Reads from the flat ``llm_usage`` table (graph-db) which is populated
by the sync worker from ``write_llm_usage`` records that each Hatchet
task self-reports.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, Query

from kt_api.auth.permissions import require_system_permission
from kt_api.dependencies import get_session_factory_cached
from kt_api.schemas import (
    ConversationUsageResponse,
    ConversationUsageSummary,
    MessageUsageSummary,
    TokenUsageByModel,
    UsageSummaryResponse,
)
from kt_db.models import User
from kt_rbac import Permission

router = APIRouter(prefix="/api/v1/usage", tags=["usage"])


@router.get("/summary", response_model=UsageSummaryResponse)
async def get_usage_summary(
    since: datetime | None = Query(None, description="Start of time range (ISO 8601)"),
    until: datetime | None = Query(None, description="End of time range (ISO 8601)"),
    _admin: User = Depends(require_system_permission(Permission.SYSTEM_ADMIN_OPS)),
) -> UsageSummaryResponse:
    """Global token usage summary across all tasks."""
    from kt_db.repositories.llm_usage import LlmUsageRepository

    factory = get_session_factory_cached()
    async with factory() as session:
        repo = LlmUsageRepository(session)
        data = await repo.get_global_summary(since=since, until=until)

    return UsageSummaryResponse(
        total_prompt_tokens=int(data["total_prompt_tokens"]),
        total_completion_tokens=int(data["total_completion_tokens"]),
        total_cost_usd=float(data["total_cost_usd"]),
        report_count=int(data["report_count"]),
        by_model=[
            TokenUsageByModel(
                model_id=model_id,
                prompt_tokens=int(info["prompt_tokens"]),
                completion_tokens=int(info["completion_tokens"]),
                cost_usd=float(info["cost_usd"]),
            )
            for model_id, info in data.get("by_model", {}).items()  # type: ignore[union-attr]
        ],
        by_task=[
            TokenUsageByModel(
                model_id=task_name,
                prompt_tokens=int(info["prompt_tokens"]),
                completion_tokens=int(info["completion_tokens"]),
                cost_usd=float(info["cost_usd"]),
            )
            for task_name, info in data.get("by_task", {}).items()  # type: ignore[union-attr]
        ],
    )


@router.get("/by-conversation", response_model=list[ConversationUsageSummary])
async def get_usage_by_conversation(
    since: datetime | None = Query(None),
    until: datetime | None = Query(None),
    _admin: User = Depends(require_system_permission(Permission.SYSTEM_ADMIN_OPS)),
) -> list[ConversationUsageSummary]:
    """Token usage aggregated per conversation."""
    from kt_db.repositories.llm_usage import LlmUsageRepository

    factory = get_session_factory_cached()
    async with factory() as session:
        repo = LlmUsageRepository(session)
        rows = await repo.get_by_conversation_list(since=since, until=until)

    return [
        ConversationUsageSummary(
            conversation_id=r["conversation_id"],
            title=r["title"],
            total_prompt_tokens=r["total_prompt_tokens"],
            total_completion_tokens=r["total_completion_tokens"],
            total_cost_usd=r["total_cost_usd"],
            report_count=r["report_count"],
            last_at=r["last_at"],
            report_types=r.get("report_types", ["research"]),
        )
        for r in rows
    ]


@router.get("/conversations/{conversation_id}", response_model=ConversationUsageResponse)
async def get_conversation_usage(
    conversation_id: str,
    _admin: User = Depends(require_system_permission(Permission.SYSTEM_ADMIN_OPS)),
) -> ConversationUsageResponse:
    """Token usage for a specific conversation, broken down by message."""
    from kt_db.repositories.llm_usage import LlmUsageRepository

    factory = get_session_factory_cached()
    async with factory() as session:
        repo = LlmUsageRepository(session)
        conv_id = uuid.UUID(conversation_id)
        data = await repo.get_by_conversation(conv_id)

    return ConversationUsageResponse(
        conversation_id=conversation_id,
        total_prompt_tokens=data["total_prompt_tokens"],
        total_completion_tokens=data["total_completion_tokens"],
        total_cost_usd=data["total_cost_usd"],
        messages=[
            MessageUsageSummary(
                message_id=m["message_id"],
                total_prompt_tokens=m["total_prompt_tokens"],
                total_completion_tokens=m["total_completion_tokens"],
                total_cost_usd=m["total_cost_usd"],
                created_at=m["created_at"],
            )
            for m in data["messages"]
        ],
        by_model=[
            TokenUsageByModel(
                model_id=m["model_id"],
                prompt_tokens=int(m["prompt_tokens"]),
                completion_tokens=int(m["completion_tokens"]),
                cost_usd=float(m["cost_usd"]),
            )
            for m in data["by_model"]
        ],
    )


@router.get("/by-model", response_model=list[TokenUsageByModel])
async def get_usage_by_model(
    since: datetime | None = Query(None),
    until: datetime | None = Query(None),
    _admin: User = Depends(require_system_permission(Permission.SYSTEM_ADMIN_OPS)),
) -> list[TokenUsageByModel]:
    """Token usage aggregated by model."""
    from kt_db.repositories.llm_usage import LlmUsageRepository

    factory = get_session_factory_cached()
    async with factory() as session:
        repo = LlmUsageRepository(session)
        rows = await repo.get_by_model(since=since, until=until)

    return [
        TokenUsageByModel(
            model_id=r["model_id"],
            prompt_tokens=int(r["prompt_tokens"]),
            completion_tokens=int(r["completion_tokens"]),
            cost_usd=float(r["cost_usd"]),
        )
        for r in rows
    ]
