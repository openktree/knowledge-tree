"""Graph management endpoints — CRUD, member management, provisioning."""

from __future__ import annotations

import asyncio
import logging
import re
import uuid
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from kt_api.auth.permissions import require_system_permission
from kt_api.auth.tokens import require_auth
from kt_api.dependencies import get_db_session, get_graph_session_resolver
from kt_config.settings import get_settings
from kt_db.graph_sessions import GraphSessionResolver
from kt_db.keys import validate_schema_name
from kt_db.models import Graph, Node, User
from kt_db.repositories.graphs import GraphRepository
from kt_rbac import Permission
from kt_rbac.types import GraphRole

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/graphs", tags=["graphs"])

# No hyphens — prevents schema name collisions (my_graph vs my-graph both → graph_my_graph)
_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_]{1,98}[a-z0-9]$")

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
    database_connection_name: str | None = None
    status: str
    created_by: str | None = None
    created_at: datetime
    updated_at: datetime
    member_count: int = 0
    node_count: int = 0


class DatabaseConnectionResponse(BaseModel):
    """A database the user can pick when creating a graph.

    The synthetic ``default`` entry has ``id=None`` and ``created_at=None``;
    every other entry comes from the ``database_connections`` table whose
    ``config_key`` must exist in ``Settings.graph_databases`` (auto-discovered
    from ``EXTRA_DB_*`` env vars or set explicitly via YAML).
    """

    id: str | None = None
    name: str
    config_key: str
    created_at: datetime | None = None


class CreateGraphRequest(BaseModel):
    slug: str = Field(..., min_length=3, max_length=100, pattern=r"^[a-z0-9][a-z0-9_]{1,98}[a-z0-9]$")
    name: str = Field(..., min_length=1, max_length=200)
    description: str | None = None
    graph_type: str = Field(default="v1", pattern="^v[0-9]+$")
    byok_enabled: bool = False
    # "default" or omitted = system database; otherwise a config_key from
    # /database-connections (an external DB provisioned in the infra layer).
    # Schema is always the isolation strategy — the only choice is which DB
    # the schema lives in. To get a graph isolated to its own DB, just don't
    # create another graph against the same connection.
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
    role: GraphRole = GraphRole.reader


class UpdateMemberRoleRequest(BaseModel):
    role: GraphRole


# -- Helpers ----------------------------------------------------------------


def _graph_response(
    graph: Graph,
    member_count: int = 0,
    node_count: int = 0,
    database_connection_name: str | None = None,
) -> GraphResponse:
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
        database_connection_name=database_connection_name,
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
    permission: Permission = Permission.GRAPH_READ,
) -> Graph:
    """Load graph ORM object and verify access using kt-rbac.

    This helper exists because graph management endpoints (member CRUD, metadata
    update) need the ORM Graph object and use ``{slug}`` as a path param — not
    ``{graph_slug}`` which ``get_graph_context`` expects. The permission logic
    delegates to the same ``PermissionChecker`` used by ``require_graph_permission``.

    For graph-scoped data endpoints, prefer ``require_graph_permission()`` instead.
    """
    from kt_rbac import PermissionDeniedError, default_checker
    from kt_rbac.context import PermissionContext

    repo = GraphRepository(session)
    graph = await repo.get_by_slug(slug)
    if graph is None or graph.status == "deleted":
        raise HTTPException(status_code=404, detail="Graph not found")

    graph_role: GraphRole | None = None
    if not graph.is_default and not user.is_superuser:
        raw_role = await repo.get_member_role(graph.id, user.id)
        if raw_role is None:
            raise HTTPException(status_code=403, detail="Not a member of this graph")
        graph_role = GraphRole(raw_role)
    elif not graph.is_default:
        raw_role = await repo.get_member_role(graph.id, user.id)
        graph_role = GraphRole(raw_role) if raw_role else None

    ctx = PermissionContext(
        user_id=user.id,
        is_superuser=user.is_superuser,
        graph_role=graph_role,
        is_default_graph=graph.is_default,
    )
    try:
        default_checker.check_or_raise(ctx, permission)
    except PermissionDeniedError:
        raise HTTPException(status_code=403, detail=f"Requires permission: {permission.value}")

    return graph


# -- Graph CRUD -------------------------------------------------------------


@router.get("", response_model=list[GraphResponse])
async def list_graphs(
    user: User = Depends(require_auth),
    session: AsyncSession = Depends(get_db_session),
) -> list[GraphResponse]:
    """List graphs accessible to the current user."""
    from kt_db.models import DatabaseConnection, GraphMember

    repo = GraphRepository(session)
    graphs = await repo.list_accessible(user.id, user.is_superuser)

    # Batch-fetch database connection names
    db_conn_ids = {g.database_connection_id for g in graphs if g.database_connection_id}
    db_conn_names: dict[str, str] = {}
    if db_conn_ids:
        conn_stmt = select(DatabaseConnection.id, DatabaseConnection.name).where(DatabaseConnection.id.in_(db_conn_ids))
        conn_result = await session.execute(conn_stmt)
        db_conn_names = {str(row[0]): row[1] for row in conn_result.all()}

    # Batch-fetch member counts in a single query
    graph_ids = [g.id for g in graphs]
    member_counts: dict[str, int] = {}
    if graph_ids:
        count_stmt = (
            select(GraphMember.graph_id, func.count(GraphMember.id))
            .where(GraphMember.graph_id.in_(graph_ids))
            .group_by(GraphMember.graph_id)
        )
        count_result = await session.execute(count_stmt)
        member_counts = {str(row[0]): row[1] for row in count_result.all()}

    # Batch-fetch node counts for active graphs concurrently (capped to
    # avoid opening too many DB connections when many graphs exist).
    _NODE_COUNT_CONCURRENCY = 5
    node_counts: dict[str, int] = {}
    resolver = get_graph_session_resolver()
    active_graphs = [g for g in graphs if g.status == "active"]
    sem = asyncio.Semaphore(_NODE_COUNT_CONCURRENCY)

    async def _count_nodes(g: Graph) -> tuple[str, int]:
        async with sem:
            gs = await resolver.resolve(g.id)
            async with gs.graph_session_factory() as graph_session:
                result = await graph_session.execute(select(func.count(Node.id)))
                return str(g.id), result.scalar_one() or 0

    if active_graphs:
        results = await asyncio.gather(
            *[_count_nodes(g) for g in active_graphs],
            return_exceptions=True,
        )
        for i, r in enumerate(results):
            if isinstance(r, Exception):
                logger.debug("Failed to count nodes for graph %s", active_graphs[i].slug, exc_info=True)
            else:
                node_counts[r[0]] = r[1]

    return [
        _graph_response(
            g,
            member_count=member_counts.get(str(g.id), 0),
            node_count=node_counts.get(str(g.id), 0),
            database_connection_name=db_conn_names.get(str(g.database_connection_id), None)
            if g.database_connection_id
            else None,
        )
        for g in graphs
    ]


@router.post("", response_model=GraphResponse, status_code=201)
async def create_graph(
    body: CreateGraphRequest,
    admin: User = Depends(require_system_permission(Permission.SYSTEM_MANAGE_GRAPHS)),
    session: AsyncSession = Depends(get_db_session),
    resolver: GraphSessionResolver = Depends(get_graph_session_resolver),
) -> GraphResponse:
    """Create a new graph (admin only). Provisions schema synchronously."""
    if not _SLUG_RE.match(body.slug):
        raise HTTPException(
            status_code=400,
            detail="Slug must be 3-100 chars, lowercase alphanumeric + underscores only, "
            "starting and ending with alphanumeric (no hyphens)",
        )

    if body.slug == "default":
        raise HTTPException(status_code=400, detail="Cannot create graph with reserved slug 'default'")

    repo = GraphRepository(session)
    existing = await repo.get_by_slug(body.slug)
    if existing:
        raise HTTPException(status_code=409, detail=f"Graph with slug '{body.slug}' already exists")

    # Resolve the chosen database. ``None`` or ``"default"`` → system DB.
    # Any other key → an external DB row in ``database_connections``. Schema
    # is always the isolation strategy; the only choice is which DB the
    # schema lives in.
    db_conn_id: uuid.UUID | None = None
    if body.database_connection_config_key and body.database_connection_config_key != "default":
        db_conn = await repo.get_database_connection_by_key(body.database_connection_config_key)
        if db_conn is None:
            raise HTTPException(
                status_code=400,
                detail=f"Database connection '{body.database_connection_config_key}' not found",
            )
        db_conn_id = db_conn.id

    schema_name = f"graph_{body.slug}"

    graph = await repo.create(
        slug=body.slug,
        name=body.name,
        description=body.description,
        graph_type=body.graph_type,
        byok_enabled=body.byok_enabled,
        # storage_mode is now legacy: every non-default graph is schema-mode.
        # Kept on the column for backward-compat with older rows.
        storage_mode="schema",
        schema_name=schema_name,
        database_connection_id=db_conn_id,
        created_by=admin.id,
        status="provisioning",
    )
    await session.commit()

    # Provision schema + Qdrant collections synchronously
    try:
        await _provision_graph(graph, session, resolver)
        # Combine status update + admin member in a single commit
        # to avoid orphaned graphs with no admin on crash
        graph = await repo.update(graph, status="active")
        await repo.add_member(graph.id, admin.id, role=GraphRole.admin)
        await session.commit()
        # Invalidate cached GraphInfo so next resolve() sees status="active"
        await resolver.invalidate(graph.id)
    except Exception:
        logger.exception("Failed to provision graph '%s'", body.slug)
        graph = await repo.update(graph, status="error")
        await session.commit()
        await resolver.invalidate(graph.id)
        raise HTTPException(status_code=500, detail="Graph provisioning failed")

    return _graph_response(graph, member_count=1)


# -- Database connections ---------------------------------------------------


@router.get("/database-connections", response_model=list[DatabaseConnectionResponse])
async def list_database_connections(
    _admin: User = Depends(require_system_permission(Permission.SYSTEM_MANAGE_GRAPHS)),
    session: AsyncSession = Depends(get_db_session),
) -> list[DatabaseConnectionResponse]:
    """List databases an admin can pick when creating a graph.

    The synthetic ``default`` entry (system DB) is always returned first,
    followed by every row from ``database_connections``.
    """
    repo = GraphRepository(session)
    connections = await repo.list_database_connections()
    out: list[DatabaseConnectionResponse] = [
        DatabaseConnectionResponse(id=None, name="default", config_key="default", created_at=None)
    ]
    out.extend(
        DatabaseConnectionResponse(
            id=str(c.id),
            name=c.name,
            config_key=c.config_key,
            created_at=c.created_at,
        )
        for c in connections
    )
    return out


# -- Graph detail -----------------------------------------------------------


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

    # Resolve database connection name
    db_conn_name: str | None = None
    if graph.database_connection_id:
        from kt_db.models import DatabaseConnection

        conn_result = await session.execute(
            select(DatabaseConnection.name).where(DatabaseConnection.id == graph.database_connection_id)
        )
        db_conn_name = conn_result.scalar_one_or_none()

    return _graph_response(
        graph,
        member_count=len(members),
        node_count=node_count,
        database_connection_name=db_conn_name,
    )


@router.put("/{slug}", response_model=GraphResponse)
async def update_graph(
    slug: str,
    body: UpdateGraphRequest,
    user: User = Depends(require_auth),
    session: AsyncSession = Depends(get_db_session),
) -> GraphResponse:
    """Update graph name/description. Requires admin role on the graph."""
    graph = await _require_graph_access(slug, user, session, permission=Permission.GRAPH_MANAGE_METADATA)
    repo = GraphRepository(session)
    graph = await repo.update(graph, name=body.name, description=body.description)
    await session.commit()
    members = await repo.get_members(graph.id)
    return _graph_response(graph, member_count=len(members))


@router.post("/{slug}/retry-provision", response_model=GraphResponse)
async def retry_provision(
    slug: str,
    admin: User = Depends(require_system_permission(Permission.SYSTEM_MANAGE_GRAPHS)),
    session: AsyncSession = Depends(get_db_session),
    resolver: GraphSessionResolver = Depends(get_graph_session_resolver),
) -> GraphResponse:
    """Retry provisioning for a graph stuck in 'error' status (admin only).

    CREATE SCHEMA IF NOT EXISTS and Alembic migrations are idempotent,
    so retrying is safe even if the schema/tables already exist.
    """
    repo = GraphRepository(session)
    graph = await repo.get_by_slug(slug)
    if graph is None:
        raise HTTPException(status_code=404, detail="Graph not found")
    if graph.status != "error":
        raise HTTPException(status_code=400, detail="Only graphs in 'error' status can be re-provisioned")

    try:
        await _provision_graph(graph, session, resolver)
        graph = await repo.update(graph, status="active")
        # Add admin member if none exist (handles the orphaned-graph case)
        members = await repo.get_members(graph.id)
        if not members:
            await repo.add_member(graph.id, admin.id, role=GraphRole.admin)
        await session.commit()
        await resolver.invalidate(graph.id)
    except Exception:
        logger.exception("Retry provisioning failed for graph '%s'", slug)
        graph = await repo.update(graph, status="error")
        await session.commit()
        await resolver.invalidate(graph.id)
        raise HTTPException(status_code=500, detail="Provisioning retry failed")

    members = await repo.get_members(graph.id)
    return _graph_response(graph, member_count=len(members))


@router.delete("/{slug}", status_code=204)
async def delete_graph(
    slug: str,
    _admin: User = Depends(require_system_permission(Permission.SYSTEM_MANAGE_GRAPHS)),
    session: AsyncSession = Depends(get_db_session),
    resolver: GraphSessionResolver = Depends(get_graph_session_resolver),
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
    # Evict from cache and dispose engine pools
    await resolver.invalidate(graph.id)


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
    graph = await _require_graph_access(slug, user, session, permission=Permission.GRAPH_MANAGE_MEMBERS)
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
    graph = await _require_graph_access(slug, user, session, permission=Permission.GRAPH_MANAGE_MEMBERS)
    repo = GraphRepository(session)

    try:
        target_id = uuid.UUID(user_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid user_id")

    # Lock admin members first, then check role — prevents TOCTOU race
    # where two concurrent requests both read admin_count=2 before either demotes.
    from kt_db.models import GraphMember as GraphMemberModel

    if body.role != GraphRole.admin:
        lock_stmt = (
            select(GraphMemberModel)
            .where(GraphMemberModel.graph_id == graph.id, GraphMemberModel.role == GraphRole.admin)
            .with_for_update()
        )
        result = await session.execute(lock_stmt)
        locked_admins = result.scalars().all()
        # Check if the target is an admin being demoted and is the last one
        target_is_admin = any(m.user_id == target_id for m in locked_admins)
        if target_is_admin and len(locked_admins) <= 1:
            raise HTTPException(status_code=400, detail="Cannot demote the last admin of a graph")

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
    graph = await _require_graph_access(slug, user, session, permission=Permission.GRAPH_MANAGE_MEMBERS)
    repo = GraphRepository(session)

    try:
        target_id = uuid.UUID(user_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid user_id")

    # Lock admin members, then check — prevents TOCTOU race on last-admin removal
    from kt_db.models import GraphMember as GraphMemberModel

    lock_stmt = (
        select(GraphMemberModel)
        .where(GraphMemberModel.graph_id == graph.id, GraphMemberModel.role == GraphRole.admin)
        .with_for_update()
    )
    result = await session.execute(lock_stmt)
    locked_admins = result.scalars().all()
    target_is_admin = any(m.user_id == target_id for m in locked_admins)
    if target_is_admin and len(locked_admins) <= 1:
        raise HTTPException(status_code=400, detail="Cannot remove the last admin of a graph")

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

    Schema is the only isolation strategy. The DATABASE the schema lives in
    is determined by ``graph.database_connection_id``: a NULL connection
    routes to the system DBs; a set connection routes to the external DB
    referenced by its ``config_key`` in ``Settings.graph_databases``.
    """
    schema = graph.schema_name
    if schema == "public":
        return  # Default graph, nothing to provision

    validate_schema_name(schema)

    # SECURITY: f-string in DDL is safe ONLY because validate_schema_name() above
    # enforces ^[a-z0-9_]+$ — if that regex is ever loosened, these become injectable.

    # ---- Resolve target URLs based on the chosen DatabaseConnection -------
    settings = get_settings()
    if graph.database_connection_id is not None:
        repo = GraphRepository(session)
        db_conn = await repo.get_database_connection(graph.database_connection_id)
        if db_conn is None:
            raise RuntimeError(
                f"Graph '{graph.slug}' references missing DatabaseConnection {graph.database_connection_id}"
            )
        cfg = settings.graph_databases.get(db_conn.config_key)
        if cfg is None:
            raise RuntimeError(
                f"Database connection '{db_conn.config_key}' is not configured in "
                f"settings.graph_databases — check that EXTRA_DB_"
                f"{db_conn.config_key.upper().replace('-', '_')}_* env vars "
                f"are set on the API pod"
            )
        graph_url = cfg.graph_database_url
        write_url = cfg.write_database_url
        qdrant_url = cfg.qdrant_url or settings.qdrant_url
    else:
        graph_url = settings.database_url
        write_url = settings.write_database_url
        qdrant_url = settings.qdrant_url

    # ---- Create schema in graph-db (one-off engine) -----------------------
    from sqlalchemy.ext.asyncio import create_async_engine

    g_eng = create_async_engine(graph_url, future=True)
    try:
        async with g_eng.begin() as conn:
            await conn.execute(text(f"CREATE SCHEMA IF NOT EXISTS {schema}"))
    finally:
        await g_eng.dispose()

    # ---- Create schema in write-db (one-off engine) -----------------------
    # write_url usually points at PgBouncer (transaction pool mode); a single
    # CREATE SCHEMA statement is fine through that.
    w_eng = create_async_engine(write_url, future=True)
    try:
        async with w_eng.begin() as conn:
            await conn.execute(text(f"CREATE SCHEMA IF NOT EXISTS {schema}"))
    finally:
        await w_eng.dispose()

    # ---- Run Alembic migrations against the chosen DBs --------------------
    # libs/kt-db/alembic{,_write}/env.py both call get_settings() at startup;
    # Pydantic Settings reads DATABASE_URL / WRITE_DATABASE_URL from env, so
    # the override below propagates into the subprocess via env={...}.
    import os
    import subprocess
    import sys

    import kt_db

    kt_db_root = Path(kt_db.__file__).resolve().parents[2]  # kt_db/__init__.py -> src/kt_db -> kt-db/
    env = {
        **os.environ,
        "ALEMBIC_SCHEMA": schema,
        "DATABASE_URL": graph_url,
        "WRITE_DATABASE_URL": write_url,
    }

    def _run_migrations() -> None:
        for ini_file in ("alembic.ini", "alembic_write.ini"):
            result = subprocess.run(
                [sys.executable, "-m", "alembic", "-c", str(kt_db_root / ini_file), "upgrade", "head"],
                env=env,
                capture_output=True,
                text=True,
                cwd=str(kt_db_root),
            )
            if result.returncode != 0:
                logger.error("Migration failed for graph '%s': %s", graph.slug, result.stderr)
                raise RuntimeError(f"Migration failed for graph '{graph.slug}'")

    await asyncio.to_thread(_run_migrations)

    # ---- Create Qdrant collections ----------------------------------------
    # Use the per-graph Qdrant if it differs from the system one. The system
    # singleton client is reused for the default-DB code path so we don't
    # spawn a fresh client per provisioning.
    from kt_qdrant.client import get_qdrant_client, make_qdrant_client
    from kt_qdrant.repositories.facts import QdrantFactRepository
    from kt_qdrant.repositories.nodes import QdrantNodeRepository
    from kt_qdrant.repositories.seeds import QdrantSeedRepository

    use_per_graph_qdrant = bool(qdrant_url) and qdrant_url != settings.qdrant_url
    client = make_qdrant_client(qdrant_url) if use_per_graph_qdrant else get_qdrant_client()
    prefix = f"{graph.slug}__"

    try:
        await QdrantFactRepository(client, f"{prefix}facts").ensure_collection()
        await QdrantNodeRepository(client, f"{prefix}nodes").ensure_collection()
        await QdrantSeedRepository(client, f"{prefix}seeds").ensure_collection()
    finally:
        if use_per_graph_qdrant:
            await client.close()
