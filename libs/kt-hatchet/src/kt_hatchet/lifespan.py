"""Worker lifespan -- shared state across all Hatchet tasks on a worker process.

Yields a ``WorkerState`` that each task receives via ``ctx.lifespan``.
This replaces the per-message service construction in the old stream
workers, amortising connection pool and provider setup across all tasks.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncGenerator
from dataclasses import dataclass
from typing import TYPE_CHECKING

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from kt_config.settings import Settings
from kt_db.graph_sessions import GraphSessionResolver
from kt_db.session import get_engine, get_write_engine

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from qdrant_client import AsyncQdrantClient

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

    async def resolve_sessions(
        self, graph_id: str | None = None
    ) -> tuple["async_sessionmaker[AsyncSession]", "async_sessionmaker[AsyncSession]"]:
        """Resolve session factories for a graph_id.

        Returns (graph_session_factory, write_session_factory).
        When graph_id is None or "default", returns the system-level factories.
        """
        if not graph_id or graph_id == "default" or self.graph_resolver is None:
            return self.session_factory, self.write_session_factory
        import uuid as _uuid

        gs = await self.graph_resolver.resolve(_uuid.UUID(graph_id))
        return gs.graph_session_factory, gs.write_session_factory


async def worker_lifespan() -> AsyncGenerator[WorkerState, None]:
    """Async context manager that Hatchet calls at worker start/stop."""
    settings = Settings()

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
    if default_provider in ("brave", "all") and settings.brave_key:
        from kt_providers.brave import BraveSearchProvider

        provider_registry.register(BraveSearchProvider(settings.brave_key))
    if default_provider in ("serper", "all") and getattr(settings, "serper_key", ""):
        from kt_providers.serper import SerperSearchProvider

        provider_registry.register(SerperSearchProvider(settings.serper_key))

    fetch_registry = None
    if settings.enable_full_text_fetch:
        from kt_providers.fetch import build_fetch_registry

        fetch_registry = build_fetch_registry(settings)

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
    )

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
    if default_provider in ("brave", "all") and settings.brave_key:
        from kt_providers.brave import BraveSearchProvider

        provider_registry.register(BraveSearchProvider(settings.brave_key))
    if default_provider in ("serper", "all") and getattr(settings, "serper_key", ""):
        from kt_providers.serper import SerperSearchProvider

        provider_registry.register(SerperSearchProvider(settings.serper_key))

    fetch_registry = None
    if settings.enable_full_text_fetch:
        from kt_providers.fetch import build_fetch_registry

        fetch_registry = build_fetch_registry(settings)

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
    )
