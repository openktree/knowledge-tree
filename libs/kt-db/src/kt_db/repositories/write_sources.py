"""Write-optimized raw source repository.

All operations target the write-db.  Primary repository for source storage
during pipelines — the sync worker propagates to graph-db.
"""

import hashlib
import logging
import uuid

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from kt_db.write_models import WriteRawSource

logger = logging.getLogger(__name__)


class WriteSourceRepository:
    """Upsert-friendly repository for raw sources in the write-optimized database."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    @staticmethod
    def compute_hash(content: str) -> str:
        """Compute SHA-256 hash of content for deduplication."""
        return hashlib.sha256(content.encode()).hexdigest()

    async def get_by_id(self, source_id: uuid.UUID) -> WriteRawSource | None:
        """Find a WriteRawSource by its ID."""
        result = await self._session.execute(select(WriteRawSource).where(WriteRawSource.id == source_id))
        return result.scalar_one_or_none()

    async def get_by_content_hash(self, content_hash: str) -> WriteRawSource | None:
        """Find a WriteRawSource by its content hash."""
        result = await self._session.execute(
            select(WriteRawSource).where(WriteRawSource.content_hash == content_hash).limit(1)
        )
        return result.scalar_one_or_none()

    async def create_or_get(
        self,
        *,
        source_id: uuid.UUID | None = None,
        uri: str,
        title: str | None,
        raw_content: str | None,
        content_hash: str | None = None,
        provider_id: str,
        provider_metadata: dict | None = None,
    ) -> WriteRawSource:
        """Insert or return existing source, deduplicating by URI then content_hash.

        First checks for an existing source with the same URI to prevent
        duplicate entries when search engines return different snippets for
        the same URL across queries.  Falls back to content_hash upsert for
        genuinely new URLs.
        """
        # Deduplicate by URI first — same URL should always reuse the
        # existing source regardless of snippet content.
        existing = (
            await self._session.execute(select(WriteRawSource).where(WriteRawSource.uri == uri).limit(1))
        ).scalar_one_or_none()
        if existing is not None:
            return existing

        if content_hash is None:
            content_hash = self.compute_hash(raw_content or "")
        if source_id is None:
            source_id = uuid.uuid4()

        stmt = (
            pg_insert(WriteRawSource)
            .values(
                id=source_id,
                uri=uri,
                title=title,
                raw_content=raw_content,
                content_hash=content_hash,
                provider_id=provider_id,
                provider_metadata=provider_metadata,
            )
            .on_conflict_do_update(
                index_elements=["content_hash"],
                set_={"content_hash": pg_insert(WriteRawSource).excluded.content_hash},
            )
            .returning(WriteRawSource.id)
        )
        result = await self._session.execute(stmt)
        returned_id = result.scalar_one()

        source = await self.get_by_id(returned_id)
        assert source is not None  # noqa: S101
        return source

    async def update_content(
        self,
        source_id: uuid.UUID,
        new_content: str,
        is_full_text: bool = True,
        content_type: str | None = None,
    ) -> bool:
        """Replace raw_content with full-text content and update content_hash.

        Returns True if updated, False if another record already has this hash.
        """
        new_hash = self.compute_hash(new_content)

        # Check for hash collision with a different record
        existing = await self._session.execute(
            select(WriteRawSource).where(
                WriteRawSource.content_hash == new_hash,
                WriteRawSource.id != source_id,
            )
        )
        if existing.scalar_one_or_none() is not None:
            return False

        values: dict[str, object] = {
            "raw_content": new_content,
            "content_hash": new_hash,
            "is_full_text": is_full_text,
        }
        if content_type is not None:
            values["content_type"] = content_type
        await self._session.execute(update(WriteRawSource).where(WriteRawSource.id == source_id).values(**values))
        await self._session.flush()
        return True

    async def mark_fetch_attempted(self, source_id: uuid.UUID) -> None:
        """Mark a source as having had a fetch attempt (success or failure)."""
        await self._session.execute(
            update(WriteRawSource).where(WriteRawSource.id == source_id).values(fetch_attempted=True)
        )
        await self._session.flush()
        return True
