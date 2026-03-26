"""Qdrant-backed fact vector repository.

Stores fact embeddings in Qdrant for fast similarity search while facts
themselves remain in PostgreSQL as the source of truth.
"""

import logging
import uuid
from dataclasses import dataclass

from qdrant_client import AsyncQdrantClient
from qdrant_client.models import (
    Distance,
    FieldCondition,
    Filter,
    HasIdCondition,
    MatchAny,
    MatchValue,
    PointStruct,
    VectorParams,
)

from kt_config.settings import get_settings

logger = logging.getLogger(__name__)

FACTS_COLLECTION = "facts"


@dataclass
class FactSearchResult:
    """Result from a Qdrant fact similarity search."""

    fact_id: uuid.UUID
    score: float
    fact_type: str | None = None


class QdrantFactRepository:
    """Repository for fact vectors in Qdrant.

    Facts remain in PostgreSQL; this stores only embeddings + minimal
    payload for filtering. The fact_id links back to the PG Fact row.
    """

    def __init__(self, client: AsyncQdrantClient) -> None:
        self._client = client

    async def ensure_collection(self) -> None:
        """Create the facts collection if it doesn't exist."""
        settings = get_settings()
        collections = await self._client.get_collections()
        existing = {c.name for c in collections.collections}
        if FACTS_COLLECTION not in existing:
            await self._client.create_collection(
                collection_name=FACTS_COLLECTION,
                vectors_config=VectorParams(
                    size=settings.embedding_dimensions,
                    distance=Distance.COSINE,
                ),
            )
            logger.info("Created Qdrant collection '%s' (dim=%d)", FACTS_COLLECTION, settings.embedding_dimensions)

    async def upsert(
        self,
        fact_id: uuid.UUID,
        embedding: list[float],
        fact_type: str | None = None,
        node_ids: list[uuid.UUID] | None = None,
    ) -> None:
        """Insert or update a fact vector in Qdrant.

        Args:
            fact_id: The PostgreSQL fact ID (used as Qdrant point ID).
            embedding: The fact embedding vector.
            fact_type: Optional fact type for payload filtering.
            node_ids: Optional list of linked node IDs for payload filtering.
        """
        payload: dict[str, object] = {}
        if fact_type is not None:
            payload["fact_type"] = fact_type
        if node_ids is not None:
            payload["node_ids"] = [str(nid) for nid in node_ids]

        point = PointStruct(
            id=str(fact_id),
            vector=embedding,
            payload=payload,
        )
        await self._client.upsert(
            collection_name=FACTS_COLLECTION,
            points=[point],
        )

    async def upsert_batch(
        self,
        facts: list[tuple[uuid.UUID, list[float], str | None]],
        *,
        chunk_size: int = 200,
    ) -> None:
        """Batch upsert fact vectors in chunks to avoid connection timeouts.

        Args:
            facts: List of (fact_id, embedding, fact_type) tuples.
            chunk_size: Max points per Qdrant upsert call (default 200).
        """
        if not facts:
            return
        for i in range(0, len(facts), chunk_size):
            chunk = facts[i : i + chunk_size]
            points = [
                PointStruct(
                    id=str(fid),
                    vector=emb,
                    payload={"fact_type": ft} if ft else {},
                )
                for fid, emb, ft in chunk
            ]
            await self._client.upsert(
                collection_name=FACTS_COLLECTION,
                points=points,
            )

    async def search_similar(
        self,
        embedding: list[float],
        limit: int = 10,
        score_threshold: float = 0.5,
        fact_type: str | None = None,
        exclude_ids: list[uuid.UUID] | None = None,
        node_ids: list[str] | None = None,
    ) -> list[FactSearchResult]:
        """Search for facts similar to the given embedding.

        Args:
            embedding: Query embedding vector.
            limit: Maximum results to return.
            score_threshold: Minimum cosine similarity score.
            fact_type: Optional filter by fact type.
            exclude_ids: Optional fact IDs to exclude from results.
            node_ids: Optional filter — only return facts linked to these nodes.

        Returns:
            List of FactSearchResult ordered by similarity (highest first).
        """
        query_filter = self._build_filter(fact_type=fact_type, exclude_ids=exclude_ids, node_ids=node_ids)

        results = await self._client.query_points(
            collection_name=FACTS_COLLECTION,
            query=embedding,
            limit=limit,
            score_threshold=score_threshold,
            query_filter=query_filter,
            with_payload=True,
        )

        return [
            FactSearchResult(
                fact_id=uuid.UUID(str(point.id)),
                score=point.score,
                fact_type=point.payload.get("fact_type") if point.payload else None,
            )
            for point in results.points
        ]

    async def find_most_similar(
        self,
        embedding: list[float],
        score_threshold: float = 0.92,
    ) -> FactSearchResult | None:
        """Find the single most similar fact above threshold, or None."""
        results = await self.search_similar(
            embedding=embedding,
            limit=1,
            score_threshold=score_threshold,
        )
        return results[0] if results else None

    async def search_by_node(
        self,
        embedding: list[float],
        node_id: uuid.UUID,
        limit: int = 30,
        score_threshold: float = 0.5,
    ) -> list[FactSearchResult]:
        """Search for facts linked to a specific node."""
        query_filter = Filter(
            must=[
                FieldCondition(
                    key="node_ids",
                    match=MatchValue(value=str(node_id)),
                ),
            ],
        )

        results = await self._client.query_points(
            collection_name=FACTS_COLLECTION,
            query=embedding,
            limit=limit,
            score_threshold=score_threshold,
            query_filter=query_filter,
            with_payload=True,
        )

        return [
            FactSearchResult(
                fact_id=uuid.UUID(str(point.id)),
                score=point.score,
                fact_type=point.payload.get("fact_type") if point.payload else None,
            )
            for point in results.points
        ]

    async def get_vectors(self, fact_ids: list[uuid.UUID]) -> dict[uuid.UUID, list[float]]:
        """Retrieve embedding vectors for a batch of fact IDs.

        Returns a mapping of fact_id -> embedding for points found in Qdrant.
        Missing IDs are silently omitted.
        """
        if not fact_ids:
            return {}
        points = await self._client.retrieve(
            collection_name=FACTS_COLLECTION,
            ids=[str(fid) for fid in fact_ids],
            with_vectors=True,
            with_payload=False,
        )
        result: dict[uuid.UUID, list[float]] = {}
        for point in points:
            if point.vector is not None and isinstance(point.vector, list):
                result[uuid.UUID(str(point.id))] = point.vector
        return result

    async def delete(self, fact_id: uuid.UUID) -> None:
        """Delete a fact vector from Qdrant."""
        await self._client.delete(
            collection_name=FACTS_COLLECTION,
            points_selector=[str(fact_id)],
        )

    async def delete_batch(self, fact_ids: list[uuid.UUID]) -> None:
        """Delete multiple fact vectors from Qdrant."""
        if not fact_ids:
            return
        await self._client.delete(
            collection_name=FACTS_COLLECTION,
            points_selector=[str(fid) for fid in fact_ids],
        )

    async def update_node_ids(
        self,
        fact_id: uuid.UUID,
        node_ids: list[uuid.UUID],
    ) -> None:
        """Update the node_ids payload for a fact point."""
        await self._client.set_payload(
            collection_name=FACTS_COLLECTION,
            payload={"node_ids": [str(nid) for nid in node_ids]},
            points=[str(fact_id)],
        )

    async def count(self) -> int:
        """Count total facts in the collection."""
        info = await self._client.get_collection(FACTS_COLLECTION)
        return info.points_count or 0

    def _build_filter(
        self,
        fact_type: str | None = None,
        exclude_ids: list[uuid.UUID] | None = None,
        node_ids: list[str] | None = None,
    ) -> Filter | None:
        """Build a Qdrant filter from optional parameters."""
        conditions: list[FieldCondition] = []
        must_not = []

        if fact_type is not None:
            conditions.append(
                FieldCondition(key="fact_type", match=MatchValue(value=fact_type)),
            )

        if node_ids:
            conditions.append(
                FieldCondition(key="node_ids", match=MatchAny(any=node_ids)),
            )

        if exclude_ids:
            must_not.append(
                HasIdCondition(has_id=[str(eid) for eid in exclude_ids]),
            )

        if not conditions and not must_not:
            return None

        return Filter(
            must=conditions if conditions else None,
            must_not=must_not if must_not else None,
        )
