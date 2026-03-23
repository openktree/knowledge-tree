"""Repository for PageFetchLog — tracks processed URLs to avoid re-fetching."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from kt_db.models import PageFetchLog


class PageFetchLogRepository:
    """CRUD operations for page_fetch_log table."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_url(self, url: str) -> PageFetchLog | None:
        """Find a PageFetchLog entry by URL."""
        result = await self._session.execute(select(PageFetchLog).where(PageFetchLog.url == url))
        return result.scalar_one_or_none()

    async def is_fresh(self, url: str, stale_days: int = 30) -> bool:
        """Check if a URL was processed recently enough to skip.

        Returns True if the URL exists in the log and was fetched within
        the staleness window (i.e. it should be skipped).
        """
        entry = await self.get_by_url(url)
        if entry is None:
            return False
        cutoff = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=stale_days)
        return entry.fetched_at >= cutoff

    async def check_urls_freshness(self, urls: list[str], stale_days: int = 30) -> dict[str, bool]:
        """Batch check freshness for multiple URLs.

        Returns dict mapping URL -> True if fresh (should skip).
        """
        if not urls:
            return {}
        cutoff = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=stale_days)
        result = await self._session.execute(
            select(PageFetchLog.url, PageFetchLog.fetched_at).where(PageFetchLog.url.in_(urls))
        )
        rows = result.all()
        freshness: dict[str, bool] = {}
        found = {row.url: row.fetched_at for row in rows}
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
    ) -> PageFetchLog:
        """Record or update a page fetch in the log.

        Uses upsert: if the URL already exists, update fetched_at and metadata.
        """
        now = datetime.now(UTC).replace(tzinfo=None)
        new_id = uuid.uuid4()

        stmt = pg_insert(PageFetchLog).values(
            id=new_id,
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
        ).returning(PageFetchLog.id)

        result = await self._session.execute(stmt)
        returned_id = result.scalar_one()

        entry = await self._session.get(PageFetchLog, returned_id)
        assert entry is not None  # noqa: S101
        return entry
