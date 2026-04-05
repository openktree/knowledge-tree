"""Graph context for graph-scoped API endpoints.

Provides a FastAPI dependency that resolves a graph by slug, checks user
access, and yields the correct session factories for that graph.
"""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import Depends, HTTPException, Path, Request
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from kt_api.auth.tokens import require_auth
from kt_api.dependencies import get_db_session, get_graph_session_resolver
from kt_db.graph_sessions import GraphInfo, GraphSessionResolver
from kt_db.models import User
from kt_db.repositories.graphs import GraphRepository


@dataclass
class GraphContext:
    """Resolved graph with scoped session factories for graph-scoped endpoints.

    Uses GraphInfo (frozen dataclass) instead of the ORM Graph instance
    to avoid DetachedInstanceError when the request session closes.
    Only scalar fields are available — no relationship access.
    """

    graph: GraphInfo
    graph_session_factory: async_sessionmaker[AsyncSession]
    write_session_factory: async_sessionmaker[AsyncSession]
    qdrant_collection_prefix: str
    user: User
    user_role: str | None  # None for superuser or default graph


async def get_graph_context(
    request: Request,
    graph_slug: str = Path(..., description="Graph slug"),
    user: User = Depends(require_auth),
    session: AsyncSession = Depends(get_db_session),
    resolver: GraphSessionResolver = Depends(get_graph_session_resolver),
) -> GraphContext:
    """FastAPI dependency that resolves a graph-scoped context.

    Checks that:
    - The graph exists and is active
    - The user has access (superuser, member, or it's the default graph)

    Returns a GraphContext with the correct session factories.
    """
    repo = GraphRepository(session)
    graph = await repo.get_by_slug(graph_slug)

    if graph is None or graph.status == "deleted":
        raise HTTPException(status_code=404, detail="Graph not found")

    if graph.status != "active":
        raise HTTPException(status_code=503, detail="Graph is not yet active")

    # Check token-level graph scope (set on request.state by require_auth for API tokens)
    token_graph_slugs: list[str] | None = getattr(request.state, "token_graph_slugs", None)
    if token_graph_slugs is not None and graph_slug not in token_graph_slugs:
        raise HTTPException(status_code=403, detail="Token does not have access to this graph")

    # Access check
    user_role: str | None = None
    if not graph.is_default and not user.is_superuser:
        user_role = await repo.get_member_role(graph.id, user.id)
        if user_role is None:
            raise HTTPException(status_code=403, detail="Not a member of this graph")
    elif not graph.is_default:
        # Superuser — still fetch role for informational purposes
        user_role = await repo.get_member_role(graph.id, user.id)

    gs = await resolver.resolve(graph.id)

    return GraphContext(
        graph=gs.graph,  # GraphInfo — detached, safe to use after session closes
        graph_session_factory=gs.graph_session_factory,
        write_session_factory=gs.write_session_factory,
        qdrant_collection_prefix=gs.qdrant_collection_prefix,
        user=user,
        user_role=user_role,
    )


def require_writer(ctx: GraphContext) -> GraphContext:
    """Verify the user has at least writer access on the graph.

    Default graph: superuser-only for writes (no membership model).
    Non-default graphs: requires writer or admin role.
    """
    if ctx.user.is_superuser:
        return ctx
    if ctx.graph.is_default:
        raise HTTPException(status_code=403, detail="Admin access required for default graph writes")
    if ctx.user_role in ("writer", "admin"):
        return ctx
    raise HTTPException(status_code=403, detail="Requires at least writer role")


def require_graph_admin(ctx: GraphContext) -> GraphContext:
    """Verify the user has admin access on the graph."""
    if ctx.user.is_superuser:
        return ctx
    if ctx.user_role == "admin":
        return ctx
    raise HTTPException(status_code=403, detail="Requires admin role on this graph")
