"""Repository for RBAC roles and user-role assignments."""

from __future__ import annotations

import uuid

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from kt_db.models import Role, UserRole


class RoleRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # -- Role CRUD -------------------------------------------------------------

    async def list_all(self) -> list[Role]:
        result = await self._session.execute(select(Role).order_by(Role.name))
        return list(result.scalars().all())

    async def get_by_id(self, role_id: uuid.UUID) -> Role | None:
        result = await self._session.execute(select(Role).where(Role.id == role_id))
        return result.scalar_one_or_none()

    async def get_by_name(self, name: str) -> Role | None:
        result = await self._session.execute(select(Role).where(Role.name == name))
        return result.scalar_one_or_none()

    async def create(
        self,
        name: str,
        permissions: dict[str, bool],
        *,
        is_system: bool = False,
    ) -> Role:
        role = Role(
            id=uuid.uuid4(),
            name=name,
            permissions=permissions,
            is_system=is_system,
        )
        self._session.add(role)
        await self._session.flush()
        return role

    async def update_permissions(self, role_id: uuid.UUID, permissions: dict[str, bool]) -> Role | None:
        role = await self.get_by_id(role_id)
        if role is None:
            return None
        role.permissions = permissions
        await self._session.flush()
        return role

    async def delete(self, role_id: uuid.UUID) -> bool:
        """Delete a non-system role. Returns False if role is system or not found."""
        role = await self.get_by_id(role_id)
        if role is None or role.is_system:
            return False
        await self._session.execute(delete(Role).where(Role.id == role_id))
        await self._session.flush()
        return True

    # -- User-Role assignments -------------------------------------------------

    async def assign_role(self, user_id: uuid.UUID, role_id: uuid.UUID) -> None:
        stmt = pg_insert(UserRole).values(user_id=user_id, role_id=role_id).on_conflict_do_nothing()
        await self._session.execute(stmt)
        await self._session.flush()

    async def remove_role(self, user_id: uuid.UUID, role_id: uuid.UUID) -> bool:
        result = await self._session.execute(
            delete(UserRole).where(UserRole.user_id == user_id, UserRole.role_id == role_id)
        )
        await self._session.flush()
        return result.rowcount > 0

    async def get_user_roles(self, user_id: uuid.UUID) -> list[Role]:
        result = await self._session.execute(select(Role).join(UserRole).where(UserRole.user_id == user_id))
        return list(result.scalars().all())

    async def get_user_permissions(self, user_id: uuid.UUID) -> set[str]:
        """Return the union of all permission keys across the user's roles."""
        roles = await self.get_user_roles(user_id)
        permissions: set[str] = set()
        for role in roles:
            for key, granted in (role.permissions or {}).items():
                if granted:
                    permissions.add(key)
        return permissions
