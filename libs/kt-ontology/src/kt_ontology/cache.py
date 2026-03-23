"""Redis cache wrapper for ontology providers.

Wraps any OntologyProvider with transparent Redis caching. Cache keys
are formatted as ``ontology:{provider_id}:{node_type}:{normalized_name}``
with configurable TTL (default 7 days).
"""

from __future__ import annotations

import json
import logging
import re

import redis.asyncio as aioredis

from kt_ontology.base import AncestorEntry, AncestryChain, OntologyProvider

logger = logging.getLogger(__name__)


def _normalize_name(name: str) -> str:
    """Normalize a concept name for cache key usage."""
    return re.sub(r"\s+", "_", name.strip().lower())


class CachedOntologyProvider(OntologyProvider):
    """Wraps an OntologyProvider with Redis-backed caching."""

    def __init__(
        self,
        inner: OntologyProvider,
        redis_url: str,
        ttl: int = 604800,  # 7 days
    ) -> None:
        self._inner = inner
        self._redis: aioredis.Redis = aioredis.from_url(redis_url)  # type: ignore[assignment]
        self._ttl = ttl

    @property
    def provider_id(self) -> str:
        return self._inner.provider_id

    def _cache_key(self, concept_name: str, node_type: str) -> str:
        normalized = _normalize_name(concept_name)
        return f"ontology:{self.provider_id}:{node_type}:{normalized}"

    async def get_ancestry(
        self, concept_name: str, node_type: str
    ) -> AncestryChain | None:
        key = self._cache_key(concept_name, node_type)

        # Try cache first
        try:
            cached = await self._redis.get(key)
            if cached is not None:
                data = json.loads(cached)
                if data is None:
                    logger.debug("ontology cache hit (negative) for %s", key)
                    return None  # Cached negative result
                logger.debug("ontology cache hit for %s", key)
                return AncestryChain(
                    ancestors=[AncestorEntry(**a) for a in data["ancestors"]],
                    source=data["source"],
                )
        except Exception:
            logger.error(
                "ontology cache READ failed for %s — falling through to API "
                "(Redis may be down, risk of upstream rate limiting)",
                key,
                exc_info=True,
            )

        # Cache miss — call inner provider
        result = await self._inner.get_ancestry(concept_name, node_type)

        # Store in cache
        try:
            if result is None:
                await self._redis.set(key, json.dumps(None), ex=self._ttl)
            else:
                await self._redis.set(key, result.model_dump_json(), ex=self._ttl)
        except Exception:
            logger.error(
                "ontology cache WRITE failed for %s — result will not be cached "
                "(Redis may be down, subsequent calls will hit API again)",
                key,
                exc_info=True,
            )

        return result

    async def is_available(self) -> bool:
        return await self._inner.is_available()

    async def close(self) -> None:
        """Close Redis connection."""
        await self._redis.aclose()
