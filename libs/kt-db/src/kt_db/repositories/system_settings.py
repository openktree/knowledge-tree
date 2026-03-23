"""Repository for admin-configurable system settings."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from kt_db.models import SystemSetting


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class SystemSettingsRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, key: str) -> str | None:
        result = await self._session.execute(select(SystemSetting).where(SystemSetting.key == key))
        row = result.scalar_one_or_none()
        return row.value if row else None

    async def get_bool(self, key: str, default: bool = False) -> bool:
        val = await self.get(key)
        if val is None:
            return default
        return val.lower() in ("true", "1", "yes")

    async def set(self, key: str, value: str) -> None:
        stmt = pg_insert(SystemSetting).values(key=key, value=value, updated_at=_utcnow())
        stmt = stmt.on_conflict_do_update(
            index_elements=["key"],
            set_={"value": value, "updated_at": _utcnow()},
        )
        await self._session.execute(stmt)
        await self._session.flush()
