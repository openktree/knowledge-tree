"""RBAC permission enforcement via FastAPI dependencies."""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from typing import Any

from fastapi import Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from kt_api.auth.tokens import require_auth
from kt_api.dependencies import get_db_session
from kt_db.models import User
from kt_db.repositories.roles import RoleRepository


async def get_user_permissions(
    user: User = Depends(require_auth),
    session: AsyncSession = Depends(get_db_session),
) -> set[str]:
    """FastAPI dependency: return the set of permission keys for the current user.

    Superusers get all permissions automatically.
    """
    if user.is_superuser:
        # Superusers bypass RBAC — they have every permission
        return {"*"}
    repo = RoleRepository(session)
    return await repo.get_user_permissions(user.id)


def require_permission(
    *permissions: str,
) -> Callable[..., Coroutine[Any, Any, User]]:
    """Factory that returns a FastAPI dependency enforcing RBAC permissions.

    Usage::

        @router.post("/syntheses")
        async def create_synthesis(
            user: User = Depends(require_permission("syntheses.create")),
        ): ...

    The dependency resolves the current user's roles, unions their
    permission sets, and raises 403 if any required permission is missing.
    Superusers always pass.
    """

    async def _check(
        user: User = Depends(require_auth),
        session: AsyncSession = Depends(get_db_session),
    ) -> User:
        if user.is_superuser:
            return user

        repo = RoleRepository(session)
        user_perms = await repo.get_user_permissions(user.id)

        # Wildcard grants everything
        if "*" in user_perms:
            return user

        for perm in permissions:
            if perm not in user_perms:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail=f"Missing permission: {perm}",
                )
        return user

    return _check
