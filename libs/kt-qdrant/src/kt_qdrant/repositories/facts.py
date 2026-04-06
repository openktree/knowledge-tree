"""Qdrant-backed fact vector repository.

Stores fact embeddings in Qdrant for fast similarity search while facts
themselves remain in PostgreSQL as the source of truth.
"""

import asyncio
import logging
import uuid
from dataclasses import dataclass

from qdrant_client import AsyncQdrantClient
from qdrant_client.http.exceptions import ResponseHandlingException
from qdrant_client.models import (
    Distance,
    FieldCondition,
    Filter,
    Fusion,
    FusionQuery,
    HasIdCondition,
    MatchAny,
    MatchText,
    MatchValue,
    PointStruct,
    Prefetch,
    TextIndexParams,
    TextIndexType,
    TokenizerType,
    VectorParams,
)

from kt_config.settings import get_settings

logger = logging.getLogger(__name__)

FACTS_COLLECTION = "facts"

_SEARCH_MAX_RETRIES = 3
_SEARCH_BASE_DELAY = 0.5  # seconds


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

    def __init__(self, client: AsyncQdrantClient, collection_name: str = FACTS_COLLECTION) -> None:
        self._client = client
        self._collection_name = collection_name

    async def ensure_collection(self) -> None:
        """Create the facts collection if it doesn't exist."""
        settings = get_settings()
        collections = await self._client.get_collections()
        existing = {c.name for c in collections.collections}
        if self._collection_name not in existing:
            await self._client.create_collection(
                collection_name=self._collection_name,
                vectors_config=VectorParams(
                    size=settings.embedding_dimensions,
                    distance=Distance.COSINE,
                ),
            )
            logger.info("Created Qdrant collection '%s' (dim=%d)", self._collection_name, settings.embedding_dimensions)

    async def ensure_text_index(self) -> None:
        """Create a full-text index on the 'content' payload field if not present."""
        try:
            collection_info = await self._client.get_collection(self._collection_name)
            existing_indexes = collection_info.payload_schema or {}
            if "content" in existing_indexes:
                return
        except Exception:
            pass

        await self._client.create_payload_index(
            collection_name=self._collection_name,
            field_name="content",
            field_schema=TextIndexParams(
                type=TextIndexType.TEXT,
                tokenizer=TokenizerType.WORD,
                min_token_len=2,
                max_token_len=40,
                lowercase=True,
            ),
        )
        logger.info("Created text index on '%s.content'", self._collection_name)

    async def upsert(
        self,
        fact_id: uuid.UUID,
        embedding: list[float],
        fact_type: str | None = None,
        node_ids: list[uuid.UUID] | None = None,
        content: str | None = None,
    ) -> None:
        """Insert or update a fact vector in Qdrant.

        Args:
            fact_id: The PostgreSQL fact ID (used as Qdrant point ID).
            embedding: The fact embedding vector.
            fact_type: Optional fact type for payload filtering.
            node_ids: Optional list of linked node IDs for payload filtering.
            content: Optional fact text content for full-text search.
        """
        payload: dict[str, object] = {}
        if fact_type is not None:
            payload["fact_type"] = fact_type
        if node_ids is not None:
            payload["node_ids"] = [str(nid) for nid in node_ids]
        if content is not None:
            payload["content"] = content

        point = PointStruct(
            id=str(fact_id),
            vector=embedding,
            payload=payload,
        )
        await self._client.upsert(
            collection_name=self._collection_name,
            points=[point],
        )

    async def upsert_batch(
        self,
        facts: list[tuple[uuid.UUID, list[float], str | None]]
        | list[tuple[uuid.UUID, list[float], str | None, str | None]]
        | list[tuple[uuid.UUID, list[float], str | None, str | None, list[uuid.UUID] | None]],
        *,
        chunk_size: int = 200,
    ) -> None:
        """Batch upsert fact vectors in chunks to avoid connection timeouts.

        Args:
            facts: List of ``(fact_id, embedding, fact_type)`` 3-tuples,
                ``(fact_id, embedding, fact_type, content)`` 4-tuples, or
                ``(fact_id, embedding, fact_type, content, node_ids)`` 5-tuples.
            chunk_size: Max points per Qdrant upsert call (default 200).
        """
        if not facts:
            return
        for i in range(0, len(facts), chunk_size):
            chunk = facts[i : i + chunk_size]
            points = []
            for item in chunk:
                fid = item[0]
                emb = item[1]
                ft = item[2]
                content = item[3] if len(item) > 3 else None  # type: ignore[misc]
                node_ids = item[4] if len(item) > 4 else None  # type: ignore[misc]
                payload: dict[str, object] = {}
                if ft:
                    payload["fact_type"] = ft
                if content:
                    payload["content"] = content
                if node_ids is not None:
                    payload["node_ids"] = [str(nid) for nid in node_ids]
                points.append(PointStruct(id=str(fid), vector=emb, payload=payload))
            await self._client.upsert(
                collection_name=self._collection_name,
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

        last_exc: Exception | None = None
        for attempt in range(_SEARCH_MAX_RETRIES):
            try:
                results = await self._client.query_points(
                    collection_name=self._collection_name,
                    query=embedding,
                    limit=limit,
                    score_threshold=score_threshold,
                    query_filter=query_filter,
                    with_payload=True,
                )
                break
            except ResponseHandlingException as exc:
                last_exc = exc
                if attempt < _SEARCH_MAX_RETRIES - 1:
                    delay = _SEARCH_BASE_DELAY * (2**attempt)
                    logger.warning(
                        "Qdrant search_similar retry %d/%d after %.1fs", attempt + 1, _SEARCH_MAX_RETRIES, delay
                    )
                    await asyncio.sleep(delay)
        else:
            raise last_exc  # type: ignore[misc]

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
            collection_name=self._collection_name,
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

    async def hybrid_search(
        self,
        query_embedding: list[float],
        query_text: str,
        limit: int = 30,
        score_threshold: float = 0.3,
        fact_type: str | None = None,
    ) -> list[FactSearchResult]:
        """Hybrid search combining vector similarity and keyword matching.

        Uses Qdrant prefetch with RRF (Reciprocal Rank Fusion) to merge
        results from both a vector nearest-neighbour search and a full-text
        keyword search on the ``content`` payload field.

        Args:
            query_embedding: Query embedding vector for semantic search.
            query_text: Query text for full-text keyword search.
            limit: Maximum results to return.
            score_threshold: Minimum vector similarity score for the
                vector prefetch branch.
            fact_type: Optional filter by fact type (applied to both branches).
        """
        query_filter = self._build_filter(fact_type=fact_type)

        # Keyword prefetch — full-text match on the content field
        keyword_must: list[FieldCondition | Filter] = [
            FieldCondition(key="content", match=MatchText(text=query_text)),
        ]
        if query_filter and query_filter.must:
            must = query_filter.must
            if isinstance(must, list):
                keyword_must.extend(must)
            else:
                keyword_must.append(must)

        keyword_prefetch = Prefetch(
            query=query_embedding,
            filter=Filter(must=keyword_must),  # type: ignore[arg-type]
            limit=limit,
        )

        # Vector prefetch — pure semantic similarity
        vector_prefetch = Prefetch(
            query=query_embedding,
            score_threshold=score_threshold,
            filter=query_filter,
            limit=limit,
        )

        results = await self._client.query_points(
            collection_name=self._collection_name,
            prefetch=[keyword_prefetch, vector_prefetch],
            query=FusionQuery(fusion=Fusion.RRF),
            limit=limit,
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
            collection_name=self._collection_name,
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
            collection_name=self._collection_name,
            points_selector=[str(fact_id)],
        )

    async def delete_batch(self, fact_ids: list[uuid.UUID]) -> None:
        """Delete multiple fact vectors from Qdrant."""
        if not fact_ids:
            return
        await self._client.delete(
            collection_name=self._collection_name,
            points_selector=[str(fid) for fid in fact_ids],
        )

    async def update_node_ids(
        self,
        fact_id: uuid.UUID,
        node_ids: list[uuid.UUID],
    ) -> None:
        """Update the node_ids payload for a fact point."""
        await self._client.set_payload(
            collection_name=self._collection_name,
            payload={"node_ids": [str(nid) for nid in node_ids]},
            points=[str(fact_id)],
        )

    async def append_node_id(self, fact_id: uuid.UUID, node_id: uuid.UUID) -> None:
        """Append a node_id to the fact's node_ids payload (idempotent).

        No-op if the fact point does not exist in Qdrant yet (e.g. the
        embedding upsert hasn't happened).

        Note: the retrieve → check → set_payload sequence is not atomic.
        Concurrent calls for the same fact could lose an append.  This is
        acceptable because Qdrant is a secondary index (PostgreSQL NodeFact
        is authoritative) and the backfill script can repair any drift.
        """
        points = await self._client.retrieve(
            collection_name=self._collection_name,
            ids=[str(fact_id)],
            with_payload=["node_ids"],
            with_vectors=False,
        )
        if not points:
            return
        existing: list[str] = []
        if points[0].payload:
            existing = points[0].payload.get("node_ids", [])
        nid_str = str(node_id)
        if nid_str not in existing:
            existing.append(nid_str)
            await self._client.set_payload(
                collection_name=self._collection_name,
                payload={"node_ids": existing},
                points=[str(fact_id)],
            )

    async def remove_node_id(self, fact_id: uuid.UUID, node_id: uuid.UUID) -> None:
        """Remove a node_id from the fact's node_ids payload (idempotent).

        No-op if the fact point does not exist in Qdrant.
        """
        points = await self._client.retrieve(
            collection_name=self._collection_name,
            ids=[str(fact_id)],
            with_payload=["node_ids"],
            with_vectors=False,
        )
        if not points:
            return
        existing: list[str] = []
        if points[0].payload:
            existing = points[0].payload.get("node_ids", [])
        nid_str = str(node_id)
        if nid_str in existing:
            existing.remove(nid_str)
            await self._client.set_payload(
                collection_name=self._collection_name,
                payload={"node_ids": existing},
                points=[str(fact_id)],
            )

    async def count(self) -> int:
        """Count total facts in the collection."""
        info = await self._client.get_collection(self._collection_name)
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
