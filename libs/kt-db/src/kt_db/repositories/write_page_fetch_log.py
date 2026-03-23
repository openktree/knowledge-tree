"""Repository for WritePageFetchLog — tracks processed URLs to avoid re-fetching."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from kt_db.write_models import WritePageFetchLog


class WritePageFetchLogRepository:
    """CRUD operations for write_page_fetch_log table."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def check_urls_freshness(self, urls: list[str], stale_days: int = 30) -> dict[str, bool]:
        """Batch check freshness for multiple URLs.

        Returns dict mapping URL -> True if fresh (should skip).
        """
        if not urls:
            return {}
        cutoff = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=stale_days)
        result = await self._session.execute(
            select(WritePageFetchLog.url, WritePageFetchLog.fetched_at).where(WritePageFetchLog.url.in_(urls))
        )
        rows = result.all()
        found = {row.url: row.fetched_at for row in rows}
        freshness: dict[str, bool] = {}
        for url in urls:
            if url in found:
                freshness[url] = found[url] >= cutoff
            else:
                freshness[url] = False
        return freshness

    async def record_fetch(
        self,
        url: str,
        raw_source_id: uuid.UUID | None = None,
        content_hash: str | None = None,
        fact_count: int = 0,
        skip_reason: str | None = None,
    ) -> None:
        """Record or update a page fetch in the log.

        Uses upsert: if the URL already exists, update fetched_at and metadata.
        """
        now = datetime.now(UTC).replace(tzinfo=None)

        stmt = pg_insert(WritePageFetchLog).values(
            id=uuid.uuid4(),
            url=url,
            raw_source_id=raw_source_id,
            content_hash=content_hash,
            fact_count=fact_count,
            skip_reason=skip_reason,
            created_at=now,
            fetched_at=now,
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=["url"],
            set_={
                "raw_source_id": stmt.excluded.raw_source_id,
                "content_hash": stmt.excluded.content_hash,
                "fact_count": stmt.excluded.fact_count,
                "skip_reason": stmt.excluded.skip_reason,
                "fetched_at": stmt.excluded.fetched_at,
            },
        )
        await self._session.execute(stmt)
