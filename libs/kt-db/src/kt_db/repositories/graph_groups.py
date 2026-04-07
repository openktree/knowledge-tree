"""Repository for per-graph groups (source-level access control).

GraphGroup and GraphGroupMember live in each graph's schema, not the system DB.
This repository takes a graph-scoped session.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from kt_db.models import GraphGroup, GraphGroupMember


class GraphGroupRepository:
    """Per-graph group memberships. Session must be scoped to the graph's schema."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_user_group_names(self, user_id: uuid.UUID) -> list[str]:
        """Return the names of all groups the user belongs to in this graph."""
        stmt = (
            select(GraphGroup.name)
            .join(GraphGroupMember, GraphGroupMember.group_id == GraphGroup.id)
            .where(GraphGroupMember.user_id == user_id)
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())
