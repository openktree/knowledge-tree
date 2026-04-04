"""Repository for ResearchReport persistence."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from kt_db.models import Conversation, LlmUsageRecord, ResearchReport


class ResearchReportRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self,
        message_id: uuid.UUID | None = None,
        conversation_id: uuid.UUID | None = None,
        nodes_created: int = 0,
        edges_created: int = 0,
        waves_completed: int = 0,
        explore_budget: int | None = None,
        explore_used: int = 0,
        nav_budget: int | None = None,
        nav_used: int = 0,
        scope_summaries: list[str] | None = None,
        total_prompt_tokens: int = 0,
        total_completion_tokens: int = 0,
        total_cost_usd: float = 0.0,
        usage_by_model: dict[str, dict[str, int | float]] | None = None,
        usage_by_task: dict[str, dict[str, Any]] | None = None,
        report_type: str = "research",
        super_sources: list[dict[str, Any]] | None = None,
        workflow_run_id: str | None = None,
        summary_data: dict[str, Any] | None = None,
    ) -> ResearchReport:
        report = ResearchReport(
            id=uuid.uuid4(),
            message_id=message_id,
            conversation_id=conversation_id,
            nodes_created=nodes_created,
            edges_created=edges_created,
            waves_completed=waves_completed,
            explore_budget=explore_budget,
            explore_used=explore_used,
            nav_budget=nav_budget,
            nav_used=nav_used,
            scope_summaries=scope_summaries,
            total_prompt_tokens=total_prompt_tokens,
            total_completion_tokens=total_completion_tokens,
            total_cost_usd=total_cost_usd,
            usage_by_task=usage_by_task,
            report_type=report_type,
            super_sources=super_sources,
            workflow_run_id=workflow_run_id,
            summary_data=summary_data,
        )
        self._session.add(report)
        await self._session.flush()

        # Create per-model usage records
        if usage_by_model:
            for model_id, data in usage_by_model.items():
                record = LlmUsageRecord(
                    id=uuid.uuid4(),
                    research_report_id=report.id,
                    model_id=model_id,
                    prompt_tokens=int(data.get("prompt_tokens", 0)),
                    completion_tokens=int(data.get("completion_tokens", 0)),
                    cost_usd=float(data.get("cost_usd", 0.0)),
                )
                self._session.add(record)
            await self._session.flush()

        return report

    async def get_by_message_id(self, message_id: uuid.UUID) -> ResearchReport | None:
        result = await self._session.execute(select(ResearchReport).where(ResearchReport.message_id == message_id))
        return result.scalar_one_or_none()

    async def get_by_id(self, report_id: uuid.UUID) -> ResearchReport | None:
        result = await self._session.execute(select(ResearchReport).where(ResearchReport.id == report_id))
        return result.scalar_one_or_none()

    async def get_by_workflow_run_id(self, workflow_run_id: str) -> ResearchReport | None:
        result = await self._session.execute(
            select(ResearchReport).where(ResearchReport.workflow_run_id == workflow_run_id)
        )
        return result.scalar_one_or_none()

    async def get_latest_by_conversation_id(self, conversation_id: uuid.UUID) -> ResearchReport | None:
        result = await self._session.execute(
            select(ResearchReport)
            .where(ResearchReport.conversation_id == conversation_id)
            .order_by(ResearchReport.created_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def get_usage_records_by_report(
        self,
        report_id: uuid.UUID,
    ) -> list[LlmUsageRecord]:
        result = await self._session.execute(
            select(LlmUsageRecord).where(LlmUsageRecord.research_report_id == report_id)
        )
        return list(result.scalars().all())

    async def get_usage_by_conversation(
        self,
        conversation_id: uuid.UUID,
    ) -> list[ResearchReport]:
        result = await self._session.execute(
            select(ResearchReport)
            .where(ResearchReport.conversation_id == conversation_id)
            .order_by(ResearchReport.created_at)
        )
        return list(result.scalars().all())

    @staticmethod
    def _date_filters(
        since: datetime | None,
        until: datetime | None,
    ) -> list[Any]:
        """Build SQLAlchemy filter clauses for date range on ResearchReport.created_at."""
        filters: list[Any] = []
        if since is not None:
            # Strip tzinfo — DB column is TIMESTAMP WITHOUT TIME ZONE
            filters.append(ResearchReport.created_at >= since.replace(tzinfo=None))
        if until is not None:
            filters.append(ResearchReport.created_at <= until.replace(tzinfo=None))
        return filters

    async def get_all_conversations_usage(
        self,
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> list[dict[str, Any]]:
        """Get usage totals per conversation with title and report types."""
        q = select(
            ResearchReport.conversation_id,
            Conversation.title,
            func.sum(ResearchReport.total_prompt_tokens).label("prompt"),
            func.sum(ResearchReport.total_completion_tokens).label("completion"),
            func.sum(ResearchReport.total_cost_usd).label("cost"),
            func.count(ResearchReport.id).label("report_count"),
            func.max(ResearchReport.created_at).label("last_at"),
            func.array_agg(func.distinct(ResearchReport.report_type)).label("report_types"),
        ).join(Conversation, ResearchReport.conversation_id == Conversation.id)
        for f in self._date_filters(since, until):
            q = q.where(f)
        q = q.group_by(ResearchReport.conversation_id, Conversation.title)
        q = q.order_by(func.max(ResearchReport.created_at).desc())

        result = await self._session.execute(q)
        return [
            {
                "conversation_id": str(r.conversation_id),
                "title": r.title,
                "total_prompt_tokens": int(r.prompt),
                "total_completion_tokens": int(r.completion),
                "total_cost_usd": float(r.cost),
                "report_count": int(r.report_count),
                "last_at": r.last_at,
                "report_types": r.report_types or ["research"],
            }
            for r in result.all()
        ]

    async def get_global_usage_summary(
        self,
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> dict[str, object]:
        """Aggregate token usage across all research reports."""
        date_filters = self._date_filters(since, until)

        totals_q = select(
            func.coalesce(func.sum(ResearchReport.total_prompt_tokens), 0).label("prompt"),
            func.coalesce(func.sum(ResearchReport.total_completion_tokens), 0).label("completion"),
            func.coalesce(func.sum(ResearchReport.total_cost_usd), 0.0).label("cost"),
            func.count(ResearchReport.id).label("report_count"),
        )
        for f in date_filters:
            totals_q = totals_q.where(f)
        totals = await self._session.execute(totals_q)
        row = totals.one()

        # Per-model: join through research_reports to apply date filter
        model_q = select(
            LlmUsageRecord.model_id,
            func.sum(LlmUsageRecord.prompt_tokens).label("prompt"),
            func.sum(LlmUsageRecord.completion_tokens).label("completion"),
            func.sum(LlmUsageRecord.cost_usd).label("cost"),
        ).join(ResearchReport, LlmUsageRecord.research_report_id == ResearchReport.id)
        for f in date_filters:
            model_q = model_q.where(f)
        model_q = model_q.group_by(LlmUsageRecord.model_id)
        by_model_rows = await self._session.execute(model_q)
        by_model = {
            r.model_id: {
                "prompt_tokens": int(r.prompt),
                "completion_tokens": int(r.completion),
                "cost_usd": float(r.cost),
            }
            for r in by_model_rows.all()
        }

        # Aggregate usage_by_task across filtered reports
        task_q = select(ResearchReport.usage_by_task).where(ResearchReport.usage_by_task.isnot(None))
        for f in date_filters:
            task_q = task_q.where(f)
        task_rows = await self._session.execute(task_q)
        by_task: dict[str, dict[str, int | float]] = {}
        for (task_json,) in task_rows.all():
            if not isinstance(task_json, dict):
                continue
            for task_name, data in task_json.items():
                if task_name not in by_task:
                    by_task[task_name] = {"prompt_tokens": 0, "completion_tokens": 0, "cost_usd": 0.0}
                by_task[task_name]["prompt_tokens"] += int(data.get("prompt_tokens", 0))
                by_task[task_name]["completion_tokens"] += int(data.get("completion_tokens", 0))
                by_task[task_name]["cost_usd"] += float(data.get("cost_usd", 0.0))

        return {
            "total_prompt_tokens": int(row.prompt),
            "total_completion_tokens": int(row.completion),
            "total_cost_usd": float(row.cost),
            "report_count": int(row.report_count),
            "by_model": by_model,
            "by_task": by_task,
        }
