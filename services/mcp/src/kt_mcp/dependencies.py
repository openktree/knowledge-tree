"""Shared singletons for the MCP server — session factory and Qdrant client."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from kt_db.graph_sessions import GraphSessionResolver
from kt_db.session import get_session_factory
from kt_models.embeddings import EmbeddingService

_session_factory: async_sessionmaker[AsyncSession] | None = None
_qdrant_client: object | None = None
_embedding_service: EmbeddingService | None = None
_graph_resolver: GraphSessionResolver | None = None


def get_session_factory_cached() -> async_sessionmaker[AsyncSession]:
    """Return a cached async session factory (singleton) for graph-db (read-only)."""
    global _session_factory  # noqa: PLW0603
    if _session_factory is None:
        _session_factory = get_session_factory(application_name="kt-mcp")
    return _session_factory


def get_graph_resolver_cached() -> GraphSessionResolver:
    """Return a cached GraphSessionResolver singleton."""
    global _graph_resolver  # noqa: PLW0603
    if _graph_resolver is None:
        _graph_resolver = GraphSessionResolver(get_session_factory_cached())
    return _graph_resolver


def get_qdrant_client_cached() -> object:
    """Return a cached Qdrant client singleton."""
    global _qdrant_client  # noqa: PLW0603
    if _qdrant_client is None:
        from kt_qdrant.client import get_qdrant_client

        _qdrant_client = get_qdrant_client()
    return _qdrant_client


def get_embedding_service_cached() -> EmbeddingService | None:
    """Return a cached EmbeddingService singleton (None if no API key)."""
    global _embedding_service  # noqa: PLW0603
    if _embedding_service is None:
        from kt_config.settings import get_settings

        settings = get_settings()
        if settings.openrouter_api_key:
            _embedding_service = EmbeddingService()
    return _embedding_service


def reset_singletons() -> None:
    """Reset cached singletons (used in tests)."""
    global _session_factory, _qdrant_client, _embedding_service, _graph_resolver  # noqa: PLW0603
    _session_factory = None
    _qdrant_client = None
    _embedding_service = None
    _graph_resolver = None
