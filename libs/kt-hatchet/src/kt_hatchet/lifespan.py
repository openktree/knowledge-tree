"""Worker lifespan -- shared state across all Hatchet tasks on a worker process.

Yields a ``WorkerState`` that each task receives via ``ctx.lifespan``.
This replaces the per-message service construction in the old stream
workers, amortising connection pool and provider setup across all tasks.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from dataclasses import dataclass
from typing import TYPE_CHECKING

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from kt_config.settings import Settings
from kt_db.session import get_engine, get_write_engine

if TYPE_CHECKING:
    from qdrant_client import AsyncQdrantClient

    from kt_models.embeddings import EmbeddingService
    from kt_models.gateway import ModelGateway
    from kt_providers.fetcher import ContentFetcher
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
    content_fetcher: ContentFetcher | None
    qdrant_client: AsyncQdrantClient | None = None


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

    content_fetcher = None
    if settings.enable_full_text_fetch:
        from kt_providers.fetcher import ContentFetcher

        content_fetcher = ContentFetcher(
            timeout=settings.full_text_fetch_timeout,
            max_concurrent=settings.full_text_fetch_max_urls,
        )

    # Qdrant vector search client (required for all vector search)
    from kt_qdrant.client import get_qdrant_client
    from kt_qdrant.repositories.facts import QdrantFactRepository
    from kt_qdrant.repositories.nodes import QdrantNodeRepository
    from kt_qdrant.repositories.seeds import QdrantSeedRepository

    qdrant_client = get_qdrant_client()
    await QdrantFactRepository(qdrant_client).ensure_collection()
    await QdrantNodeRepository(qdrant_client).ensure_collection()
    await QdrantSeedRepository(qdrant_client).ensure_collection()

    yield WorkerState(
        session_factory=session_factory,
        write_session_factory=write_session_factory,
        settings=settings,
        model_gateway=model_gateway,
        embedding_service=embedding_service,
        provider_registry=provider_registry,
        content_fetcher=content_fetcher,
        qdrant_client=qdrant_client,
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

    content_fetcher = None
    if settings.enable_full_text_fetch:
        from kt_providers.fetcher import ContentFetcher

        content_fetcher = ContentFetcher(
            timeout=settings.full_text_fetch_timeout,
            max_concurrent=settings.full_text_fetch_max_urls,
        )

    # Qdrant vector search client (required for all vector search)
    from kt_qdrant.client import get_qdrant_client
    from kt_qdrant.repositories.facts import QdrantFactRepository
    from kt_qdrant.repositories.nodes import QdrantNodeRepository
    from kt_qdrant.repositories.seeds import QdrantSeedRepository

    qdrant_client = get_qdrant_client()
    await QdrantFactRepository(qdrant_client).ensure_collection()
    await QdrantNodeRepository(qdrant_client).ensure_collection()
    await QdrantSeedRepository(qdrant_client).ensure_collection()

    return WorkerState(
        session_factory=session_factory,
        write_session_factory=write_session_factory,
        settings=settings,
        model_gateway=model_gateway,
        embedding_service=embedding_service,
        provider_registry=provider_registry,
        content_fetcher=content_fetcher,
        qdrant_client=qdrant_client,
    )
