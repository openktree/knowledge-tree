import logging
import uuid
from sqlalchemy import func, literal_column, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from kt_db.models import Fact, FactSource, Node, NodeFact, NodeFactRejection, RawSource

logger = logging.getLogger(__name__)


class FactRepository:
    """Repository for Fact CRUD operations."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self,
        content: str,
        fact_type: str,
        metadata: dict | None = None,
    ) -> Fact:
        """Create a new Fact record."""
        fact = Fact(
            id=uuid.uuid4(),
            content=content,
            fact_type=fact_type,
            metadata_=metadata,
        )
        self._session.add(fact)
        await self._session.flush()
        return fact

    async def get_by_id(self, fact_id: uuid.UUID) -> Fact | None:
        """Find a Fact by its ID."""
        result = await self._session.execute(select(Fact).where(Fact.id == fact_id))
        return result.scalar_one_or_none()

    async def link_to_node(
        self,
        node_id: uuid.UUID,
        fact_id: uuid.UUID,
        relevance_score: float = 1.0,
        stance: str | None = None,
    ) -> NodeFact | None:
        """Link a fact to a node via the NodeFact association.

        Uses INSERT ... ON CONFLICT DO NOTHING on ``uq_node_fact`` to
        handle duplicate links without savepoints.

        Returns the NodeFact if created, or None if the link already existed.
        """
        stmt = (
            pg_insert(NodeFact)
            .values(
                node_id=node_id,
                fact_id=fact_id,
                relevance_score=relevance_score,
                stance=stance,
            )
            .on_conflict_do_nothing(constraint="uq_node_fact")
        )
        result = await self._session.execute(stmt)
        if result.rowcount == 0:  # type: ignore[union-attr]
            logger.debug("Node-fact link already exists: node=%s fact=%s", node_id, fact_id)
            return None
        # Return a transient object -- caller rarely needs the ORM object
        return NodeFact(
            node_id=node_id,
            fact_id=fact_id,
            relevance_score=relevance_score,
            stance=stance,
        )

    async def unlink_from_node(self, node_id: uuid.UUID, fact_id: uuid.UUID) -> bool:
        """Remove a fact-to-node link. Returns True if a row was deleted."""
        from sqlalchemy import delete

        stmt = delete(NodeFact).where(
            NodeFact.node_id == node_id, NodeFact.fact_id == fact_id,
        )
        result = await self._session.execute(stmt)
        return result.rowcount > 0  # type: ignore[union-attr]

    async def get_fact_ids_for_nodes(
        self, node_ids: list[uuid.UUID],
    ) -> dict[uuid.UUID, set[uuid.UUID]]:
        """Return {node_id: set(fact_id)} for the given nodes in a single query."""
        if not node_ids:
            return {}
        stmt = select(NodeFact.node_id, NodeFact.fact_id).where(
            NodeFact.node_id.in_(node_ids),
        )
        result = await self._session.execute(stmt)
        mapping: dict[uuid.UUID, set[uuid.UUID]] = {nid: set() for nid in node_ids}
        for row in result:
            mapping[row[0]].add(row[1])
        return mapping

    async def get_nodes_for_fact(self, fact_id: uuid.UUID) -> list[tuple[Node, NodeFact]]:
        """Get all nodes linked to a given fact with their link metadata."""
        stmt = (
            select(Node, NodeFact)
            .join(NodeFact, Node.id == NodeFact.node_id)
            .where(NodeFact.fact_id == fact_id)
            .order_by(NodeFact.relevance_score.desc())
        )
        result = await self._session.execute(stmt)
        return [(row[0], row[1]) for row in result.all()]

    async def get_facts_by_node(self, node_id: uuid.UUID) -> list[Fact]:
        """Get all facts linked to a given node."""
        stmt = select(Fact).join(NodeFact, Fact.id == NodeFact.fact_id).where(NodeFact.node_id == node_id)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def get_facts_by_type(self, fact_type: str) -> list[Fact]:
        """Get all facts of a given type."""
        result = await self._session.execute(select(Fact).where(Fact.fact_type == fact_type))
        return list(result.scalars().all())

    async def list_paginated(
        self,
        offset: int = 0,
        limit: int = 20,
        search: str | None = None,
        fact_type: str | None = None,
        author_org: str | None = None,
        source_domain: str | None = None,
    ) -> list[Fact]:
        """List facts with pagination and optional filters.

        Args:
            offset: Number of facts to skip.
            limit: Max facts to return.
            search: Filter by fact content (ILIKE).
            fact_type: Filter by fact type.
            author_org: Filter by author organization (ILIKE on FactSource.author_org).
            source_domain: Filter by source domain (ILIKE on RawSource.uri).
        """
        needs_source_join = author_org or source_domain
        stmt = select(Fact).order_by(Fact.created_at.desc())
        if needs_source_join:
            stmt = stmt.join(FactSource, Fact.id == FactSource.fact_id)
            stmt = stmt.join(RawSource, FactSource.raw_source_id == RawSource.id)
        if search:
            stmt = stmt.where(Fact.content.ilike(f"%{search}%"))
        if fact_type:
            stmt = stmt.where(Fact.fact_type == fact_type)
        if author_org:
            stmt = stmt.where(FactSource.author_org.ilike(f"%{author_org}%"))
        if source_domain:
            stmt = stmt.where(RawSource.uri.ilike(f"%{source_domain}%"))
        stmt = stmt.offset(offset).limit(limit)
        result = await self._session.execute(stmt)
        return list(result.scalars().unique().all())

    async def count(
        self,
        search: str | None = None,
        fact_type: str | None = None,
        author_org: str | None = None,
        source_domain: str | None = None,
    ) -> int:
        """Count total facts, optionally filtered."""
        needs_source_join = author_org or source_domain
        stmt = select(func.count(func.distinct(Fact.id)))
        if needs_source_join:
            stmt = stmt.select_from(Fact)
            stmt = stmt.join(FactSource, Fact.id == FactSource.fact_id)
            stmt = stmt.join(RawSource, FactSource.raw_source_id == RawSource.id)
        if search:
            stmt = stmt.where(Fact.content.ilike(f"%{search}%"))
        if fact_type:
            stmt = stmt.where(Fact.fact_type == fact_type)
        if author_org:
            stmt = stmt.where(FactSource.author_org.ilike(f"%{author_org}%"))
        if source_domain:
            stmt = stmt.where(RawSource.uri.ilike(f"%{source_domain}%"))
        result = await self._session.execute(stmt)
        return result.scalar_one()

    async def update_fields(self, fact_id: uuid.UUID, **kwargs: object) -> None:
        """Update arbitrary fields on a fact."""
        if kwargs:
            await self._session.execute(update(Fact).where(Fact.id == fact_id).values(**kwargs))

    async def delete(self, fact_id: uuid.UUID) -> bool:
        """Delete a fact by ID. Returns True if deleted, False if not found."""
        fact = await self.get_by_id(fact_id)
        if fact is None:
            return False
        await self._session.delete(fact)
        await self._session.flush()
        return True

    async def get_facts_by_node_with_sources(self, node_id: uuid.UUID) -> list[Fact]:
        """Get all facts linked to a node with sources eagerly loaded."""
        stmt = (
            select(Fact)
            .join(NodeFact, Fact.id == NodeFact.fact_id)
            .where(NodeFact.node_id == node_id)
            .options(selectinload(Fact.sources).selectinload(FactSource.raw_source))
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def get_by_id_with_sources(self, fact_id: uuid.UUID) -> Fact | None:
        """Find a Fact by ID with sources eagerly loaded."""
        stmt = (
            select(Fact)
            .where(Fact.id == fact_id)
            .options(selectinload(Fact.sources).selectinload(FactSource.raw_source))
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_ids_with_sources(self, fact_ids: list[uuid.UUID]) -> list[Fact]:
        """Load multiple facts by ID with sources eagerly loaded."""
        if not fact_ids:
            return []
        stmt = (
            select(Fact)
            .where(Fact.id.in_(fact_ids))
            .options(selectinload(Fact.sources).selectinload(FactSource.raw_source))
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def create_fact_source(
        self,
        fact_id: uuid.UUID,
        raw_source_id: uuid.UUID,
        context_snippet: str | None = None,
        attribution: str | None = None,
        author_person: str | None = None,
        author_org: str | None = None,
    ) -> FactSource | None:
        """Create a FactSource linking a fact to a raw source.

        Uses INSERT ... ON CONFLICT DO NOTHING on ``uq_fact_source``
        to handle duplicates without savepoints.

        Returns the FactSource if created, or None if the link already existed.
        """
        new_id = uuid.uuid4()
        values: dict = {
            "id": new_id,
            "fact_id": fact_id,
            "raw_source_id": raw_source_id,
            "context_snippet": context_snippet,
            "attribution": attribution,
        }
        if author_person is not None:
            values["author_person"] = author_person
        if author_org is not None:
            values["author_org"] = author_org
        stmt = (
            pg_insert(FactSource)
            .values(**values)
            .on_conflict_do_nothing(constraint="uq_fact_source")
        )
        result = await self._session.execute(stmt)
        if result.rowcount == 0:  # type: ignore[union-attr]
            logger.debug("Fact-source link already exists: fact=%s source=%s", fact_id, raw_source_id)
            return None
        # Increment the cached fact_count on the RawSource
        await self._session.execute(
            update(RawSource)
            .where(RawSource.id == raw_source_id)
            .values(fact_count=RawSource.fact_count + 1)
        )
        return FactSource(
            id=new_id,
            fact_id=fact_id,
            raw_source_id=raw_source_id,
            context_snippet=context_snippet,
            attribution=attribution,
            author_person=author_person,
            author_org=author_org,
        )

    async def search_fact_pool_text(self, query: str, limit: int = 30) -> list[Fact]:
        """Text search across fact content using full-text search (tsquery).

        Uses plainto_tsquery for word-boundary matching, avoiding substring
        false positives (e.g. 'lim' matching 'limestone' or 'limited').
        """
        ts_query = func.plainto_tsquery("english", query)
        stmt = (
            select(Fact)
            .where(func.to_tsvector("english", Fact.content).op("@@")(ts_query))
            .order_by(func.ts_rank(func.to_tsvector("english", Fact.content), ts_query).desc())
            .limit(limit)
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def list_all_with_sources(self) -> list[Fact]:
        """Return all facts with eagerly-loaded sources, ordered by created_at desc."""
        stmt = (
            select(Fact)
            .order_by(Fact.created_at.desc())
            .options(selectinload(Fact.sources).selectinload(FactSource.raw_source))
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def get_facts_by_node_with_stance(self, node_id: uuid.UUID) -> list[tuple[Fact, str | None]]:
        """Get all facts linked to a node along with their stance classification.

        Returns list of (Fact, stance) tuples where stance may be None.
        """
        stmt = (
            select(Fact, NodeFact.stance)
            .join(NodeFact, Fact.id == NodeFact.fact_id)
            .where(NodeFact.node_id == node_id)
        )
        result = await self._session.execute(stmt)
        return [(row[0], row[1]) for row in result.all()]

    async def find_nodes_sharing_facts(
        self,
        node_id: uuid.UUID,
        limit: int = 20,
    ) -> list[tuple[uuid.UUID, str, list[uuid.UUID]]]:
        """Find nodes that share facts with the given node.

        Uses the node_facts join table: finds facts linked to *node_id*, then
        reverse-looks up other nodes referencing the SAME facts.

        Returns:
            List of (other_node_id, other_concept, [evidence_fact_ids])
            sorted by evidence fact count descending.
        """
        # Subquery: fact IDs linked to the source node
        source_facts = (
            select(NodeFact.fact_id)
            .where(NodeFact.node_id == node_id)
            .subquery()
        )

        # Find other nodes sharing those facts, grouped
        stmt = (
            select(
                NodeFact.node_id,
                Node.concept,
                func.array_agg(NodeFact.fact_id).label("shared_fact_ids"),
                func.count(NodeFact.fact_id).label("shared_count"),
            )
            .join(Node, Node.id == NodeFact.node_id)
            .where(
                NodeFact.fact_id.in_(select(source_facts.c.fact_id)),
                NodeFact.node_id != node_id,
            )
            .group_by(NodeFact.node_id, Node.concept)
            .order_by(literal_column("shared_count").desc())
            .limit(limit)
        )
        result = await self._session.execute(stmt)
        return [
            (row[0], row[1], list(row[2]))
            for row in result.all()
        ]

    async def search_fact_pool_trigram(
        self,
        query: str,
        threshold: float = 0.3,
        limit: int = 30,
    ) -> list[Fact]:
        """Search facts using trigram word_similarity (pg_trgm).

        Uses word_similarity() which finds the best-matching substring,
        ideal for short queries against long fact content.
        """
        stmt = (
            select(Fact)
            .where(func.word_similarity(query, Fact.content) >= threshold)
            .order_by(func.word_similarity(query, Fact.content).desc())
            .limit(limit)
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def find_nodes_by_text_facts(
        self,
        query: str,
        source_node_id: uuid.UUID,
        threshold: float = 0.3,
        node_limit: int = 10,
    ) -> list[tuple[uuid.UUID, str, list[uuid.UUID]]]:
        """Find nodes that have text-matching facts using pg_trgm word_similarity.

        Finds facts where ``word_similarity(query, content) >= threshold``,
        excludes facts already linked to *source_node_id*, then reverse-looks
        up which OTHER nodes own those facts via ``node_facts``.

        Returns:
            List of (node_id, concept, [evidence_fact_ids])
            sorted by evidence fact count descending.
        """
        # Subquery: fact IDs already linked to the source node
        source_facts = (
            select(NodeFact.fact_id)
            .where(NodeFact.node_id == source_node_id)
            .subquery()
        )

        stmt = (
            select(
                NodeFact.node_id,
                Node.concept,
                func.array_agg(Fact.id.distinct()).label("evidence_fact_ids"),
                func.count(Fact.id.distinct()).label("evidence_count"),
            )
            .join(Fact, Fact.id == NodeFact.fact_id)
            .join(Node, Node.id == NodeFact.node_id)
            .where(
                func.word_similarity(query, Fact.content) >= threshold,
                Fact.id.notin_(select(source_facts.c.fact_id)),
                NodeFact.node_id != source_node_id,
            )
            .group_by(NodeFact.node_id, Node.concept)
            .order_by(literal_column("evidence_count").desc())
            .limit(node_limit)
        )

        result = await self._session.execute(stmt)
        return [
            (row[0], row[1], list(row[2]))
            for row in result.all()
        ]

    # -- Fact rejection tracking -----------------------------------------

    async def record_fact_rejection(
        self,
        node_id: uuid.UUID,
        fact_id: uuid.UUID,
    ) -> bool:
        """Record that a fact was rejected as irrelevant for a node.

        Uses INSERT ... ON CONFLICT DO NOTHING on ``uq_node_fact_rejection``
        to handle duplicates without savepoints.

        Returns True if recorded, False if already existed.
        """
        stmt = (
            pg_insert(NodeFactRejection)
            .values(
                id=uuid.uuid4(),
                node_id=node_id,
                fact_id=fact_id,
            )
            .on_conflict_do_nothing(constraint="uq_node_fact_rejection")
        )
        result = await self._session.execute(stmt)
        if result.rowcount == 0:  # type: ignore[union-attr]
            logger.debug("Fact rejection already exists: node=%s fact=%s", node_id, fact_id)
            return False
        return True

    async def get_rejected_fact_ids(self, node_id: uuid.UUID) -> set[uuid.UUID]:
        """Get all fact IDs rejected for a given node."""
        stmt = select(NodeFactRejection.fact_id).where(NodeFactRejection.node_id == node_id)
        result = await self._session.execute(stmt)
        return {row[0] for row in result.all()}

    async def search_fact_pool_text_excluding_rejected(
        self,
        query: str,
        node_id: uuid.UUID,
        limit: int = 30,
    ) -> list[Fact]:
        """Text search across fact pool, excluding facts rejected for this node.

        Uses plainto_tsquery for word-boundary matching.
        """
        rejected_subq = (
            select(NodeFactRejection.fact_id)
            .where(NodeFactRejection.node_id == node_id)
            .subquery()
        )
        ts_query = func.plainto_tsquery("english", query)
        stmt = (
            select(Fact)
            .where(
                func.to_tsvector("english", Fact.content).op("@@")(ts_query),
                Fact.id.notin_(select(rejected_subq.c.fact_id)),
            )
            .order_by(func.ts_rank(func.to_tsvector("english", Fact.content), ts_query).desc())
            .limit(limit)
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def browse_by_source(
        self,
        raw_source_ids: list[uuid.UUID],
        *,
        fact_type: str | None = None,
        unlinked_only: bool = False,
        limit: int = 20,
        offset: int = 0,
    ) -> list[Fact]:
        """Browse facts belonging to specific raw sources.

        Supports type filtering and an unlinked_only mode that excludes
        facts already attached to nodes.
        """
        if not raw_source_ids:
            return []

        stmt = (
            select(Fact)
            .join(FactSource, Fact.id == FactSource.fact_id)
            .where(FactSource.raw_source_id.in_(raw_source_ids))
        )

        if fact_type:
            stmt = stmt.where(Fact.fact_type == fact_type)

        if unlinked_only:
            linked_facts_subq = select(NodeFact.fact_id).subquery()
            stmt = stmt.where(Fact.id.notin_(select(linked_facts_subq.c.fact_id)))

        stmt = stmt.order_by(Fact.created_at.desc())

        stmt = stmt.offset(offset).limit(limit)
        result = await self._session.execute(stmt)
        return list(result.scalars().unique().all())

    async def get_node_facts_by_source(
        self,
        node_id: uuid.UUID,
        *,
        source_node_id: uuid.UUID | None = None,
        author_org: str | None = None,
        source_domain: str | None = None,
        search: str | None = None,
        fact_type: str | None = None,
        limit: int = 50,
        offset: int = 0,
        load_sources: bool = True,
    ) -> list[Fact]:
        """Get facts linked to a node, with flexible source filtering.

        Supports two complementary filtering strategies:

        1. **Node intersection** (``source_node_id``): Find facts linked
           to BOTH ``node_id`` and ``source_node_id``.  E.g. facts shared
           between "Epstein" and "CNN" — answers "what does CNN say about
           Epstein".  This is the most reliable approach because it uses
           graph structure rather than text matching.

        2. **Source metadata** (``author_org``, ``source_domain``): Filter
           by FactSource/RawSource fields via ILIKE.  Useful as a fallback
           when the source entity doesn't have its own node.

        Both can be combined (intersection + metadata filters).

        Args:
            node_id: UUID of the subject node.
            source_node_id: UUID of a second node — only facts linked to
                BOTH nodes are returned.
            author_org: Filter by author organization (ILIKE).
            source_domain: Filter by source URI domain (ILIKE).
            search: Filter by fact content (ILIKE).
            fact_type: Filter by fact type.
            limit: Max facts to return.
            offset: Number of facts to skip.
            load_sources: Eagerly load sources (default True). Set False
                when caller will batch-load sources separately.
        """
        stmt = (
            select(Fact)
            .join(NodeFact, Fact.id == NodeFact.fact_id)
            .where(NodeFact.node_id == node_id)
        )

        # Intersect with a second node's facts via EXISTS (avoids materializing full ID set)
        if source_node_id is not None:
            source_nf = NodeFact.__table__.alias("source_nf")
            stmt = stmt.where(
                select(literal_column("1"))
                .select_from(source_nf)
                .where(source_nf.c.node_id == source_node_id, source_nf.c.fact_id == Fact.id)
                .exists()
            )

        needs_source_join = author_org or source_domain
        if needs_source_join:
            stmt = stmt.join(FactSource, Fact.id == FactSource.fact_id)
            stmt = stmt.join(RawSource, FactSource.raw_source_id == RawSource.id)

        if search:
            stmt = stmt.where(Fact.content.ilike(f"%{search}%"))
        if fact_type:
            stmt = stmt.where(Fact.fact_type == fact_type)
        if author_org:
            stmt = stmt.where(FactSource.author_org.ilike(f"%{author_org}%"))
        if source_domain:
            stmt = stmt.where(RawSource.uri.ilike(f"%{source_domain}%"))

        if load_sources:
            stmt = stmt.options(selectinload(Fact.sources).selectinload(FactSource.raw_source))
        stmt = stmt.order_by(Fact.created_at.desc()).offset(offset).limit(limit)
        result = await self._session.execute(stmt)
        return list(result.scalars().unique().all())

    async def get_first_sources_for_facts(
        self, fact_ids: list[uuid.UUID],
    ) -> dict[uuid.UUID, tuple[FactSource, RawSource]]:
        """Batch-load the first FactSource + RawSource for each fact.

        Uses DISTINCT ON to pick one source per fact in a single query,
        avoiding the N+1 problem of selectinload when only the primary
        source is needed.

        Returns:
            {fact_id: (FactSource, RawSource)} for facts that have sources.
        """
        if not fact_ids:
            return {}
        stmt = (
            select(FactSource, RawSource)
            .join(RawSource, FactSource.raw_source_id == RawSource.id)
            .where(FactSource.fact_id.in_(fact_ids))
            .distinct(FactSource.fact_id)
            .order_by(FactSource.fact_id, FactSource.id)
        )
        result = await self._session.execute(stmt)
        return {row[0].fact_id: (row[0], row[1]) for row in result.all()}

    async def count_node_facts_by_source(
        self,
        node_id: uuid.UUID,
        *,
        source_node_id: uuid.UUID | None = None,
        author_org: str | None = None,
        source_domain: str | None = None,
        search: str | None = None,
        fact_type: str | None = None,
    ) -> int:
        """Count facts linked to a node with optional source filters."""
        stmt = (
            select(func.count(func.distinct(Fact.id)))
            .select_from(Fact)
            .join(NodeFact, Fact.id == NodeFact.fact_id)
            .where(NodeFact.node_id == node_id)
        )

        if source_node_id is not None:
            source_nf = NodeFact.__table__.alias("source_nf")
            stmt = stmt.where(
                select(literal_column("1"))
                .select_from(source_nf)
                .where(source_nf.c.node_id == source_node_id, source_nf.c.fact_id == Fact.id)
                .exists()
            )

        needs_source_join = author_org or source_domain
        if needs_source_join:
            stmt = stmt.join(FactSource, Fact.id == FactSource.fact_id)
            stmt = stmt.join(RawSource, FactSource.raw_source_id == RawSource.id)

        if search:
            stmt = stmt.where(Fact.content.ilike(f"%{search}%"))
        if fact_type:
            stmt = stmt.where(Fact.fact_type == fact_type)
        if author_org:
            stmt = stmt.where(FactSource.author_org.ilike(f"%{author_org}%"))
        if source_domain:
            stmt = stmt.where(RawSource.uri.ilike(f"%{source_domain}%"))

        result = await self._session.execute(stmt)
        return result.scalar_one()
