"""RBAC types — roles and permissions for the Knowledge Tree system."""

from __future__ import annotations

from enum import Enum


class SystemRole(str, Enum):
    """System-level roles. Stored on User.is_superuser (superadmin) or implied (user)."""

    user = "user"
    superadmin = "superadmin"


class GraphRole(str, Enum):
    """Graph-level roles. Stored in GraphMember.role."""

    reader = "reader"
    writer = "writer"
    admin = "admin"


class Permission(str, Enum):
    """All permissions in the system, namespaced by scope.

    To add a new permission:
    1. Add the enum value here
    2. Add it to the appropriate role mapping in policies.py
    """

    # System scope — superadmin only (for now)
    SYSTEM_MANAGE_USERS = "system:manage_users"
    SYSTEM_MANAGE_GRAPHS = "system:manage_graphs"
    SYSTEM_MANAGE_SETTINGS = "system:manage_settings"
    SYSTEM_MANAGE_INVITES = "system:manage_invites"
    SYSTEM_ADMIN_OPS = "system:admin_ops"

    # Graph scope — role-based
    GRAPH_READ = "graph:read"
    GRAPH_WRITE = "graph:write"
    GRAPH_MANAGE_MEMBERS = "graph:manage_members"
    GRAPH_MANAGE_METADATA = "graph:manage_metadata"

    # Source scope — role-based + group-based
    SOURCE_READ = "source:read"
    SOURCE_WRITE = "source:write"
