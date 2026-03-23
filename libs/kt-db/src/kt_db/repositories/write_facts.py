"""Write-optimized fact repository.

All operations target the write-db.  No FK constraints, no advisory locks.
Facts use UUID PKs (not TEXT keys) because identity is determined by
embedding-based dedup, not a deterministic key formula.
"""

import logging
import uuid

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from kt_db.write_models import (
    WriteFact,
    WriteFactSource,
    WriteNode,
    WriteNodeFactRejection,
)

logger = logging.getLogger(__name__)


class WriteFactRepository:
    """Upsert-friendly repository for facts in the write-optimized database."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ── Fact CRUD ──────────────────────────────────────────────────────

    async def upsert(
        self,
        fact_id: uuid.UUID,
        content: str,
        fact_type: str,
        metadata_: dict | None = None,
    ) -> uuid.UUID:
        """Insert or update a fact. Returns the fact ID."""
        stmt = (
            pg_insert(WriteFact)
            .values(
                id=fact_id,
                content=content,
                fact_type=fact_type,
                metadata_=metadata_,
            )
            .on_conflict_do_update(
                index_elements=[WriteFact.id],
                set_={"content": content, "fact_type": fact_type, "updated_at": func.clock_timestamp()},
            )
        )
        await self._session.execute(stmt)
        return fact_id

    async def update_fields(self, fact_id: uuid.UUID, **kwargs: object) -> None:
        """Update arbitrary fields on a WriteFact."""
        if not kwargs:
            return
        update_dict = dict(kwargs)
        update_dict["updated_at"] = func.clock_timestamp()
        stmt = (
            WriteFact.__table__.update()
            .where(WriteFact.id == fact_id)
            .values(**update_dict)
        )
        await self._session.execute(stmt)

    async def get_by_id(self, fact_id: uuid.UUID) -> WriteFact | None:
        result = await self._session.execute(
            select(WriteFact).where(WriteFact.id == fact_id)
        )
        return result.scalar_one_or_none()

    async def get_by_ids(self, fact_ids: list[uuid.UUID]) -> list[WriteFact]:
        if not fact_ids:
            return []
        result = await self._session.execute(
            select(WriteFact).where(WriteFact.id.in_(fact_ids))
        )
        return list(result.scalars().all())

    # ── Fact sources ───────────────────────────────────────────────────

    async def create_fact_source(
        self,
        fact_id: uuid.UUID,
        raw_source_uri: str,
        raw_source_title: str | None,
        raw_source_content_hash: str,
        raw_source_provider_id: str,
        context_snippet: str | None = None,
        attribution: str | None = None,
        author_person: str | None = None,
        author_org: str | None = None,
    ) -> uuid.UUID:
        """Create a fact-source provenance record. Returns the row ID."""
        row_id = uuid.uuid4()
        stmt = pg_insert(WriteFactSource).values(
            id=row_id,
            fact_id=fact_id,
            raw_source_uri=raw_source_uri,
            raw_source_title=raw_source_title,
            raw_source_content_hash=raw_source_content_hash,
            raw_source_provider_id=raw_source_provider_id,
            context_snippet=context_snippet,
            attribution=attribution,
            author_person=author_person,
            author_org=author_org,
        )
        await self._session.execute(stmt)
        return row_id

    # ── Fact pool queries (for edge candidate discovery) ───────────────

    async def find_nodes_sharing_facts(
        self,
        node_id: uuid.UUID,
        limit: int = 20,
    ) -> list[tuple[uuid.UUID, str, list[uuid.UUID]]]:
        """Find nodes that share facts with the given node via fact_ids array overlap.

        Returns: List of (node_uuid, concept, [shared_fact_ids]) sorted by count desc.

        Uses the deterministic key_to_uuid mapping: the node_id passed here is
        a UUID; we find its WriteNode by UUID-matching its key, then compare
        fact_ids arrays across all WriteNodes.
        """
        # Get the source node's fact_ids
        source_stmt = select(WriteNode).where(WriteNode.node_uuid == node_id)
        source_node = (await self._session.execute(source_stmt)).scalar_one_or_none()

        if source_node is None or not source_node.fact_ids:
            return []

        source_fact_set = set(source_node.fact_ids)

        # Find other nodes with overlapping fact_ids
        all_result = await self._session.execute(
            select(WriteNode).where(WriteNode.node_uuid != node_id, WriteNode.fact_ids.isnot(None))
        )
        candidates: list[tuple[uuid.UUID, str, list[uuid.UUID]]] = []
        for wn in all_result.scalars().all():
            if not wn.fact_ids:
                continue
            shared = source_fact_set & set(wn.fact_ids)
            if shared:
                shared_uuids = [uuid.UUID(fid) for fid in shared]
                candidates.append((wn.node_uuid, wn.concept, shared_uuids))

        # Sort by shared count descending
        candidates.sort(key=lambda x: len(x[2]), reverse=True)
        return candidates[:limit]

    async def find_nodes_by_text_facts(
        self,
        query: str,
        source_node_id: uuid.UUID,
        threshold: float = 0.3,
        node_limit: int = 10,
    ) -> list[tuple[uuid.UUID, str, list[uuid.UUID]]]:
        """Find nodes with text-matching facts via pg_trgm on write_facts.

        1. Search write_facts by word_similarity
        2. Find which write_nodes contain those fact IDs in their fact_ids array
        3. Exclude facts already linked to the source node
        """
        # Step 1: Find matching fact IDs
        fact_stmt = (
            select(WriteFact.id)
            .where(func.word_similarity(query, WriteFact.content) >= threshold)
            .order_by(func.word_similarity(query, WriteFact.content).desc())
            .limit(100)
        )
        fact_result = await self._session.execute(fact_stmt)
        matching_fact_ids = {str(row[0]) for row in fact_result.all()}

        if not matching_fact_ids:
            return []

        # Step 2: Get source node's fact_ids to exclude
        source_stmt = select(WriteNode).where(WriteNode.node_uuid == source_node_id)
        source_wn = (await self._session.execute(source_stmt)).scalar_one_or_none()
        source_fact_ids: set[str] = set(source_wn.fact_ids or []) if source_wn else set()

        # Exclude source node's own facts
        candidate_fact_ids = matching_fact_ids - source_fact_ids

        if not candidate_fact_ids:
            return []

        # Step 3: Find nodes owning these facts
        all_nodes_result = await self._session.execute(
            select(WriteNode).where(WriteNode.node_uuid != source_node_id, WriteNode.fact_ids.isnot(None))
        )
        candidates: list[tuple[uuid.UUID, str, list[uuid.UUID]]] = []
        for wn in all_nodes_result.scalars().all():
            if not wn.fact_ids:
                continue
            overlap = candidate_fact_ids & set(wn.fact_ids)
            if overlap:
                evidence = [uuid.UUID(fid) for fid in overlap]
                candidates.append((wn.node_uuid, wn.concept, evidence))

        candidates.sort(key=lambda x: len(x[2]), reverse=True)
        return candidates[:node_limit]

    async def find_nodes_by_embedding_facts(
        self,
        candidate_fact_ids: list[uuid.UUID],
        source_node_id: uuid.UUID,
        node_limit: int = 15,
    ) -> list[tuple[uuid.UUID, str, list[uuid.UUID]]]:
        """Given fact IDs from Qdrant vector search, find owning write_nodes.

        The Qdrant search is done externally; this method does the relational
        join via write_nodes.fact_ids arrays.
        """
        if not candidate_fact_ids:
            return []

        candidate_str_ids = {str(fid) for fid in candidate_fact_ids}

        result = await self._session.execute(
            select(WriteNode).where(WriteNode.node_uuid != source_node_id, WriteNode.fact_ids.isnot(None))
        )

        candidates: list[tuple[uuid.UUID, str, list[uuid.UUID]]] = []
        for wn in result.scalars().all():
            if not wn.fact_ids:
                continue
            overlap = candidate_str_ids & set(wn.fact_ids)
            if overlap:
                evidence = [uuid.UUID(fid) for fid in overlap]
                candidates.append((wn.node_uuid, wn.concept, evidence))

        candidates.sort(key=lambda x: len(x[2]), reverse=True)
        return candidates[:node_limit]

    # ── Text search on fact content ────────────────────────────────────

    async def search_text(self, query: str, limit: int = 30) -> list[WriteFact]:
        """Text search across fact content using full-text search (tsquery).

        Uses plainto_tsquery for word-boundary matching, avoiding substring
        false positives (e.g. 'lim' matching 'limestone' or 'limited').
        """
        ts_query = func.plainto_tsquery("english", query)
        stmt = (
            select(WriteFact)
            .where(func.to_tsvector("english", WriteFact.content).op("@@")(ts_query))
            .order_by(func.ts_rank(func.to_tsvector("english", WriteFact.content), ts_query).desc())
            .limit(limit)
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def search_trigram(
        self, query: str, threshold: float = 0.3, limit: int = 30,
    ) -> list[WriteFact]:
        """Search facts using trigram word_similarity (pg_trgm)."""
        stmt = (
            select(WriteFact)
            .where(func.word_similarity(query, WriteFact.content) >= threshold)
            .order_by(func.word_similarity(query, WriteFact.content).desc())
            .limit(limit)
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def search_text_excluding_rejected(
        self,
        query: str,
        node_id: uuid.UUID,
        limit: int = 30,
    ) -> list[WriteFact]:
        """Text search excluding facts rejected for this node.

        Uses plainto_tsquery for word-boundary matching.
        """
        rejected_subq = (
            select(WriteNodeFactRejection.fact_id)
            .where(WriteNodeFactRejection.node_id == node_id)
            .subquery()
        )
        ts_query = func.plainto_tsquery("english", query)
        stmt = (
            select(WriteFact)
            .where(
                func.to_tsvector("english", WriteFact.content).op("@@")(ts_query),
                WriteFact.id.notin_(select(rejected_subq.c.fact_id)),
            )
            .order_by(func.ts_rank(func.to_tsvector("english", WriteFact.content), ts_query).desc())
            .limit(limit)
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    # ── Fact rejection tracking ────────────────────────────────────────

    async def record_fact_rejection(
        self, node_id: uuid.UUID, fact_id: uuid.UUID,
    ) -> bool:
        """Record that a fact was rejected for a node. Returns True if new."""
        stmt = (
            pg_insert(WriteNodeFactRejection)
            .values(id=uuid.uuid4(), node_id=node_id, fact_id=fact_id)
            .on_conflict_do_nothing(index_elements=["node_id", "fact_id"])
        )
        result = await self._session.execute(stmt)
        return result.rowcount > 0  # type: ignore[return-value]

    async def get_rejected_fact_ids(self, node_id: uuid.UUID) -> set[uuid.UUID]:
        stmt = select(WriteNodeFactRejection.fact_id).where(
            WriteNodeFactRejection.node_id == node_id
        )
        result = await self._session.execute(stmt)
        return {row[0] for row in result.all()}

