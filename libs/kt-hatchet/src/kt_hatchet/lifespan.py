"""Worker lifespan -- shared state across all Hatchet tasks on a worker process.

Yields a ``WorkerState`` that each task receives via ``ctx.lifespan``.
This replaces the per-message service construction in the old stream
workers, amortising connection pool and provider setup across all tasks.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import AsyncGenerator
from dataclasses import dataclass
from typing import TYPE_CHECKING

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from kt_config.settings import Settings
from kt_db.graph_sessions import GraphSessionResolver
from kt_db.session import get_engine, get_write_engine
from kt_flags.hatchet import init_worker as _init_flag_client

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from qdrant_client import AsyncQdrantClient

    from kt_graph.worker_engine import WorkerGraphEngine
    from kt_models.embeddings import EmbeddingService
    from kt_models.gateway import ModelGateway
    from kt_providers.fetch import FetchProviderRegistry
    from kt_providers.registry import ProviderRegistry


@dataclass
class WorkerState:
    """Shared services available to every Hatchet task via ``ctx.lifespan``."""

    session_factory: async_sessionmaker[AsyncSession]
    write_session_factory: async_sessionmaker[AsyncSession]
    settings: Settings

    # Lazy-imported services -- set during lifespan setup
    model_gateway: ModelGateway
    embedding_service: EmbeddingService
    provider_registry: ProviderRegistry
    fetch_registry: FetchProviderRegistry | None
    qdrant_client: AsyncQdrantClient | None = None

    # Multi-graph session resolver (resolves graph_id to per-graph session factories)
    graph_resolver: GraphSessionResolver | None = None

    # ID of the default ("public") graph, resolved once at worker startup.
    # Used by the multigraph public-cache bridge to detect self-reference
    # (the default graph never participates as either source or target).
    default_graph_id: uuid.UUID | None = None

    async def resolve_sessions(
        self, graph_id: str | None = None
    ) -> tuple["async_sessionmaker[AsyncSession]", "async_sessionmaker[AsyncSession]"]:
        """Resolve session factories for a graph_id.

        Returns (graph_session_factory, write_session_factory).
        When graph_id is None or "default", returns the system-level factories.
        """
        if not graph_id or graph_id == "default" or self.graph_resolver is None:
            return self.session_factory, self.write_session_factory

        gs = await self.graph_resolver.resolve(uuid.UUID(graph_id))
        return gs.graph_session_factory, gs.write_session_factory

    def make_worker_engine(
        self,
        write_session: AsyncSession,
        *,
        graph_id: uuid.UUID | None,
        qdrant_collection_prefix: str = "",
        embedding_service: "EmbeddingService | None" = None,
    ) -> WorkerGraphEngine:
        """Build a ``WorkerGraphEngine`` wired with the public-cache bridge.

        ``graph_id`` is the *current* graph the workflow is operating on.
        When it is the default graph (or None / no resolver / no default
        registered), the bridge is set to ``None`` so the engine's
        pass-through methods all no-op — that is the universal "skip"
        signal that workflows rely on. The factory is the single place
        any worker plumbs the bridge through; per-workflow code never
        sees it.
        """
        # Lazy import to avoid pulling kt-graph at module load time —
        # kt-hatchet has a thinner runtime footprint than kt-graph.
        from kt_graph.public_bridge import PublicGraphBridge
        from kt_graph.worker_engine import WorkerGraphEngine

        bridge: PublicGraphBridge | None = None
        if (
            self.graph_resolver is not None
            and self.default_graph_id is not None
            and graph_id is not None
            and graph_id != self.default_graph_id
        ):
            # Defence in depth: a non-default graph that's missing its
            # Qdrant prefix would silently dedup against the *default*
            # graph's collection — exactly the cross-contamination this
            # whole subsystem exists to prevent. Fail loud at construction
            # time rather than discovering it in production.
            if not qdrant_collection_prefix:
                raise ValueError(
                    f"make_worker_engine: graph_id={graph_id} is non-default but "
                    "qdrant_collection_prefix is empty — refusing to wire the bridge"
                )
            bridge = PublicGraphBridge(
                resolver=self.graph_resolver,
                qdrant_client=self.qdrant_client,
                embedding_service=self.embedding_service,
                default_graph_id=self.default_graph_id,
                settings=self.settings,
            )

        return WorkerGraphEngine(
            write_session,
            embedding_service=embedding_service or self.embedding_service,
            qdrant_client=self.qdrant_client,
            public_bridge=bridge,
            qdrant_collection_prefix=qdrant_collection_prefix,
        )


async def _resolve_default_graph_id(
    session_factory: async_sessionmaker[AsyncSession],
) -> uuid.UUID | None:
    """Look up the public/default graph row's id once at worker startup.

    Drives the public-cache bridge — when a workflow's current ``graph_id``
    matches this, the bridge is omitted entirely. Failures are swallowed so
    a misconfigured environment still boots; cache features just silently
    disable themselves until the row appears.
    """
    try:
        from sqlalchemy import select as _sa_select

        from kt_db.models import Graph as _GraphModel

        async with session_factory() as ctrl:
            row = (
                await ctrl.execute(_sa_select(_GraphModel.id).where(_GraphModel.is_default.is_(True)).limit(1))
            ).scalar_one_or_none()
            return row
    except Exception:
        logger.warning("worker lifespan: failed to resolve default graph id", exc_info=True)
        return None


async def worker_lifespan() -> AsyncGenerator[WorkerState, None]:
    """Async context manager that Hatchet calls at worker start/stop."""
    settings = Settings()
    _init_flag_client()

    engine = get_engine(application_name="kt-worker")
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    write_engine = get_write_engine(application_name="kt-worker")
    write_session_factory = async_sessionmaker(write_engine, class_=AsyncSession, expire_on_commit=False)

    # Lazy imports to avoid circular dependencies
    from kt_models.embeddings import EmbeddingService
    from kt_models.gateway import ModelGateway
    from kt_providers.registry import ProviderRegistry

    model_gateway = ModelGateway()
    embedding_service = EmbeddingService()

    provider_registry = ProviderRegistry()
    default_provider = settings.default_search_provider
    # Extra providers registered by services (typically bridged from plugins).
    # kt-hatchet stays plugin-agnostic — it only sees generic factories.
    from kt_providers.registry import iter_extra_provider_factories

    for extra in iter_extra_provider_factories():
        if default_provider not in (extra.provider_id, "all"):
            continue
        try:
            if not extra.is_available():
                continue
            provider_registry.register(extra.factory())
            logger.info("Registered extra search provider: %s", extra.name)
        except Exception:
            logger.exception("Failed to register extra search provider: %s", extra.name)

    from kt_providers.fetch import maybe_build_fetch_registry

    fetch_registry = maybe_build_fetch_registry(settings)

    # Qdrant vector search client (required for all vector search)
    from kt_qdrant.client import get_qdrant_client
    from kt_qdrant.repositories.facts import QdrantFactRepository
    from kt_qdrant.repositories.nodes import QdrantNodeRepository
    from kt_qdrant.repositories.seeds import QdrantSeedRepository

    qdrant_client = get_qdrant_client()
    await QdrantFactRepository(qdrant_client).ensure_collection()
    await QdrantNodeRepository(qdrant_client).ensure_collection()
    await QdrantSeedRepository(qdrant_client).ensure_collection()

    # Ensure Qdrant collections for all active non-default graphs
    try:
        from sqlalchemy import select as sa_select

        from kt_db.models import Graph

        async with session_factory() as ctrl_session:
            stmt = sa_select(Graph).where(Graph.status == "active", Graph.is_default.is_(False))
            result = await ctrl_session.execute(stmt)
            active_graphs = result.scalars().all()
            for g in active_graphs:
                prefix = f"{g.slug}__"
                try:
                    await QdrantFactRepository(qdrant_client, f"{prefix}facts").ensure_collection()
                    await QdrantNodeRepository(qdrant_client, f"{prefix}nodes").ensure_collection()
                    await QdrantSeedRepository(qdrant_client, f"{prefix}seeds").ensure_collection()
                except Exception:
                    logger.error("Failed to ensure Qdrant collections for graph %s", g.slug, exc_info=True)
    except Exception:
        logger.error("Failed to ensure per-graph Qdrant collections", exc_info=True)

    graph_resolver = GraphSessionResolver(
        session_factory,
        settings,
        default_graph_session_factory=session_factory,
        default_write_session_factory=write_session_factory,
    )

    default_graph_id = await _resolve_default_graph_id(session_factory)

    # Run all DB migrations in-process (core + plugins + per-graph).
    # Guaranteed to complete before this worker registers workflows.
    from kt_db.startup import run_startup_migrations

    await run_startup_migrations(settings)

    # Install the LLM usage sink so every gateway call records one row to
    # write_llm_usage via the expense-context pipeline.
    from kt_models.usage_sink import UsageSink

    UsageSink.install(write_session_factory)

    # Bootstrap plugins — hand each a PluginContext with session factories
    # and the shared HookRegistry so they can subscribe to hooks. Plugins
    # were discovered + registered in the worker's __main__.
    from kt_plugins import PluginContext, plugin_manager

    def _ctx_factory(manifest):  # noqa: ANN202
        plugin_settings = None
        if manifest.settings_class is not None:
            try:
                plugin_settings = manifest.settings_class()
            except Exception:
                logger.warning("plugin %r: settings failed to load", manifest.id, exc_info=True)
        return PluginContext(
            plugin_id=manifest.id,
            settings=plugin_settings or settings,
            hook_registry=plugin_manager.hook_registry,
            session_factory=session_factory,
            write_session_factory=write_session_factory,
            model_gateway=model_gateway,
            embedding_service=embedding_service,
            provider_registry=provider_registry,
        )

    await plugin_manager.bootstrap(ctx_factory=_ctx_factory)

    yield WorkerState(
        session_factory=session_factory,
        write_session_factory=write_session_factory,
        settings=settings,
        model_gateway=model_gateway,
        embedding_service=embedding_service,
        provider_registry=provider_registry,
        fetch_registry=fetch_registry,
        qdrant_client=qdrant_client,
        graph_resolver=graph_resolver,
        default_graph_id=default_graph_id,
    )

    # Plugin shutdown before DB engines go away.
    await plugin_manager.shutdown()

    # Drain and stop the usage sink before DB engines go away so that
    # in-flight usage rows land in write-db rather than being dropped.
    from kt_models.usage_sink import UsageSink as _UsageSink

    await _UsageSink.shutdown()

    if qdrant_client is not None:
        from kt_qdrant.client import close_qdrant_client

        await close_qdrant_client()
    await write_engine.dispose()
    await engine.dispose()


async def build_worker_state() -> WorkerState:
    """Build a WorkerState for use outside the Hatchet worker process.

    Unlike ``worker_lifespan`` (which is a generator tied to the worker
    lifecycle), this creates a standalone state suitable for background
    tasks triggered from API endpoints.  The caller is responsible for
    not leaking the underlying engine -- use sparingly.
    """
    settings = Settings()

    engine = get_engine(application_name="kt-worker")
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    write_engine = get_write_engine(application_name="kt-worker")
    write_session_factory = async_sessionmaker(write_engine, class_=AsyncSession, expire_on_commit=False)

    from kt_models.embeddings import EmbeddingService
    from kt_models.gateway import ModelGateway
    from kt_providers.registry import ProviderRegistry

    model_gateway = ModelGateway()
    embedding_service = EmbeddingService()

    provider_registry = ProviderRegistry()
    default_provider = settings.default_search_provider
    # Extra providers registered by services (typically bridged from plugins).
    # kt-hatchet stays plugin-agnostic — it only sees generic factories.
    from kt_providers.registry import iter_extra_provider_factories

    for extra in iter_extra_provider_factories():
        if default_provider not in (extra.provider_id, "all"):
            continue
        try:
            if not extra.is_available():
                continue
            provider_registry.register(extra.factory())
            logger.info("Registered extra search provider: %s", extra.name)
        except Exception:
            logger.exception("Failed to register extra search provider: %s", extra.name)

    from kt_providers.fetch import maybe_build_fetch_registry

    fetch_registry = maybe_build_fetch_registry(settings)

    # Qdrant vector search client (required for all vector search)
    from kt_qdrant.client import get_qdrant_client
    from kt_qdrant.repositories.facts import QdrantFactRepository
    from kt_qdrant.repositories.nodes import QdrantNodeRepository
    from kt_qdrant.repositories.seeds import QdrantSeedRepository

    qdrant_client = get_qdrant_client()
    await QdrantFactRepository(qdrant_client).ensure_collection()
    await QdrantNodeRepository(qdrant_client).ensure_collection()
    await QdrantSeedRepository(qdrant_client).ensure_collection()

    graph_resolver = GraphSessionResolver(
        session_factory,
        settings,
        default_graph_session_factory=session_factory,
        default_write_session_factory=write_session_factory,
    )

    default_graph_id = await _resolve_default_graph_id(session_factory)

    return WorkerState(
        session_factory=session_factory,
        write_session_factory=write_session_factory,
        settings=settings,
        model_gateway=model_gateway,
        embedding_service=embedding_service,
        provider_registry=provider_registry,
        fetch_registry=fetch_registry,
        qdrant_client=qdrant_client,
        graph_resolver=graph_resolver,
        default_graph_id=default_graph_id,
    )
