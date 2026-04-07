"""Qdrant client singleton."""

from qdrant_client import AsyncQdrantClient

from kt_config.settings import get_settings

_client: AsyncQdrantClient | None = None


def get_qdrant_client() -> AsyncQdrantClient:
    """Get or create a singleton async Qdrant client."""
    global _client
    if _client is None:
        settings = get_settings()
        url = settings.qdrant_url
        if settings.qdrant_tls:
            if url.startswith("http://"):
                url = url.replace("http://", "https://", 1)
            elif not url.startswith("https://"):
                import logging

                logging.getLogger(__name__).warning("qdrant_tls is enabled but qdrant_url=%r does not use https", url)
        _client = AsyncQdrantClient(url=url, timeout=settings.qdrant_timeout)
    return _client


def make_qdrant_client(url: str, timeout: int | None = None) -> AsyncQdrantClient:
    """Construct a non-singleton client for an arbitrary Qdrant URL.

    Used by per-graph provisioning when a graph lives in a database whose
    associated Qdrant instance differs from the system default. The caller
    is responsible for closing the returned client when done.
    """
    if timeout is None:
        timeout = get_settings().qdrant_timeout
    return AsyncQdrantClient(url=url, timeout=timeout)


async def close_qdrant_client() -> None:
    """Close the singleton client (for graceful shutdown)."""
    global _client
    if _client is not None:
        await _client.close()
        _client = None
