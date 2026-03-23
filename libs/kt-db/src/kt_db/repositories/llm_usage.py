"""Repository for LlmUsage analytics queries (graph-db, read-only)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from kt_db.models import Conversation, LlmUsage


class LlmUsageRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    @staticmethod
    def _date_filters(since: datetime | None, until: datetime | None) -> list[Any]:
        filters: list[Any] = []
        if since is not None:
            filters.append(LlmUsage.created_at >= since.replace(tzinfo=None))
        if until is not None:
            filters.append(LlmUsage.created_at <= until.replace(tzinfo=None))
        return filters

    async def get_global_summary(
        self,
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> dict[str, object]:
        """SUM totals + GROUP BY model_id + GROUP BY task_type."""
        date_filters = self._date_filters(since, until)

        # Totals
        totals_q = select(
            func.coalesce(func.sum(LlmUsage.prompt_tokens), 0).label("prompt"),
            func.coalesce(func.sum(LlmUsage.completion_tokens), 0).label("completion"),
            func.coalesce(func.sum(LlmUsage.cost_usd), 0.0).label("cost"),
            func.count(func.distinct(LlmUsage.message_id)).label("report_count"),
        ).where(LlmUsage.conversation_id != uuid.UUID(int=0))
        for f in date_filters:
            totals_q = totals_q.where(f)
        row = (await self._session.execute(totals_q)).one()

        # By model
        model_q = (
            select(
                LlmUsage.model_id,
                func.sum(LlmUsage.prompt_tokens).label("prompt"),
                func.sum(LlmUsage.completion_tokens).label("completion"),
                func.sum(LlmUsage.cost_usd).label("cost"),
            )
            .where(LlmUsage.conversation_id != uuid.UUID(int=0))
            .group_by(LlmUsage.model_id)
        )
        for f in date_filters:
            model_q = model_q.where(f)
        by_model = {
            r.model_id: {
                "prompt_tokens": int(r.prompt),
                "completion_tokens": int(r.completion),
                "cost_usd": float(r.cost),
            }
            for r in (await self._session.execute(model_q)).all()
        }

        # By task
        task_q = (
            select(
                LlmUsage.task_type,
                func.sum(LlmUsage.prompt_tokens).label("prompt"),
                func.sum(LlmUsage.completion_tokens).label("completion"),
                func.sum(LlmUsage.cost_usd).label("cost"),
            )
            .where(LlmUsage.conversation_id != uuid.UUID(int=0))
            .group_by(LlmUsage.task_type)
        )
        for f in date_filters:
            task_q = task_q.where(f)
        by_task = {
            r.task_type: {
                "prompt_tokens": int(r.prompt),
                "completion_tokens": int(r.completion),
                "cost_usd": float(r.cost),
            }
            for r in (await self._session.execute(task_q)).all()
        }

        return {
            "total_prompt_tokens": int(row.prompt),
            "total_completion_tokens": int(row.completion),
            "total_cost_usd": float(row.cost),
            "report_count": int(row.report_count),
            "by_model": by_model,
            "by_task": by_task,
        }

    async def get_by_conversation_list(
        self,
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> list[dict[str, Any]]:
        """SUM GROUP BY conversation_id, joining conversations for title."""
        date_filters = self._date_filters(since, until)
        q = (
            select(
                LlmUsage.conversation_id,
                Conversation.title,
                func.sum(LlmUsage.prompt_tokens).label("prompt"),
                func.sum(LlmUsage.completion_tokens).label("completion"),
                func.sum(LlmUsage.cost_usd).label("cost"),
                func.count(func.distinct(LlmUsage.message_id)).label("report_count"),
                func.max(LlmUsage.created_at).label("last_at"),
            )
            .join(Conversation, LlmUsage.conversation_id == Conversation.id)
        )
        for f in date_filters:
            q = q.where(f)
        q = q.group_by(LlmUsage.conversation_id, Conversation.title)
        q = q.order_by(func.max(LlmUsage.created_at).desc())

        return [
            {
                "conversation_id": str(r.conversation_id),
                "title": r.title,
                "total_prompt_tokens": int(r.prompt),
                "total_completion_tokens": int(r.completion),
                "total_cost_usd": float(r.cost),
                "report_count": int(r.report_count),
                "last_at": r.last_at,
                "report_types": ["research"],
            }
            for r in (await self._session.execute(q)).all()
        ]

    async def get_by_conversation(
        self, conversation_id: uuid.UUID,
    ) -> dict[str, Any]:
        """SUM GROUP BY message_id for a single conversation."""
        msg_q = (
            select(
                LlmUsage.message_id,
                func.sum(LlmUsage.prompt_tokens).label("prompt"),
                func.sum(LlmUsage.completion_tokens).label("completion"),
                func.sum(LlmUsage.cost_usd).label("cost"),
                func.min(LlmUsage.created_at).label("created_at"),
            )
            .where(LlmUsage.conversation_id == conversation_id)
            .group_by(LlmUsage.message_id)
            .order_by(func.min(LlmUsage.created_at))
        )
        messages = [
            {
                "message_id": str(r.message_id),
                "total_prompt_tokens": int(r.prompt),
                "total_completion_tokens": int(r.completion),
                "total_cost_usd": float(r.cost),
                "created_at": r.created_at,
            }
            for r in (await self._session.execute(msg_q)).all()
        ]

        # Model breakdown for the whole conversation
        model_q = (
            select(
                LlmUsage.model_id,
                func.sum(LlmUsage.prompt_tokens).label("prompt"),
                func.sum(LlmUsage.completion_tokens).label("completion"),
                func.sum(LlmUsage.cost_usd).label("cost"),
            )
            .where(LlmUsage.conversation_id == conversation_id)
            .group_by(LlmUsage.model_id)
        )
        by_model = [
            {
                "model_id": r.model_id,
                "prompt_tokens": int(r.prompt),
                "completion_tokens": int(r.completion),
                "cost_usd": float(r.cost),
            }
            for r in (await self._session.execute(model_q)).all()
        ]

        total_prompt = sum(m["total_prompt_tokens"] for m in messages)
        total_completion = sum(m["total_completion_tokens"] for m in messages)
        total_cost = sum(m["total_cost_usd"] for m in messages)

        return {
            "conversation_id": str(conversation_id),
            "total_prompt_tokens": total_prompt,
            "total_completion_tokens": total_completion,
            "total_cost_usd": total_cost,
            "messages": messages,
            "by_model": by_model,
        }

    async def get_by_model(
        self,
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> list[dict[str, Any]]:
        """SUM GROUP BY model_id across all usage."""
        date_filters = self._date_filters(since, until)
        q = (
            select(
                LlmUsage.model_id,
                func.sum(LlmUsage.prompt_tokens).label("prompt"),
                func.sum(LlmUsage.completion_tokens).label("completion"),
                func.sum(LlmUsage.cost_usd).label("cost"),
            )
            .where(LlmUsage.conversation_id != uuid.UUID(int=0))
            .group_by(LlmUsage.model_id)
        )
        for f in date_filters:
            q = q.where(f)
        return [
            {
                "model_id": r.model_id,
                "prompt_tokens": int(r.prompt),
                "completion_tokens": int(r.completion),
                "cost_usd": float(r.cost),
            }
            for r in (await self._session.execute(q)).all()
        ]

    async def get_message_breakdown(
        self, message_id: uuid.UUID,
    ) -> dict[str, Any]:
        """Per task_type + per model_id for a single message."""
        by_task_q = (
            select(
                LlmUsage.task_type,
                func.sum(LlmUsage.prompt_tokens).label("prompt"),
                func.sum(LlmUsage.completion_tokens).label("completion"),
                func.sum(LlmUsage.cost_usd).label("cost"),
            )
            .where(LlmUsage.message_id == message_id)
            .group_by(LlmUsage.task_type)
        )
        by_model_q = (
            select(
                LlmUsage.model_id,
                func.sum(LlmUsage.prompt_tokens).label("prompt"),
                func.sum(LlmUsage.completion_tokens).label("completion"),
                func.sum(LlmUsage.cost_usd).label("cost"),
            )
            .where(LlmUsage.message_id == message_id)
            .group_by(LlmUsage.model_id)
        )
        by_task = {
            r.task_type: {
                "prompt_tokens": int(r.prompt),
                "completion_tokens": int(r.completion),
                "cost_usd": float(r.cost),
            }
            for r in (await self._session.execute(by_task_q)).all()
        }
        by_model = {
            r.model_id: {
                "prompt_tokens": int(r.prompt),
                "completion_tokens": int(r.completion),
                "cost_usd": float(r.cost),
            }
            for r in (await self._session.execute(by_model_q)).all()
        }
        return {"by_task": by_task, "by_model": by_model}
