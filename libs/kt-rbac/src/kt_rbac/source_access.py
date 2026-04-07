"""Source-level access evaluation.

Sources are the protection boundary for proprietary information.
Facts inherit visibility from their sources — a user sees a fact if they
can access at least one of its sources.

access_groups on a source:
  - None or []  → public (open access to anyone with graph membership)
  - ["group_a", "group_b"] → restricted to users in at least one group
"""

from __future__ import annotations

from kt_rbac.context import PermissionContext
from kt_rbac.types import GraphRole


def can_access_source(
    ctx: PermissionContext,
    source_access_groups: list[str] | None,
) -> bool:
    """Check if user can access a source based on its access_groups.

    Rules:
    - Superuser: always
    - Graph admin: always (full visibility within their graph)
    - access_groups is None or empty: public
    - access_groups is non-empty: user must belong to at least one group
    """
    if ctx.is_superuser:
        return True
    if ctx.graph_role == GraphRole.admin:
        return True
    if not source_access_groups:
        return True
    return bool(ctx.user_groups & frozenset(source_access_groups))


def can_access_fact(
    ctx: PermissionContext,
    fact_source_access_groups: list[list[str] | None],
) -> bool:
    """Check if user can access a fact based on ANY of its sources.

    A fact is visible if the user can access at least one of its sources.
    If the fact has no sources, it is treated as public.
    """
    if not fact_source_access_groups:
        return True
    return any(can_access_source(ctx, groups) for groups in fact_source_access_groups)
