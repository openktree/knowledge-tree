"""Role-to-permission mappings.

To grant a new permission to a role, add it to the appropriate frozenset below.
No schema migration needed — this is pure code configuration.
"""

from __future__ import annotations

from kt_rbac.types import GraphRole, Permission

GRAPH_ROLE_PERMISSIONS: dict[GraphRole, frozenset[Permission]] = {
    GraphRole.reader: frozenset(
        {
            Permission.GRAPH_READ,
            Permission.SOURCE_READ,
        }
    ),
    GraphRole.writer: frozenset(
        {
            Permission.GRAPH_READ,
            Permission.GRAPH_WRITE,
            Permission.SOURCE_READ,
            Permission.SOURCE_WRITE,
        }
    ),
    GraphRole.admin: frozenset(
        {
            Permission.GRAPH_READ,
            Permission.GRAPH_WRITE,
            Permission.GRAPH_MANAGE_MEMBERS,
            Permission.GRAPH_MANAGE_METADATA,
            Permission.SOURCE_READ,
            Permission.SOURCE_WRITE,
        }
    ),
}

# Superadmin has every permission defined in the enum.
SUPERADMIN_PERMISSIONS: frozenset[Permission] = frozenset(Permission)

# Default graph: authenticated users get these without membership.
DEFAULT_GRAPH_PUBLIC_PERMISSIONS: frozenset[Permission] = frozenset(
    {
        Permission.GRAPH_READ,
        Permission.SOURCE_READ,
    }
)
