"""Qdrant-backed node vector repository.

Stores node embeddings in Qdrant for fast similarity search while nodes
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
    MatchValue,
    PointStruct,
    VectorParams,
)

from kt_config.settings import get_settings

logger = logging.getLogger(__name__)

NODES_COLLECTION = "nodes"


@dataclass
class NodeSearchResult:
    """Result from a Qdrant node similarity search."""

    node_id: uuid.UUID
    score: float
    node_type: str | None = None
    concept: str | None = None


class QdrantNodeRepository:
    """Repository for node vectors in Qdrant.

    Nodes remain in PostgreSQL; this stores only embeddings + minimal
    payload for filtering. The node_id links back to the PG Node row.
    """

    def __init__(self, client: AsyncQdrantClient) -> None:
        self._client = client

    async def ensure_collection(self) -> None:
        """Create the nodes collection if it doesn't exist."""
        settings = get_settings()
        collections = await self._client.get_collections()
        existing = {c.name for c in collections.collections}
        if NODES_COLLECTION not in existing:
            await self._client.create_collection(
                collection_name=NODES_COLLECTION,
                vectors_config=VectorParams(
                    size=settings.embedding_dimensions,
                    distance=Distance.COSINE,
                ),
            )
            logger.info("Created Qdrant collection '%s' (dim=%d)", NODES_COLLECTION, settings.embedding_dimensions)

    async def upsert(
        self,
        node_id: uuid.UUID,
        embedding: list[float],
        node_type: str | None = None,
        concept: str | None = None,
    ) -> None:
        """Insert or update a node vector in Qdrant."""
        payload: dict[str, object] = {}
        if node_type is not None:
            payload["node_type"] = node_type
        if concept is not None:
            payload["concept"] = concept

        point = PointStruct(
            id=str(node_id),
            vector=embedding,
            payload=payload,
        )
        await self._client.upsert(
            collection_name=NODES_COLLECTION,
            points=[point],
        )

    async def upsert_batch(
        self,
        nodes: list[tuple[uuid.UUID, list[float], str | None, str | None]],
        *,
        chunk_size: int = 200,
    ) -> None:
        """Batch upsert node vectors in chunks to avoid connection timeouts.

        Args:
            nodes: List of (node_id, embedding, node_type, concept) tuples.
            chunk_size: Max points per Qdrant upsert call (default 200).
        """
        if not nodes:
            return
        for i in range(0, len(nodes), chunk_size):
            chunk = nodes[i : i + chunk_size]
            points = [
                PointStruct(
                    id=str(nid),
                    vector=emb,
                    payload={
                        k: v
                        for k, v in [("node_type", nt), ("concept", concept)]
                        if v is not None
                    },
                )
                for nid, emb, nt, concept in chunk
            ]
            await self._client.upsert(
                collection_name=NODES_COLLECTION,
                points=points,
            )

    async def search_similar(
        self,
        embedding: list[float],
        limit: int = 10,
        score_threshold: float = 0.7,
        node_type: str | None = None,
        exclude_ids: list[uuid.UUID] | None = None,
    ) -> list[NodeSearchResult]:
        """Search for nodes similar to the given embedding.

        Args:
            embedding: Query embedding vector.
            limit: Maximum results to return.
            score_threshold: Minimum cosine similarity score.
            node_type: Optional filter by node type.
            exclude_ids: Optional node IDs to exclude from results.

        Returns:
            List of NodeSearchResult ordered by similarity (highest first).
        """
        query_filter = self._build_filter(node_type=node_type, exclude_ids=exclude_ids)

        results = await self._client.query_points(
            collection_name=NODES_COLLECTION,
            query=embedding,
            limit=limit,
            score_threshold=score_threshold,
            query_filter=query_filter,
            with_payload=True,
        )

        return [
            NodeSearchResult(
                node_id=uuid.UUID(str(point.id)),
                score=point.score,
                node_type=point.payload.get("node_type") if point.payload else None,
                concept=point.payload.get("concept") if point.payload else None,
            )
            for point in results.points
        ]

    async def get_vectors(self, node_ids: list[uuid.UUID]) -> dict[uuid.UUID, list[float]]:
        """Retrieve embedding vectors for a batch of node IDs.

        Returns a mapping of node_id -> embedding for points found in Qdrant.
        Missing IDs are silently omitted.
        """
        if not node_ids:
            return {}
        points = await self._client.retrieve(
            collection_name=NODES_COLLECTION,
            ids=[str(nid) for nid in node_ids],
            with_vectors=True,
            with_payload=False,
        )
        result: dict[uuid.UUID, list[float]] = {}
        for point in points:
            if point.vector is not None and isinstance(point.vector, list):
                result[uuid.UUID(str(point.id))] = point.vector
        return result

    async def delete(self, node_id: uuid.UUID) -> None:
        """Delete a node vector from Qdrant."""
        await self._client.delete(
            collection_name=NODES_COLLECTION,
            points_selector=[str(node_id)],
        )

    async def delete_batch(self, node_ids: list[uuid.UUID]) -> None:
        """Delete multiple node vectors from Qdrant."""
        if not node_ids:
            return
        await self._client.delete(
            collection_name=NODES_COLLECTION,
            points_selector=[str(nid) for nid in node_ids],
        )

    async def count(self) -> int:
        """Count total nodes in the collection."""
        info = await self._client.get_collection(NODES_COLLECTION)
        return info.points_count or 0

    def _build_filter(
        self,
        node_type: str | None = None,
        exclude_ids: list[uuid.UUID] | None = None,
    ) -> Filter | None:
        """Build a Qdrant filter from optional parameters."""
        conditions: list[FieldCondition] = []
        must_not = []

        if node_type is not None:
            conditions.append(
                FieldCondition(key="node_type", match=MatchValue(value=node_type)),
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
