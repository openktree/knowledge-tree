"""Repository for IngestSource CRUD."""

import uuid

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from kt_db.models import IngestSource


class IngestSourceRepository:
    """Repository for IngestSource CRUD."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self,
        conversation_id: uuid.UUID,
        source_type: str,
        original_name: str,
        stored_path: str | None = None,
        mime_type: str | None = None,
        file_size: int | None = None,
    ) -> IngestSource:
        """Create a new ingest source record."""
        source = IngestSource(
            id=uuid.uuid4(),
            conversation_id=conversation_id,
            source_type=source_type,
            original_name=original_name,
            stored_path=stored_path,
            mime_type=mime_type,
            file_size=file_size,
        )
        self._session.add(source)
        await self._session.flush()
        return source

    async def get_by_id(self, source_id: uuid.UUID) -> IngestSource | None:
        """Get an ingest source by ID."""
        result = await self._session.execute(
            select(IngestSource).where(IngestSource.id == source_id)
        )
        return result.scalar_one_or_none()

    async def get_by_conversation(self, conversation_id: uuid.UUID) -> list[IngestSource]:
        """Get all ingest sources for a conversation."""
        result = await self._session.execute(
            select(IngestSource)
            .where(IngestSource.conversation_id == conversation_id)
            .order_by(IngestSource.created_at)
        )
        return list(result.scalars().all())

    async def update_status(
        self,
        source_id: uuid.UUID,
        status: str,
        error: str | None = None,
    ) -> None:
        """Update the status of an ingest source."""
        values: dict[str, object] = {"status": status}
        if error is not None:
            values["error"] = error
        await self._session.execute(
            update(IngestSource).where(IngestSource.id == source_id).values(**values)
        )

    async def update_fields(self, source_id: uuid.UUID, **kwargs: object) -> None:
        """Update arbitrary fields on an ingest source."""
        if kwargs:
            await self._session.execute(
                update(IngestSource).where(IngestSource.id == source_id).values(**kwargs)
            )
