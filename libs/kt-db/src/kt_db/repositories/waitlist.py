"""Repository for waitlist entries."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from kt_db.models import WaitlistEntry


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class WaitlistRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self,
        email: str,
        display_name: str | None = None,
        message: str | None = None,
    ) -> WaitlistEntry:
        entry = WaitlistEntry(
            id=uuid.uuid4(),
            email=email.strip().lower(),
            display_name=display_name,
            message=message,
            status="pending",
        )
        self._session.add(entry)
        await self._session.flush()
        return entry

    async def list_all(
        self,
        offset: int = 0,
        limit: int = 50,
        status_filter: str | None = None,
    ) -> tuple[list[WaitlistEntry], int]:
        base = select(WaitlistEntry)
        count_q = select(func.count()).select_from(WaitlistEntry)
        if status_filter:
            base = base.where(WaitlistEntry.status == status_filter)
            count_q = count_q.where(WaitlistEntry.status == status_filter)

        total = (await self._session.execute(count_q)).scalar_one()
        rows = (
            (await self._session.execute(base.order_by(WaitlistEntry.created_at).offset(offset).limit(limit)))
            .scalars()
            .all()
        )
        return list(rows), total

    async def get_by_id(self, entry_id: uuid.UUID) -> WaitlistEntry | None:
        result = await self._session.execute(select(WaitlistEntry).where(WaitlistEntry.id == entry_id))
        return result.scalar_one_or_none()

    async def update_status(
        self,
        entry_id: uuid.UUID,
        status: str,
        reviewed_by: uuid.UUID,
    ) -> WaitlistEntry | None:
        await self._session.execute(
            update(WaitlistEntry)
            .where(WaitlistEntry.id == entry_id)
            .values(status=status, reviewed_at=_utcnow(), reviewed_by=reviewed_by)
        )
        await self._session.flush()
        return await self.get_by_id(entry_id)

    async def exists_pending(self, email: str) -> bool:
        result = await self._session.execute(
            select(func.count())
            .select_from(WaitlistEntry)
            .where(
                WaitlistEntry.email == email.strip().lower(),
                WaitlistEntry.status == "pending",
            )
        )
        return (result.scalar_one() or 0) > 0
