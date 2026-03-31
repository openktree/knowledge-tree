"""Async Redis caching utility for API and MCP endpoints.

Provides simple get/set/invalidate operations with JSON serialization.
Cache keys are namespaced with ``kt:`` to avoid collisions.

Usage::

    from kt_config.cache import cache_get, cache_set, cache_invalidate, make_cache_key

    key = make_cache_key("nodes:list", offset=0, limit=50, sort="edge_count")
    cached = await cache_get(key)
    if cached is not None:
        return cached
    # ... compute result ...
    await cache_set(key, result, ttl=30)
"""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any

import redis.asyncio as aioredis

logger = logging.getLogger(__name__)

_redis_client: aioredis.Redis | None = None


async def get_redis() -> aioredis.Redis:
    """Get or create a shared async Redis client."""
    global _redis_client
    if _redis_client is None:
        from kt_config.settings import get_settings

        _redis_client = aioredis.from_url(
            get_settings().redis_url,
            decode_responses=True,
        )
    return _redis_client


async def cache_get(key: str) -> Any | None:
    """Get a cached value. Returns None on miss or error."""
    try:
        r = await get_redis()
        val = await r.get(key)
        if val is not None:
            return json.loads(val)
    except Exception:
        logger.debug("Cache get failed for key=%s", key, exc_info=True)
    return None


async def cache_set(key: str, data: Any, ttl: int = 60) -> None:
    """Set a cached value with TTL in seconds. Silently ignores errors."""
    try:
        r = await get_redis()
        await r.set(key, json.dumps(data, default=str), ex=ttl)
    except Exception:
        logger.debug("Cache set failed for key=%s", key, exc_info=True)


async def cache_invalidate(pattern: str) -> int:
    """Delete all keys matching a glob pattern. Returns count deleted."""
    try:
        r = await get_redis()
        keys: list[str] = []
        async for key in r.scan_iter(match=pattern, count=500):
            keys.append(key)
        if keys:
            return await r.delete(*keys)  # type: ignore[return-value]
    except Exception:
        logger.debug("Cache invalidate failed for pattern=%s", pattern, exc_info=True)
    return 0


def make_cache_key(prefix: str, **params: Any) -> str:
    """Build a deterministic cache key from a prefix and keyword params.

    Example::

        make_cache_key("nodes:list", offset=0, limit=50, sort="edge_count")
        # => "kt:nodes:list:a1b2c3d4e5f6"
    """
    param_str = json.dumps(params, sort_keys=True, default=str)
    h = hashlib.md5(param_str.encode()).hexdigest()[:12]
    return f"kt:{prefix}:{h}"
