"""Qdrant-backed seed vector repository.

Stores seed name embeddings for similarity-based deduplication.
"""

import logging
from dataclasses import dataclass

from qdrant_client import AsyncQdrantClient
from qdrant_client.models import (
    Distance,
    FieldCondition,
    Filter,
    MatchValue,
    PointStruct,
    VectorParams,
)

from kt_config.settings import get_settings

logger = logging.getLogger(__name__)

SEEDS_COLLECTION = "seeds"


@dataclass
class SeedSearchResult:
    """Result from a Qdrant seed similarity search."""

    seed_key: str
    score: float
    name: str | None = None
    node_type: str | None = None


class QdrantSeedRepository:
    """Repository for seed name vectors in Qdrant."""

    def __init__(self, client: AsyncQdrantClient) -> None:
        self._client = client

    async def ensure_collection(self) -> None:
        """Create the seeds collection if it doesn't exist."""
        settings = get_settings()
        collections = await self._client.get_collections()
        existing = {c.name for c in collections.collections}
        if SEEDS_COLLECTION not in existing:
            await self._client.create_collection(
                collection_name=SEEDS_COLLECTION,
                vectors_config=VectorParams(
                    size=settings.embedding_dimensions,
                    distance=Distance.COSINE,
                ),
            )
            logger.info("Created Qdrant collection '%s' (dim=%d)", SEEDS_COLLECTION, settings.embedding_dimensions)

    async def upsert(
        self,
        seed_key: str,
        embedding: list[float],
        name: str | None = None,
        node_type: str | None = None,
        context_text: str | None = None,
    ) -> None:
        """Insert or update a seed vector in Qdrant."""
        from kt_db.keys import key_to_uuid

        payload: dict[str, object] = {"seed_key": seed_key}
        if name is not None:
            payload["name"] = name
        if node_type is not None:
            payload["node_type"] = node_type
        if context_text is not None:
            payload["context_text"] = context_text

        # Use deterministic UUID from seed key as point ID
        point_id = str(key_to_uuid(seed_key))

        point = PointStruct(
            id=point_id,
            vector=embedding,
            payload=payload,
        )
        await self._client.upsert(
            collection_name=SEEDS_COLLECTION,
            points=[point],
        )

    async def upsert_batch(
        self,
        points: list[dict],
        *,
        chunk_size: int = 200,
    ) -> None:
        """Batch upsert seed vectors to Qdrant in chunks.

        Each dict in points: {"seed_key": str, "embedding": list[float],
        "name": str | None, "node_type": str | None, "context_text": str | None}

        Args:
            points: Seed data dicts.
            chunk_size: Max points per Qdrant upsert call (default 200).
        """
        from kt_db.keys import key_to_uuid

        qdrant_points = []
        for p in points:
            payload: dict[str, object] = {"seed_key": p["seed_key"]}
            if p.get("name"):
                payload["name"] = p["name"]
            if p.get("node_type"):
                payload["node_type"] = p["node_type"]
            if p.get("context_text"):
                payload["context_text"] = p["context_text"]

            point_id = str(key_to_uuid(p["seed_key"]))
            qdrant_points.append(
                PointStruct(
                    id=point_id,
                    vector=p["embedding"],
                    payload=payload,
                )
            )

        for i in range(0, len(qdrant_points), chunk_size):
            chunk = qdrant_points[i : i + chunk_size]
            await self._client.upsert(
                collection_name=SEEDS_COLLECTION,
                points=chunk,
            )

    async def find_similar(
        self,
        embedding: list[float],
        node_type: str | None = None,
        limit: int = 10,
        score_threshold: float = 0.90,
        exclude_keys: set[str] | None = None,
    ) -> list[SeedSearchResult]:
        """Find seeds with similar name embeddings."""
        query_filter = None
        if node_type is not None:
            query_filter = Filter(must=[FieldCondition(key="node_type", match=MatchValue(value=node_type))])

        results = await self._client.query_points(
            collection_name=SEEDS_COLLECTION,
            query=embedding,
            query_filter=query_filter,
            limit=limit + len(exclude_keys or set()),
            score_threshold=score_threshold,
            with_payload=True,
        )

        exclude = exclude_keys or set()
        matches: list[SeedSearchResult] = []
        for hit in results.points:
            payload = hit.payload or {}
            seed_key = payload.get("seed_key", "")
            if seed_key in exclude:
                continue
            matches.append(
                SeedSearchResult(
                    seed_key=seed_key,
                    score=hit.score,
                    name=payload.get("name"),
                    node_type=payload.get("node_type"),
                )
            )
            if len(matches) >= limit:
                break

        return matches

    async def delete(self, seed_key: str) -> None:
        """Remove a seed vector from Qdrant."""
        from kt_db.keys import key_to_uuid

        point_id = str(key_to_uuid(seed_key))
        await self._client.delete(
            collection_name=SEEDS_COLLECTION,
            points_selector=[point_id],
        )
