"""Permission context — framework-agnostic input for permission checks."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from kt_rbac.types import GraphRole


@dataclass(frozen=True)
class PermissionContext:
    """Everything the permission checker needs to evaluate a request.

    This is the abstraction boundary: FastAPI/MCP adapters build this from
    their framework-specific models (User, GraphContext, OAuth token).
    If we swap to Casbin/Oso, this dataclass stays — only the checker changes.
    """

    user_id: uuid.UUID
    is_superuser: bool
    graph_role: GraphRole | None = None
    is_default_graph: bool = False
    # Graph-local group names the user belongs to (from graph_group_members).
    user_groups: frozenset[str] = field(default_factory=frozenset)
