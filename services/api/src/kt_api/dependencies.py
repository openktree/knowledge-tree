"""Shared dependency injection for API endpoints."""

from __future__ import annotations

import logging
from collections.abc import AsyncGenerator

from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from kt_agents_core.state import AgentContext, EventCallback
from kt_config.settings import get_settings
from kt_db.session import get_session_factory, get_write_session_factory
from kt_graph.read_engine import ReadGraphEngine
from kt_models.embeddings import EmbeddingService
from kt_models.gateway import ModelGateway
from kt_providers.brave import BraveSearchProvider
from kt_providers.fetcher import ContentFetcher
from kt_providers.registry import ProviderRegistry
from kt_providers.serper import SerperSearchProvider

logger = logging.getLogger(__name__)

_session_factory: async_sessionmaker[AsyncSession] | None = None
_write_session_factory: async_sessionmaker[AsyncSession] | None = None
_qdrant_client: object | None = None


def get_session_factory_cached() -> async_sessionmaker[AsyncSession]:
    """Return a cached async session factory (singleton)."""
    global _session_factory  # noqa: PLW0603
    if _session_factory is None:
        _session_factory = get_session_factory()
    return _session_factory


def get_write_session_factory_cached() -> async_sessionmaker[AsyncSession]:
    """Return a cached write-db async session factory (singleton)."""
    global _write_session_factory  # noqa: PLW0603
    if _write_session_factory is None:
        _write_session_factory = get_write_session_factory()
    return _write_session_factory


def reset_session_factory() -> None:
    """Reset the cached session factory (used in tests)."""
    global _session_factory, _write_session_factory  # noqa: PLW0603
    _session_factory = None
    _write_session_factory = None


def get_qdrant_client_cached() -> object:
    """Return a cached Qdrant client singleton."""
    global _qdrant_client  # noqa: PLW0603
    if _qdrant_client is None:
        from kt_qdrant.client import get_qdrant_client

        _qdrant_client = get_qdrant_client()
    return _qdrant_client


async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency that yields a database session."""
    factory = get_session_factory_cached()
    async with factory() as session:
        yield session


async def get_write_db_session() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency that yields a write-db session."""
    factory = get_write_session_factory_cached()
    async with factory() as session:
        yield session


async def get_agent_context(
    emit_event: EventCallback | None = None,
) -> AgentContext:
    """Create a full AgentContext for running the navigation agent."""
    settings = get_settings()
    factory = get_session_factory_cached()
    session = factory()
    qdrant_client = get_qdrant_client_cached()

    graph_engine = ReadGraphEngine(session=session, qdrant_client=qdrant_client)
    embedding_service = EmbeddingService() if settings.openrouter_api_key else None
    model_gateway = ModelGateway()

    provider_registry = ProviderRegistry()
    default_provider = settings.default_search_provider
    if default_provider in ("brave", "all") and settings.brave_key:
        provider_registry.register(BraveSearchProvider(settings.brave_key))
    if default_provider in ("serper", "all") and settings.serper_key:
        provider_registry.register(SerperSearchProvider(settings.serper_key))

    content_fetcher = None
    if settings.enable_full_text_fetch:
        content_fetcher = ContentFetcher(
            timeout=settings.full_text_fetch_timeout,
            max_concurrent=settings.full_text_fetch_max_urls,
        )

    return AgentContext(
        graph_engine=graph_engine,
        provider_registry=provider_registry,
        model_gateway=model_gateway,
        embedding_service=embedding_service,
        session=session,
        emit_event=emit_event,
        content_fetcher=content_fetcher,
        session_factory=factory,
        qdrant_client=qdrant_client,
    )


def resolve_api_key(user: object) -> str | None:
    """Resolve the API key for a user.

    - If user has a BYOK key, decrypt and return it.
    - If user is admin, fall back to system key.
    - Otherwise return None.
    """
    from kt_db.models import User as UserModel

    u: UserModel = user  # type: ignore[assignment]
    encrypted = getattr(u, "encrypted_openrouter_key", None)
    if encrypted:
        from kt_api.auth.crypto import decrypt_api_key

        try:
            return decrypt_api_key(encrypted)
        except Exception:
            logger.warning("Failed to decrypt BYOK key for user %s", u.id)

    # Admins fall back to system key
    if u.is_superuser:
        settings = get_settings()
        return settings.openrouter_api_key or None

    return None


def require_api_key(user: object) -> str:
    """Resolve API key or raise 403 if unavailable."""
    key = resolve_api_key(user)
    if not key:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="An OpenRouter API key is required. Set one in your profile settings.",
        )
    return key
