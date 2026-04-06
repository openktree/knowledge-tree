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


async def close_qdrant_client() -> None:
    """Close the singleton client (for graceful shutdown)."""
    global _client
    if _client is not None:
        await _client.close()
        _client = None
