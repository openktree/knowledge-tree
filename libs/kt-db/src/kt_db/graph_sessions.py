"""Graph-aware session factory resolver.

Produces and caches per-graph session factories. Each graph gets its own
engine pool scoped to the correct database + PostgreSQL schema.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from kt_config.settings import Settings, get_settings
from kt_db.models import DatabaseConnection, Graph

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class GraphSessions:
    """Session factories and metadata for a single graph."""

    graph: Graph
    graph_session_factory: async_sessionmaker[AsyncSession]
    write_session_factory: async_sessionmaker[AsyncSession]
    qdrant_collection_prefix: str  # "" for default, "{slug}__" for non-default


class GraphSessionResolver:
    """Resolves graph ID/slug to per-graph session factories.

    Caches engine pools per graph_id. For ``storage_mode="schema"``, sets
    ``search_path`` via ``server_settings`` so all queries target the
    graph's schema. For ``storage_mode="database"``, creates engines
    pointing at the configured database URLs.
    """

    def __init__(
        self,
        control_session_factory: async_sessionmaker[AsyncSession],
        settings: Settings | None = None,
    ) -> None:
        self._control_sf = control_session_factory
        self._settings = settings or get_settings()
        self._cache: dict[uuid.UUID, GraphSessions] = {}

    async def resolve(self, graph_id: uuid.UUID) -> GraphSessions:
        """Return cached GraphSessions for a graph, creating engines if needed."""
        if graph_id in self._cache:
            return self._cache[graph_id]

        async with self._control_sf() as session:
            graph = await session.execute(select(Graph).where(Graph.id == graph_id))
            graph_row = graph.scalar_one_or_none()
            if graph_row is None:
                raise ValueError(f"Graph {graph_id} not found")
            return await self._build_and_cache(graph_row, session)

    async def resolve_by_slug(self, slug: str) -> GraphSessions:
        """Resolve by slug, with caching."""
        # Check cache first (linear scan — small N)
        for gs in self._cache.values():
            if gs.graph.slug == slug:
                return gs

        async with self._control_sf() as session:
            result = await session.execute(select(Graph).where(Graph.slug == slug))
            graph_row = result.scalar_one_or_none()
            if graph_row is None:
                raise ValueError(f"Graph with slug '{slug}' not found")
            return await self._build_and_cache(graph_row, session)

    async def list_active_graphs(self) -> list[Graph]:
        """Return all active graphs from the control plane."""
        async with self._control_sf() as session:
            result = await session.execute(select(Graph).where(Graph.status == "active"))
            return list(result.scalars().all())

    def invalidate(self, graph_id: uuid.UUID) -> None:
        """Remove a graph from the cache (e.g. after deletion)."""
        self._cache.pop(graph_id, None)

    async def _build_and_cache(self, graph: Graph, session: AsyncSession) -> GraphSessions:
        """Build engines for a graph and cache the result."""
        if graph.id in self._cache:
            return self._cache[graph.id]

        settings = self._settings

        if graph.is_default:
            # Default graph uses the system-level engines
            graph_sf = _make_session_factory(
                settings.database_url,
                pool_size=settings.db_pool_size,
                max_overflow=settings.db_max_overflow,
                pool_timeout=settings.db_pool_timeout,
                pool_recycle=settings.db_pool_recycle,
                application_name="kt-graph-default",
            )
            write_sf = _make_session_factory(
                settings.write_database_url,
                pool_size=settings.write_db_pool_size,
                max_overflow=settings.write_db_max_overflow,
                pool_timeout=settings.write_db_pool_timeout,
                pool_recycle=settings.write_db_pool_recycle,
                application_name="kt-write-default",
            )
            prefix = ""
        elif graph.storage_mode == "database":
            # Different database — load connection config
            db_conn = await self._resolve_db_connection(graph, session)
            graph_db_config = settings.graph_databases.get(db_conn.config_key)
            if graph_db_config is None:
                raise ValueError(
                    f"Graph '{graph.slug}' references config_key '{db_conn.config_key}' "
                    f"not found in settings.graph_databases"
                )
            graph_sf = _make_session_factory(
                graph_db_config.graph_database_url,
                pool_size=graph_db_config.pool_size,
                max_overflow=graph_db_config.max_overflow,
                schema_name=graph.schema_name,
                application_name=f"kt-graph-{graph.slug}",
            )
            write_sf = _make_session_factory(
                graph_db_config.write_database_url,
                pool_size=graph_db_config.pool_size,
                max_overflow=graph_db_config.max_overflow,
                schema_name=graph.schema_name,
                application_name=f"kt-write-{graph.slug}",
            )
            prefix = f"{graph.slug}__"
        else:
            # Same database, different schema
            graph_sf = _make_session_factory(
                settings.database_url,
                pool_size=5,
                max_overflow=10,
                schema_name=graph.schema_name,
                application_name=f"kt-graph-{graph.slug}",
            )
            write_sf = _make_session_factory(
                settings.write_database_url,
                pool_size=5,
                max_overflow=10,
                schema_name=graph.schema_name,
                application_name=f"kt-write-{graph.slug}",
            )
            prefix = f"{graph.slug}__"

        gs = GraphSessions(
            graph=graph,
            graph_session_factory=graph_sf,
            write_session_factory=write_sf,
            qdrant_collection_prefix=prefix,
        )
        self._cache[graph.id] = gs
        logger.info(
            "Cached session factories for graph '%s' (mode=%s, schema=%s)",
            graph.slug,
            graph.storage_mode,
            graph.schema_name,
        )
        return gs

    async def _resolve_db_connection(self, graph: Graph, session: AsyncSession) -> DatabaseConnection:
        """Load the DatabaseConnection row for a database-mode graph."""
        if graph.database_connection_id is None:
            raise ValueError(f"Graph '{graph.slug}' has storage_mode='database' but no database_connection_id")
        result = await session.execute(
            select(DatabaseConnection).where(DatabaseConnection.id == graph.database_connection_id)
        )
        db_conn = result.scalar_one_or_none()
        if db_conn is None:
            raise ValueError(f"DatabaseConnection {graph.database_connection_id} not found")
        return db_conn


def _make_session_factory(
    database_url: str,
    *,
    pool_size: int = 5,
    max_overflow: int = 10,
    pool_timeout: int = 30,
    pool_recycle: int = 1800,
    schema_name: str | None = None,
    application_name: str = "kt",
) -> async_sessionmaker[AsyncSession]:
    """Create an engine + session factory, optionally scoped to a schema."""
    server_settings: dict[str, str] = {"application_name": application_name}
    if schema_name and schema_name != "public":
        # Set search_path so all unqualified table references resolve to this schema,
        # falling back to public for shared tables (user, system_settings, etc.)
        server_settings["search_path"] = f"{schema_name},public"

    engine = create_async_engine(
        database_url,
        echo=False,
        pool_size=pool_size,
        max_overflow=max_overflow,
        pool_timeout=pool_timeout,
        pool_pre_ping=True,
        pool_recycle=pool_recycle,
        connect_args={
            "statement_cache_size": 0,
            "server_settings": server_settings,
        },
    )
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
