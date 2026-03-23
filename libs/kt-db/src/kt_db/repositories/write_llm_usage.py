"""Repository for WriteLlmUsage records in write-db."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from kt_db.write_models import WriteLlmUsage


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class WriteLlmUsageRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def bulk_insert(self, records: list[WriteLlmUsage]) -> None:
        """Insert multiple usage records in a single statement."""
        if not records:
            return
        now = _utcnow()
        values = [
            {
                "id": r.id,
                "conversation_id": r.conversation_id,
                "message_id": r.message_id,
                "task_type": r.task_type,
                "workflow_run_id": r.workflow_run_id,
                "model_id": r.model_id,
                "prompt_tokens": r.prompt_tokens,
                "completion_tokens": r.completion_tokens,
                "cost_usd": r.cost_usd,
                "created_at": r.created_at or now,
                "updated_at": r.updated_at or now,
            }
            for r in records
        ]
        stmt = pg_insert(WriteLlmUsage).values(values).on_conflict_do_nothing()
        await self._session.execute(stmt)
