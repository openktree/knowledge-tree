"""Worker lifespan -- shared state across all Hatchet tasks on a worker process.

Yields a ``WorkerState`` that each task receives via ``ctx.lifespan``.
This replaces the per-message service construction in the old stream
workers, amortising connection pool and provider setup across all tasks.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from kt_config.settings import Settings


@dataclass
class WorkerState:
    """Shared services available to every Hatchet task via ``ctx.lifespan``."""

    session_factory: async_sessionmaker[AsyncSession]
    write_session_factory: async_sessionmaker[AsyncSession]
    settings: Settings

    # Lazy-imported services -- set during lifespan setup
    model_gateway: object  # ModelGateway
    embedding_service: object  # EmbeddingService
    provider_registry: object  # ProviderRegistry
    content_fetcher: object | None  # ContentFetcher | None
    ontology_registry: object | None = None  # OntologyProviderRegistry | None
    qdrant_client: object | None = None  # AsyncQdrantClient | None


async def worker_lifespan() -> AsyncGenerator[WorkerState, None]:
    """Async context manager that Hatchet calls at worker start/stop."""
    settings = Settings()

    engine = create_async_engine(
        settings.database_url,
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
        pool_timeout=settings.db_pool_timeout,
        pool_pre_ping=True,
    )
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    write_engine = create_async_engine(
        settings.write_database_url,
        pool_size=settings.write_db_pool_size,
        max_overflow=settings.write_db_max_overflow,
        pool_timeout=settings.write_db_pool_timeout,
        pool_pre_ping=True,
        connect_args={"statement_cache_size": 0},
    )
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

    # Ontology provider setup
    ontology_registry = None
    if settings.enable_ontology_ancestry:
        from kt_ontology.cache import CachedOntologyProvider
        from kt_ontology.registry import OntologyProviderRegistry
        from kt_ontology.wikidata import WikidataOntologyProvider

        ontology_registry = OntologyProviderRegistry()
        wikidata = WikidataOntologyProvider(user_agent=settings.wikidata_user_agent)
        cached_wikidata = CachedOntologyProvider(
            inner=wikidata,
            redis_url=settings.redis_url,
            ttl=settings.ontology_cache_ttl,
        )
        ontology_registry.register(cached_wikidata, default=True)

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
        ontology_registry=ontology_registry,
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

    engine = create_async_engine(
        settings.database_url,
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
        pool_timeout=settings.db_pool_timeout,
        pool_pre_ping=True,
    )
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    write_engine = create_async_engine(
        settings.write_database_url,
        pool_size=settings.write_db_pool_size,
        max_overflow=settings.write_db_max_overflow,
        pool_timeout=settings.write_db_pool_timeout,
        pool_pre_ping=True,
        connect_args={"statement_cache_size": 0},
    )
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

    ontology_registry = None
    if settings.enable_ontology_ancestry:
        from kt_ontology.cache import CachedOntologyProvider
        from kt_ontology.registry import OntologyProviderRegistry
        from kt_ontology.wikidata import WikidataOntologyProvider

        ontology_registry = OntologyProviderRegistry()
        wikidata = WikidataOntologyProvider(user_agent=settings.wikidata_user_agent)
        cached_wikidata = CachedOntologyProvider(
            inner=wikidata,
            redis_url=settings.redis_url,
            ttl=settings.ontology_cache_ttl,
        )
        ontology_registry.register(cached_wikidata, default=True)

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
        ontology_registry=ontology_registry,
        qdrant_client=qdrant_client,
    )
