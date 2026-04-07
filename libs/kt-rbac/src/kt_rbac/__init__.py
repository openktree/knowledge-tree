"""kt-rbac — Knowledge Tree RBAC library.

Framework-agnostic roles, permissions, and policy evaluation.
"""

from kt_rbac.checker import PermissionChecker, PermissionDeniedError, default_checker
from kt_rbac.context import PermissionContext
from kt_rbac.policies import (
    DEFAULT_GRAPH_PUBLIC_PERMISSIONS,
    GRAPH_ROLE_PERMISSIONS,
    SUPERADMIN_PERMISSIONS,
)
from kt_rbac.source_access import can_access_fact, can_access_source
from kt_rbac.types import GraphRole, Permission, SystemRole

__all__ = [
    "GraphRole",
    "Permission",
    "SystemRole",
    "PermissionContext",
    "PermissionChecker",
    "PermissionDeniedError",
    "default_checker",
    "GRAPH_ROLE_PERMISSIONS",
    "SUPERADMIN_PERMISSIONS",
    "DEFAULT_GRAPH_PUBLIC_PERMISSIONS",
    "can_access_source",
    "can_access_fact",
]
