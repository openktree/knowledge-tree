"""Graph-aware session factory resolver.

Produces and caches per-graph session factories. Each graph gets its own
engine pool scoped to the correct database + PostgreSQL schema.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass

from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from kt_config.settings import Settings, get_settings
from kt_db.keys import validate_schema_name
from kt_db.models import DatabaseConnection, Graph

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class GraphInfo:
    """Detached snapshot of a Graph row — safe to cache after session closes."""

    id: uuid.UUID
    slug: str
    name: str
    schema_name: str
    storage_mode: str
    is_default: bool
    database_connection_id: uuid.UUID | None
    status: str
    # Multigraph public-cache toggles (PR3). Snapshotted at resolve() time and
    # cached for the lifetime of the GraphSessions row — the API
    # invalidates the resolver cache after a Graph PATCH so toggle flips
    # take effect on the next resolution rather than mid-workflow.
    contribute_to_public: bool = True
    use_public_cache: bool = True

    @staticmethod
    def from_orm(graph: Graph) -> GraphInfo:
        return GraphInfo(
            id=graph.id,
            slug=graph.slug,
            name=graph.name,
            schema_name=graph.schema_name,
            storage_mode=graph.storage_mode,
            is_default=graph.is_default,
            database_connection_id=graph.database_connection_id,
            status=graph.status,
            contribute_to_public=graph.contribute_to_public,
            use_public_cache=graph.use_public_cache,
        )


@dataclass(frozen=True)
class GraphSessions:
    """Session factories and metadata for a single graph."""

    graph: GraphInfo
    graph_session_factory: async_sessionmaker[AsyncSession]
    write_session_factory: async_sessionmaker[AsyncSession]
    qdrant_collection_prefix: str  # "" for default, "{slug}__" for non-default
    qdrant_url: str = ""  # per-graph Qdrant URL; empty = use global settings.qdrant_url
    # Engines stored for proper disposal — None for default graph (reused system pools)
    _graph_engine: AsyncEngine | None = None
    _write_engine: AsyncEngine | None = None


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
        *,
        default_graph_session_factory: async_sessionmaker[AsyncSession] | None = None,
        default_write_session_factory: async_sessionmaker[AsyncSession] | None = None,
    ) -> None:
        self._control_sf = control_session_factory
        self._settings = settings or get_settings()
        self._cache: dict[uuid.UUID, GraphSessions] = {}
        self._slug_to_id: dict[str, uuid.UUID] = {}  # O(1) slug lookup
        self._locks: dict[uuid.UUID, asyncio.Lock] = {}
        self._meta_lock = asyncio.Lock()  # protects _locks dict creation only
        # Reuse existing system-level session factories for the default graph
        # to avoid creating duplicate connection pools
        self._default_graph_sf = default_graph_session_factory
        self._default_write_sf = default_write_session_factory

    async def _get_lock(self, graph_id: uuid.UUID) -> asyncio.Lock:
        """Get or create a per-graph lock (meta_lock only protects dict insertion)."""
        if graph_id in self._locks:
            return self._locks[graph_id]
        async with self._meta_lock:
            if graph_id not in self._locks:
                self._locks[graph_id] = asyncio.Lock()
            return self._locks[graph_id]

    async def resolve(self, graph_id: uuid.UUID) -> GraphSessions:
        """Return cached GraphSessions for a graph, creating engines if needed."""
        if graph_id in self._cache:
            return self._cache[graph_id]

        lock = await self._get_lock(graph_id)
        async with lock:
            if graph_id in self._cache:
                return self._cache[graph_id]

            async with self._control_sf() as session:
                graph = await session.execute(select(Graph).where(Graph.id == graph_id))
                graph_row = graph.scalar_one_or_none()
                if graph_row is None:
                    raise ValueError(f"Graph {graph_id} not found")
                return await self._build_and_cache(graph_row, session)

    async def resolve_by_slug(self, slug: str) -> GraphSessions:
        """Resolve by slug, with caching. O(1) via slug index."""
        graph_id = self._slug_to_id.get(slug)
        if graph_id is not None and graph_id in self._cache:
            return self._cache[graph_id]

        # Look up graph ID, then release session before acquiring lock
        # to avoid holding a connection pool slot while waiting on the lock
        async with self._control_sf() as session:
            result = await session.execute(select(Graph).where(Graph.slug == slug))
            graph_row = result.scalar_one_or_none()
            if graph_row is None:
                raise ValueError(f"Graph with slug '{slug}' not found")
            graph_id = graph_row.id

        lock = await self._get_lock(graph_id)
        async with lock:
            if graph_id in self._cache:
                return self._cache[graph_id]
            # Open a fresh session inside the lock for _build_and_cache
            async with self._control_sf() as session:
                graph_row = (await session.execute(select(Graph).where(Graph.id == graph_id))).scalar_one_or_none()
                if graph_row is None:
                    raise ValueError(f"Graph {graph_id} was deleted during resolution")
                return await self._build_and_cache(graph_row, session)

    async def list_active_graphs(self) -> list[GraphInfo]:
        """Return all active graphs from the control plane."""
        async with self._control_sf() as session:
            result = await session.execute(select(Graph).where(Graph.status == "active"))
            return [GraphInfo.from_orm(g) for g in result.scalars().all()]

    async def invalidate(self, graph_id: uuid.UUID) -> None:
        """Remove a graph from the cache and dispose its engine pools."""
        gs = self._cache.pop(graph_id, None)
        if gs is not None:
            self._slug_to_id.pop(gs.graph.slug, None)
        if gs is not None and not gs.graph.is_default:
            for engine in (gs._graph_engine, gs._write_engine):
                if engine is not None:
                    try:
                        await engine.dispose()
                    except Exception:
                        pass

    async def _build_and_cache(self, graph: Graph, session: AsyncSession) -> GraphSessions:
        """Build engines for a graph and cache the result."""
        if graph.id in self._cache:
            return self._cache[graph.id]

        settings = self._settings
        info = GraphInfo.from_orm(graph)

        graph_engine: AsyncEngine | None = None
        write_engine: AsyncEngine | None = None

        _qdrant_url = ""

        if graph.is_default:
            # Reuse existing system-level session factories to avoid duplicate pools
            if self._default_graph_sf and self._default_write_sf:
                graph_sf = self._default_graph_sf
                write_sf = self._default_write_sf
            else:
                graph_engine, graph_sf = _make_session_factory(
                    settings.database_url,
                    pool_size=settings.db_pool_size,
                    max_overflow=settings.db_max_overflow,
                    pool_timeout=settings.db_pool_timeout,
                    pool_recycle=settings.db_pool_recycle,
                    application_name="kt-graph-default",
                )
                write_engine, write_sf = _make_session_factory(
                    settings.write_database_url,
                    pool_size=settings.write_db_pool_size,
                    max_overflow=settings.write_db_max_overflow,
                    pool_timeout=settings.write_db_pool_timeout,
                    pool_recycle=settings.write_db_pool_recycle,
                    application_name="kt-write-default",
                )
            prefix = ""
        else:
            # Schema strategy is the only strategy: every non-default graph
            # gets its own schema. The DATABASE the schema lives in is
            # determined by ``database_connection_id``: NULL → system DBs,
            # otherwise → external DBs from settings.graph_databases.
            if graph.database_connection_id is not None:
                db_conn = await self._resolve_db_connection(graph, session)
                graph_db_config = settings.graph_databases.get(db_conn.config_key)
                if graph_db_config is None:
                    raise ValueError(
                        f"Graph '{graph.slug}' references config_key '{db_conn.config_key}' "
                        f"not found in settings.graph_databases"
                    )
                graph_url = graph_db_config.graph_database_url
                write_url = graph_db_config.write_database_url
                graph_pool_size = graph_db_config.graph_pool_size
                graph_max_overflow = graph_db_config.graph_max_overflow
                graph_pool_timeout = graph_db_config.graph_pool_timeout
                graph_pool_recycle = graph_db_config.graph_pool_recycle
                write_pool_size = graph_db_config.write_pool_size
                write_max_overflow = graph_db_config.write_max_overflow
                write_pool_timeout = graph_db_config.write_pool_timeout
                write_pool_recycle = graph_db_config.write_pool_recycle
                if graph_db_config.qdrant_url:
                    _qdrant_url = graph_db_config.qdrant_url
            else:
                graph_url = settings.database_url
                write_url = settings.write_database_url
                graph_pool_size = settings.multigraph_db_pool_size
                graph_max_overflow = settings.multigraph_db_max_overflow
                graph_pool_timeout = settings.multigraph_db_pool_timeout
                graph_pool_recycle = settings.multigraph_db_pool_recycle
                write_pool_size = settings.multigraph_write_db_pool_size
                write_max_overflow = settings.multigraph_write_db_max_overflow
                write_pool_timeout = settings.multigraph_write_db_pool_timeout
                write_pool_recycle = settings.multigraph_write_db_pool_recycle

            graph_engine, graph_sf = _make_session_factory(
                graph_url,
                pool_size=graph_pool_size,
                max_overflow=graph_max_overflow,
                pool_timeout=graph_pool_timeout,
                pool_recycle=graph_pool_recycle,
                schema_name=graph.schema_name,
                application_name=f"kt-graph-{graph.slug}",
            )
            write_engine, write_sf = _make_session_factory(
                write_url,
                pool_size=write_pool_size,
                max_overflow=write_max_overflow,
                pool_timeout=write_pool_timeout,
                pool_recycle=write_pool_recycle,
                schema_name=graph.schema_name,
                application_name=f"kt-write-{graph.slug}",
            )
            prefix = f"{graph.slug}__"

        gs = GraphSessions(
            graph=info,
            graph_session_factory=graph_sf,
            write_session_factory=write_sf,
            qdrant_collection_prefix=prefix,
            qdrant_url=_qdrant_url,
            _graph_engine=graph_engine,
            _write_engine=write_engine,
        )
        self._cache[graph.id] = gs
        self._slug_to_id[graph.slug] = graph.id
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
) -> tuple[AsyncEngine, async_sessionmaker[AsyncSession]]:
    """Create an engine + session factory, optionally scoped to a schema.

    Returns (engine, session_factory) so callers can store the engine for disposal.
    """
    # ``application_name`` is in PgBouncer's default startup-parameter
    # whitelist so it is safe to send via asyncpg ``server_settings``.
    # ``search_path`` is NOT — PgBouncer (transaction mode) rejects it as a
    # startup parameter with ProtocolViolationError. Adding it to PgBouncer's
    # ``ignore_startup_parameters`` would silently DROP the value (asyncpg
    # has no compensation logic for search_path the way it does for
    # extra_float_digits), so every query would land in ``public`` —
    # exchanging a loud bug for silent data corruption.
    #
    # Instead we set search_path via a SQLAlchemy ``begin`` event listener
    # using ``SET LOCAL``. SET LOCAL is transaction-scoped, which is exactly
    # what PgBouncer transaction pooling supports: each transaction gets its
    # own backing PG connection and PgBouncer runs ``server_reset_query``
    # (DISCARD ALL) between transactions, so any session-scoped SET would
    # not survive. SET LOCAL re-applies on every BEGIN, regardless of which
    # backing connection PgBouncer hands out.
    server_settings: dict[str, str] = {"application_name": application_name}
    schema_search_path: str | None = None
    if schema_name and schema_name != "public":
        validate_schema_name(schema_name)
        schema_search_path = f"{schema_name},public"

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

    if schema_search_path is not None:
        # Capture by closure; search_path is validated by validate_schema_name above.
        _set_local_sql = f"SET LOCAL search_path TO {schema_search_path}"

        @event.listens_for(engine.sync_engine, "begin")
        def _set_local_search_path(conn):  # type: ignore[no-untyped-def]
            conn.exec_driver_sql(_set_local_sql)

    sf = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    return engine, sf
