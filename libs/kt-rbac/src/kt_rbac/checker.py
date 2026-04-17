"""Permission checker — the swappable core of kt-rbac.

To migrate to Casbin/Oso: replace the internals of PermissionChecker.
The PermissionContext input and the public method signatures stay stable.
"""

from __future__ import annotations

from kt_rbac.context import PermissionContext
from kt_rbac.policies import (
    DEFAULT_GRAPH_PUBLIC_PERMISSIONS,
    GRAPH_ROLE_PERMISSIONS,
)
from kt_rbac.source_access import can_access_fact, can_access_source
from kt_rbac.types import Permission


class PermissionDeniedError(Exception):
    """Raised when a permission check fails."""

    def __init__(self, permission: Permission, context: PermissionContext) -> None:
        self.permission = permission
        self.context = context
        super().__init__(f"Permission denied: {permission.value} (user={context.user_id}, role={context.graph_role})")


class PermissionChecker:
    """Evaluates whether a permission context satisfies a required permission.

    This is the class you'd replace if swapping to Casbin/Oso.
    The interface stays stable — only internals change.
    """

    def check(self, ctx: PermissionContext, permission: Permission) -> bool:
        """Returns True if the context has the required permission."""
        granted = self._evaluate(ctx, permission)
        # Fire-and-forget audit hook for plugins (SIEM, access log, anomaly
        # detection). Must not block the critical path — handlers run on
        # the event loop in a detached task.
        try:
            from kt_plugins import plugin_manager as _pm

            _pm.hook_registry.fire_and_forget(
                "auth.permission_check",
                user_id=str(ctx.user_id) if ctx.user_id is not None else None,
                permission=permission.value,
                granted=granted,
                graph_role=ctx.graph_role.value if ctx.graph_role is not None else None,
                is_default_graph=ctx.is_default_graph,
            )
        except Exception:
            # A broken plugin registry must not break auth checks — but
            # log at DEBUG (not silently swallowed) so import errors or
            # registry bugs are still discoverable in verbose logs.
            import logging as _logging

            _logging.getLogger(__name__).debug(
                "auth.permission_check hook emit failed (non-fatal)",
                exc_info=True,
            )
        return granted

    def _evaluate(self, ctx: PermissionContext, permission: Permission) -> bool:
        if ctx.is_superuser:
            return True

        # System permissions — superadmin only (for now)
        if permission.value.startswith("system:"):
            return False

        # Default graph — public read, superadmin-only write
        if ctx.is_default_graph:
            return permission in DEFAULT_GRAPH_PUBLIC_PERMISSIONS

        # Graph permissions — check role mapping
        if ctx.graph_role is None:
            return False
        role_perms = GRAPH_ROLE_PERMISSIONS.get(ctx.graph_role, frozenset())
        return permission in role_perms

    def check_source_access(
        self,
        ctx: PermissionContext,
        source_access_groups: list[str] | None,
    ) -> bool:
        """Check if user can access a source based on its access_groups."""
        return can_access_source(ctx, source_access_groups)

    def check_fact_access(
        self,
        ctx: PermissionContext,
        fact_source_access_groups: list[list[str] | None],
    ) -> bool:
        """Check if user can access a fact via any of its sources."""
        return can_access_fact(ctx, fact_source_access_groups)

    def check_or_raise(self, ctx: PermissionContext, permission: Permission) -> None:
        """Check permission, raise PermissionDenied if not allowed."""
        if not self.check(ctx, permission):
            raise PermissionDeniedError(permission=permission, context=ctx)


# Module-level singleton — use this unless you need custom config.
default_checker = PermissionChecker()
