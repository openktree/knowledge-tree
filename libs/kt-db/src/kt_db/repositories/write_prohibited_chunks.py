"""Write-db repository for prohibited chunks."""

import uuid

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from kt_db.write_models import WriteProhibitedChunk, WriteRawSource


class WriteProhibitedChunkRepository:
    """Repository for storing prohibited chunks in the write-optimized database."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self,
        *,
        source_content_hash: str,
        chunk_text: str,
        model_id: str,
        error_message: str,
        fallback_model_id: str | None = None,
        fallback_error: str | None = None,
    ) -> WriteProhibitedChunk:
        """Store a prohibited chunk and increment the source's counter."""
        chunk = WriteProhibitedChunk(
            id=uuid.uuid4(),
            source_content_hash=source_content_hash,
            chunk_text=chunk_text,
            model_id=model_id,
            fallback_model_id=fallback_model_id,
            error_message=error_message,
            fallback_error=fallback_error,
        )
        self._session.add(chunk)
        await self._session.flush()

        # Increment the counter on the corresponding WriteRawSource
        await self._session.execute(
            update(WriteRawSource)
            .where(WriteRawSource.content_hash == source_content_hash)
            .values(prohibited_chunk_count=WriteRawSource.prohibited_chunk_count + 1)
        )
        await self._session.flush()
        return chunk
