"""Graph management endpoints — CRUD, member management, provisioning."""

from __future__ import annotations

import logging
import re
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from kt_api.auth.tokens import require_admin, require_auth
from kt_api.dependencies import get_db_session, get_graph_session_resolver
from kt_db.graph_sessions import GraphSessionResolver
from kt_db.models import Graph, Node, User
from kt_db.repositories.graphs import GraphRepository

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/graphs", tags=["graphs"])

_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{1,98}[a-z0-9]$")


# -- Schemas ----------------------------------------------------------------


class GraphResponse(BaseModel):
    id: str
    slug: str
    name: str
    description: str | None = None
    is_default: bool
    graph_type: str
    byok_enabled: bool
    storage_mode: str
    schema_name: str
    database_connection_id: str | None = None
    status: str
    created_by: str | None = None
    created_at: datetime
    updated_at: datetime
    member_count: int = 0
    node_count: int = 0


class CreateGraphRequest(BaseModel):
    slug: str = Field(..., min_length=3, max_length=100)
    name: str = Field(..., min_length=1, max_length=200)
    description: str | None = None
    graph_type: str = Field(default="v1", pattern="^v[0-9]+$")
    byok_enabled: bool = False
    storage_mode: str = Field(default="schema", pattern="^(schema|database)$")
    database_connection_config_key: str | None = None


class UpdateGraphRequest(BaseModel):
    name: str | None = None
    description: str | None = None


class GraphMemberResponse(BaseModel):
    id: str
    user_id: str
    email: str
    display_name: str | None = None
    role: str
    created_at: datetime


class AddMemberRequest(BaseModel):
    user_id: str
    role: str = Field(default="reader", pattern="^(reader|writer|admin)$")


class UpdateMemberRoleRequest(BaseModel):
    role: str = Field(..., pattern="^(reader|writer|admin)$")


# -- Helpers ----------------------------------------------------------------


def _graph_response(graph: Graph, member_count: int = 0, node_count: int = 0) -> GraphResponse:
    return GraphResponse(
        id=str(graph.id),
        slug=graph.slug,
        name=graph.name,
        description=graph.description,
        is_default=graph.is_default,
        graph_type=graph.graph_type,
        byok_enabled=graph.byok_enabled,
        storage_mode=graph.storage_mode,
        schema_name=graph.schema_name,
        database_connection_id=str(graph.database_connection_id) if graph.database_connection_id else None,
        status=graph.status,
        created_by=str(graph.created_by) if graph.created_by else None,
        created_at=graph.created_at,
        updated_at=graph.updated_at,
        member_count=member_count,
        node_count=node_count,
    )


async def _require_graph_access(
    slug: str,
    user: User,
    session: AsyncSession,
    min_role: str | None = None,
) -> Graph:
    """Load graph and verify access. Raises 404/403 as needed."""
    repo = GraphRepository(session)
    graph = await repo.get_by_slug(slug)
    if graph is None or graph.status == "deleted":
        raise HTTPException(status_code=404, detail="Graph not found")

    if graph.is_default:
        return graph

    if user.is_superuser:
        return graph

    role = await repo.get_member_role(graph.id, user.id)
    if role is None:
        raise HTTPException(status_code=403, detail="Not a member of this graph")

    if min_role:
        role_hierarchy = {"reader": 0, "writer": 1, "admin": 2}
        if role_hierarchy.get(role, 0) < role_hierarchy.get(min_role, 0):
            raise HTTPException(status_code=403, detail=f"Requires at least {min_role} role")

    return graph


# -- Graph CRUD -------------------------------------------------------------


@router.get("", response_model=list[GraphResponse])
async def list_graphs(
    user: User = Depends(require_auth),
    session: AsyncSession = Depends(get_db_session),
) -> list[GraphResponse]:
    """List graphs accessible to the current user."""
    repo = GraphRepository(session)
    graphs = await repo.list_accessible(user.id, user.is_superuser)

    results = []
    for g in graphs:
        members = await repo.get_members(g.id)
        results.append(_graph_response(g, member_count=len(members)))
    return results


@router.post("", response_model=GraphResponse, status_code=201)
async def create_graph(
    body: CreateGraphRequest,
    admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_db_session),
    resolver: GraphSessionResolver = Depends(get_graph_session_resolver),
) -> GraphResponse:
    """Create a new graph (admin only). Provisions schema synchronously."""
    if not _SLUG_RE.match(body.slug):
        raise HTTPException(
            status_code=400,
            detail="Slug must be 3-100 chars, lowercase alphanumeric/hyphens/underscores, "
            "starting and ending with alphanumeric",
        )

    if body.slug == "default":
        raise HTTPException(status_code=400, detail="Cannot create graph with reserved slug 'default'")

    repo = GraphRepository(session)
    existing = await repo.get_by_slug(body.slug)
    if existing:
        raise HTTPException(status_code=409, detail=f"Graph with slug '{body.slug}' already exists")

    # Resolve database connection if specified
    db_conn_id: uuid.UUID | None = None
    if body.database_connection_config_key:
        db_conn = await repo.get_database_connection_by_key(body.database_connection_config_key)
        if db_conn is None:
            raise HTTPException(
                status_code=400,
                detail=f"Database connection '{body.database_connection_config_key}' not found",
            )
        db_conn_id = db_conn.id

    schema_name = f"graph_{body.slug.replace('-', '_')}"

    graph = await repo.create(
        slug=body.slug,
        name=body.name,
        description=body.description,
        graph_type=body.graph_type,
        byok_enabled=body.byok_enabled,
        storage_mode=body.storage_mode,
        schema_name=schema_name,
        database_connection_id=db_conn_id,
        created_by=admin.id,
        status="provisioning",
    )
    await session.commit()

    # Provision schema + Qdrant collections synchronously
    try:
        await _provision_graph(graph, session, resolver)
        graph = await repo.update(graph, status="active")
        await session.commit()
    except Exception:
        logger.exception("Failed to provision graph '%s'", body.slug)
        graph = await repo.update(graph, status="error")
        await session.commit()
        raise HTTPException(status_code=500, detail="Graph provisioning failed")

    # Add creator as admin member
    await repo.add_member(graph.id, admin.id, role="admin")
    await session.commit()

    return _graph_response(graph, member_count=1)


@router.get("/{slug}", response_model=GraphResponse)
async def get_graph(
    slug: str,
    user: User = Depends(require_auth),
    session: AsyncSession = Depends(get_db_session),
    resolver: GraphSessionResolver = Depends(get_graph_session_resolver),
) -> GraphResponse:
    """Get graph details with usage stats."""
    graph = await _require_graph_access(slug, user, session)
    repo = GraphRepository(session)
    members = await repo.get_members(graph.id)

    # Get node count from the graph's own schema
    node_count = 0
    if graph.status == "active":
        try:
            gs = await resolver.resolve(graph.id)
            async with gs.graph_session_factory() as graph_session:
                result = await graph_session.execute(select(func.count(Node.id)))
                node_count = result.scalar_one() or 0
        except Exception:
            logger.warning("Could not fetch node count for graph '%s'", slug)

    return _graph_response(graph, member_count=len(members), node_count=node_count)


@router.put("/{slug}", response_model=GraphResponse)
async def update_graph(
    slug: str,
    body: UpdateGraphRequest,
    user: User = Depends(require_auth),
    session: AsyncSession = Depends(get_db_session),
) -> GraphResponse:
    """Update graph name/description. Requires admin role on the graph."""
    graph = await _require_graph_access(slug, user, session, min_role="admin")
    repo = GraphRepository(session)
    graph = await repo.update(graph, name=body.name, description=body.description)
    await session.commit()
    members = await repo.get_members(graph.id)
    return _graph_response(graph, member_count=len(members))


@router.delete("/{slug}", status_code=204)
async def delete_graph(
    slug: str,
    _admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_db_session),
) -> None:
    """Soft-delete a graph (superadmin only). Default graph cannot be deleted."""
    repo = GraphRepository(session)
    graph = await repo.get_by_slug(slug)
    if graph is None:
        raise HTTPException(status_code=404, detail="Graph not found")
    if graph.is_default:
        raise HTTPException(status_code=400, detail="Cannot delete the default graph")
    await repo.update(graph, status="deleted")
    await session.commit()


# -- Member management ------------------------------------------------------


@router.get("/{slug}/members", response_model=list[GraphMemberResponse])
async def list_graph_members(
    slug: str,
    user: User = Depends(require_auth),
    session: AsyncSession = Depends(get_db_session),
) -> list[GraphMemberResponse]:
    """List members of a graph."""
    graph = await _require_graph_access(slug, user, session)
    repo = GraphRepository(session)
    members = await repo.get_members(graph.id)

    # Fetch user details
    user_ids = [m.user_id for m in members]
    if not user_ids:
        return []

    result = await session.execute(select(User).where(User.id.in_(user_ids)))
    users_by_id = {u.id: u for u in result.unique().scalars().all()}

    return [
        GraphMemberResponse(
            id=str(m.id),
            user_id=str(m.user_id),
            email=users_by_id[m.user_id].email if m.user_id in users_by_id else "",
            display_name=getattr(users_by_id.get(m.user_id), "display_name", None),
            role=m.role,
            created_at=m.created_at,
        )
        for m in members
    ]


@router.post("/{slug}/members", response_model=GraphMemberResponse, status_code=201)
async def add_graph_member(
    slug: str,
    body: AddMemberRequest,
    user: User = Depends(require_auth),
    session: AsyncSession = Depends(get_db_session),
) -> GraphMemberResponse:
    """Add a member to a graph. Requires admin role on the graph."""
    graph = await _require_graph_access(slug, user, session, min_role="admin")
    repo = GraphRepository(session)

    try:
        target_id = uuid.UUID(body.user_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid user_id")

    # Check user exists
    result = await session.execute(select(User).where(User.id == target_id))
    target_user = result.unique().scalar_one_or_none()
    if target_user is None:
        raise HTTPException(status_code=404, detail="User not found")

    # Check not already a member
    existing_role = await repo.get_member_role(graph.id, target_id)
    if existing_role is not None:
        raise HTTPException(status_code=409, detail="User is already a member")

    member = await repo.add_member(graph.id, target_id, role=body.role)
    await session.commit()

    return GraphMemberResponse(
        id=str(member.id),
        user_id=str(target_id),
        email=target_user.email,
        display_name=getattr(target_user, "display_name", None),
        role=member.role,
        created_at=member.created_at,
    )


@router.put("/{slug}/members/{user_id}", response_model=GraphMemberResponse)
async def update_graph_member_role(
    slug: str,
    user_id: str,
    body: UpdateMemberRoleRequest,
    user: User = Depends(require_auth),
    session: AsyncSession = Depends(get_db_session),
) -> GraphMemberResponse:
    """Change a member's role. Requires admin role on the graph."""
    graph = await _require_graph_access(slug, user, session, min_role="admin")
    repo = GraphRepository(session)

    try:
        target_id = uuid.UUID(user_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid user_id")

    member = await repo.update_member_role(graph.id, target_id, body.role)
    if member is None:
        raise HTTPException(status_code=404, detail="Member not found")

    await session.commit()

    result = await session.execute(select(User).where(User.id == target_id))
    target_user = result.unique().scalar_one_or_none()

    return GraphMemberResponse(
        id=str(member.id),
        user_id=str(target_id),
        email=target_user.email if target_user else "",
        display_name=getattr(target_user, "display_name", None) if target_user else None,
        role=member.role,
        created_at=member.created_at,
    )


@router.delete("/{slug}/members/{user_id}", status_code=204)
async def remove_graph_member(
    slug: str,
    user_id: str,
    user: User = Depends(require_auth),
    session: AsyncSession = Depends(get_db_session),
) -> None:
    """Remove a member from a graph. Requires admin role on the graph."""
    graph = await _require_graph_access(slug, user, session, min_role="admin")
    repo = GraphRepository(session)

    try:
        target_id = uuid.UUID(user_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid user_id")

    removed = await repo.remove_member(graph.id, target_id)
    if not removed:
        raise HTTPException(status_code=404, detail="Member not found")
    await session.commit()


# -- Provisioning -----------------------------------------------------------


async def _provision_graph(
    graph: Graph,
    session: AsyncSession,
    resolver: GraphSessionResolver,
) -> None:
    """Create schema + Qdrant collections for a new graph.

    For storage_mode="schema", creates the schema in the graph-db and write-db.
    For storage_mode="database", the schema is created in the configured database.
    """
    schema = graph.schema_name
    if schema == "public":
        return  # Default graph, nothing to provision

    # Create schema in graph-db
    await session.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{schema}"'))
    await session.commit()

    # Create schema in write-db (same DB for schema mode, different for database mode)
    gs = await resolver.resolve(graph.id)
    async with gs.write_session_factory() as write_session:
        await write_session.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{schema}"'))
        await write_session.commit()

    # Create Qdrant collections
    try:
        from kt_qdrant.client import get_qdrant_client
        from kt_qdrant.repositories.facts import QdrantFactRepository
        from kt_qdrant.repositories.nodes import QdrantNodeRepository
        from kt_qdrant.repositories.seeds import QdrantSeedRepository

        client = get_qdrant_client()
        prefix = gs.qdrant_collection_prefix

        await QdrantFactRepository(client, f"{prefix}facts").ensure_collection()
        await QdrantNodeRepository(client, f"{prefix}nodes").ensure_collection()
        await QdrantSeedRepository(client, f"{prefix}seeds").ensure_collection()
    except Exception:
        logger.warning("Qdrant collection creation failed for graph '%s'", graph.slug, exc_info=True)
