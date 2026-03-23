"""Write-optimized node version repository.

Manages versioned snapshots for composite nodes (synthesis, perspective).
"""

import uuid

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from kt_db.write_models import WriteNodeVersion


class WriteNodeVersionRepository:
    """Repository for composite node version management in the write-db."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create_version(
        self,
        node_key: str,
        version_number: int,
        snapshot: dict | None,
        source_node_count: int,
    ) -> WriteNodeVersion:
        """Create a new version snapshot for a composite node."""
        version = WriteNodeVersion(
            id=uuid.uuid4(),
            node_key=node_key,
            version_number=version_number,
            snapshot=snapshot,
            source_node_count=source_node_count,
        )
        self._session.add(version)
        await self._session.flush()
        return version

    async def get_versions(self, node_key: str) -> list[WriteNodeVersion]:
        """Return all versions for a node, ordered by version_number descending."""
        stmt = (
            select(WriteNodeVersion)
            .where(WriteNodeVersion.node_key == node_key)
            .order_by(WriteNodeVersion.version_number.desc())
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def get_default_version(self, node_key: str) -> WriteNodeVersion | None:
        """Return the current default version for a node, or None."""
        stmt = select(WriteNodeVersion).where(
            WriteNodeVersion.node_key == node_key,
            WriteNodeVersion.is_default.is_(True),
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def update_default(self, node_key: str) -> WriteNodeVersion | None:
        """Set the version with the highest source_node_count as default.

        Clears is_default on all other versions for this node.
        Returns the new default version, or None if no versions exist.
        """
        # Find the version with the max source_node_count
        max_stmt = (
            select(WriteNodeVersion.id)
            .where(WriteNodeVersion.node_key == node_key)
            .order_by(WriteNodeVersion.source_node_count.desc())
            .limit(1)
        )
        max_result = await self._session.execute(max_stmt)
        best_id = max_result.scalar_one_or_none()

        if best_id is None:
            return None

        # Clear all defaults for this node
        clear_stmt = (
            update(WriteNodeVersion)
            .where(WriteNodeVersion.node_key == node_key)
            .values(is_default=False)
        )
        await self._session.execute(clear_stmt)

        # Set the best one as default
        set_stmt = (
            update(WriteNodeVersion)
            .where(WriteNodeVersion.id == best_id)
            .values(is_default=True)
        )
        await self._session.execute(set_stmt)
        await self._session.flush()

        # Fetch and return the new default
        result = await self._session.execute(
            select(WriteNodeVersion).where(WriteNodeVersion.id == best_id)
        )
        return result.scalar_one_or_none()

    async def next_version_number(self, node_key: str) -> int:
        """Return the next version number for a node (max + 1, or 1 if none)."""
        stmt = select(func.max(WriteNodeVersion.version_number)).where(
            WriteNodeVersion.node_key == node_key
        )
        result = await self._session.execute(stmt)
        current_max = result.scalar_one_or_none()
        return (current_max or 0) + 1
