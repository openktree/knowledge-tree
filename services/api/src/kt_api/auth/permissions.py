"""FastAPI permission dependencies — thin adapter over kt-rbac.

Usage in endpoints:

    @router.post("/reindex")
    async def reindex(
        user: User = Depends(require_system_permission(Permission.SYSTEM_ADMIN_OPS)),
    ): ...

    @router.get("/api/v1/graphs/{graph_slug}/nodes")
    async def list_nodes(
        ctx: GraphContext = Depends(require_graph_permission(Permission.GRAPH_READ)),
    ): ...
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from kt_api.auth.tokens import require_auth
from kt_api.graph_context import GraphContext, get_graph_context
from kt_db.models import GraphGroupMember, User
from kt_rbac import Permission, PermissionDeniedError, default_checker
from kt_rbac.context import PermissionContext
from kt_rbac.types import GraphRole


def require_system_permission(permission: Permission) -> Callable[..., Any]:
    """FastAPI dependency factory for system-level permissions.

    Returns the authenticated User if the permission check passes.
    """

    async def _check(user: User = Depends(require_auth)) -> User:
        ctx = PermissionContext(user_id=user.id, is_superuser=user.is_superuser)
        try:
            default_checker.check_or_raise(ctx, permission)
        except PermissionDeniedError:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Requires permission: {permission.value}",
            )
        return user

    return _check


def require_graph_permission(permission: Permission) -> Callable[..., Any]:
    """FastAPI dependency factory for graph-scoped permissions.

    Returns the resolved GraphContext if the permission check passes.
    """

    async def _check(ctx: GraphContext = Depends(get_graph_context)) -> GraphContext:
        perm_ctx = PermissionContext(
            user_id=ctx.user.id,
            is_superuser=ctx.user.is_superuser,
            graph_role=ctx.user_role,
            is_default_graph=ctx.graph.is_default,
        )
        try:
            default_checker.check_or_raise(perm_ctx, permission)
        except PermissionDeniedError:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Requires permission: {permission.value}",
            )
        return ctx

    return _check


async def load_user_graph_groups(user_id: Any, graph_session: AsyncSession) -> list[str]:
    """Load a user's group names from a graph's schema.

    Queries graph_group_members → graph_groups in the graph-specific schema.
    """
    from kt_db.models import GraphGroup

    stmt = (
        select(GraphGroup.name)
        .join(GraphGroupMember, GraphGroupMember.group_id == GraphGroup.id)
        .where(GraphGroupMember.user_id == user_id)
    )
    result = await graph_session.execute(stmt)
    return list(result.scalars().all())


async def build_permission_context(
    user: User,
    graph_role: GraphRole | None,
    is_default_graph: bool,
    graph_session: AsyncSession,
) -> PermissionContext:
    """Build a full PermissionContext including the user's graph-local groups."""
    groups = await load_user_graph_groups(user.id, graph_session)
    return PermissionContext(
        user_id=user.id,
        is_superuser=user.is_superuser,
        graph_role=graph_role,
        is_default_graph=is_default_graph,
        user_groups=frozenset(groups),
    )


def has_permission(
    user: User,
    role: GraphRole | None,
    permission: Permission,
    *,
    is_default_graph: bool = False,
) -> bool:
    """Pure function for permission checks inside endpoint bodies."""
    ctx = PermissionContext(
        user_id=user.id,
        is_superuser=user.is_superuser,
        graph_role=role,
        is_default_graph=is_default_graph,
    )
    return default_checker.check(ctx, permission)
