import uuid
from datetime import timedelta

from sqlalchemy import case, func, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import InstrumentedAttribute

from kt_db.models import Edge, Node, NodeCounter, NodeFact, _utcnow


def _exact_match_order(column: InstrumentedAttribute, query: str):  # noqa: ANN201
    """CASE expression that sorts exact matches (case-insensitive) first."""
    return case((func.lower(column) == func.lower(query), 0), else_=1)


class NodeRepository:
    """Repository for Node CRUD operations."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self,
        concept: str,
        attractor: str | None = None,
        filter_id: str | None = None,
        max_content_tokens: int = 500,
        node_type: str = "concept",
        parent_id: uuid.UUID | None = None,
        source_concept_id: uuid.UUID | None = None,
        metadata_: dict | None = None,
        node_id: uuid.UUID | None = None,
        entity_subtype: str | None = None,
    ) -> Node:
        """Create a new node. Uses node_id if provided, otherwise uuid4."""
        node = Node(
            id=node_id or uuid.uuid4(),
            concept=concept,
            attractor=attractor,
            filter_id=filter_id,
            max_content_tokens=max_content_tokens,
            node_type=node_type,
            parent_id=parent_id,
            source_concept_id=source_concept_id,
            metadata_=metadata_,
            entity_subtype=entity_subtype,
        )
        self._session.add(node)
        await self._session.flush()
        return node

    async def get_by_id(self, node_id: uuid.UUID) -> Node | None:
        """Find a Node by its ID."""
        result = await self._session.execute(select(Node).where(Node.id == node_id))
        return result.scalar_one_or_none()

    async def get_by_ids(self, node_ids: list[uuid.UUID]) -> list[Node]:
        """Find multiple Nodes by their IDs."""
        if not node_ids:
            return []
        result = await self._session.execute(select(Node).where(Node.id.in_(node_ids)))
        return list(result.scalars().all())

    async def search_by_concept(
        self,
        query: str,
        limit: int = 10,
        node_type: str | None = None,
    ) -> list[Node]:
        """Text search by concept name using pg_trgm similarity.

        Uses word_similarity() which handles partial matches well -- e.g.
        a query "economic impacts of artificial intelligence" will match
        a node named "artificial intelligence" because the best matching
        substring scores high.

        Results are ranked with exact-match priority, then by similarity,
        then shorter concepts first as tiebreaker.

        Falls back to ILIKE if trigram returns no results (handles exact
        substring matches that trigram might miss at low similarity).

        When *node_type* is provided, only nodes of that type are returned.
        """
        # Tier 1: trigram word_similarity (fuzzy, handles NL queries)
        threshold = 0.25
        # Exact matches first (case-insensitive), then by similarity desc,
        # then shorter concepts first as tiebreaker (so "electricity" ranks
        # above "electricity in the body" when both score equally).
        stmt = (
            select(Node)
            .where(func.word_similarity(query, Node.concept) >= threshold)
            .order_by(
                _exact_match_order(Node.concept, query),
                func.word_similarity(query, Node.concept).desc(),
                func.length(Node.concept).asc(),
            )
        )
        if node_type is not None:
            stmt = stmt.where(Node.node_type == node_type)
        stmt = stmt.limit(limit)
        result = await self._session.execute(stmt)
        nodes = list(result.scalars().all())

        if nodes:
            return nodes

        # Tier 2 fallback: ILIKE for exact substring matches
        escaped = query.replace("\\", "\\\\").replace("%", r"\%").replace("_", r"\_")
        stmt2 = select(Node).where(Node.concept.ilike(f"%{escaped}%"))
        if node_type is not None:
            stmt2 = stmt2.where(Node.node_type == node_type)
        stmt2 = stmt2.limit(limit)
        result2 = await self._session.execute(stmt2)
        return list(result2.scalars().all())

    async def increment_access_count(self, node_id: uuid.UUID) -> None:
        """Increment the access_count via the node_counters table.

        Uses INSERT ON CONFLICT DO UPDATE to avoid locking the nodes row.
        """
        stmt = (
            pg_insert(NodeCounter)
            .values(node_id=node_id, access_count=1, update_count=0)
            .on_conflict_do_update(
                index_elements=[NodeCounter.node_id],
                set_={"access_count": NodeCounter.access_count + 1},
            )
        )
        await self._session.execute(stmt)

    async def increment_update_count(self, node_id: uuid.UUID) -> None:
        """Increment the update_count via the node_counters table.

        Uses INSERT ON CONFLICT DO UPDATE to avoid locking the nodes row.
        """
        stmt = (
            pg_insert(NodeCounter)
            .values(node_id=node_id, access_count=0, update_count=1)
            .on_conflict_do_update(
                index_elements=[NodeCounter.node_id],
                set_={"update_count": NodeCounter.update_count + 1},
            )
        )
        await self._session.execute(stmt)

    async def get_counters(self, node_id: uuid.UUID) -> tuple[int, int]:
        """Get (access_count, update_count) for a node."""
        stmt = select(NodeCounter.access_count, NodeCounter.update_count).where(
            NodeCounter.node_id == node_id,
        )
        result = await self._session.execute(stmt)
        row = result.one_or_none()
        if row is None:
            return (0, 0)
        return (row[0], row[1])

    async def acquire_node_lock(self, concept: str) -> None:
        """Acquire a transaction-scoped advisory lock keyed by concept name hash.

        Used to serialize concurrent creation of the same concept.
        The lock is automatically released when the transaction commits or rolls back.
        """
        # Use a deterministic hash of the concept name as the lock key
        lock_key = hash(concept.lower().strip()) & 0x7FFFFFFF  # positive 32-bit int
        await self._session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": lock_key})

    async def update_fields(self, node_id: uuid.UUID, **kwargs: object) -> None:
        """Update arbitrary fields on a node."""
        if kwargs:
            await self._session.execute(update(Node).where(Node.id == node_id).values(**kwargs))

    async def list_paginated(
        self,
        offset: int = 0,
        limit: int = 20,
        search: str | None = None,
        node_type: str | None = None,
        sort: str = "updated_at",
    ) -> list[Node]:
        """List nodes with pagination and optional search/node_type filter.

        sort: "updated_at" (default) or "edge_count" (most connected first).
        """
        if sort == "edge_count":
            # Count edges where node is source or target, order by total desc
            source_count = (
                select(Edge.source_node_id.label("node_id"), func.count(Edge.id).label("cnt"))
                .group_by(Edge.source_node_id)
                .subquery()
            )
            target_count = (
                select(Edge.target_node_id.label("node_id"), func.count(Edge.id).label("cnt"))
                .group_by(Edge.target_node_id)
                .subquery()
            )
            edge_total = func.coalesce(source_count.c.cnt, 0) + func.coalesce(target_count.c.cnt, 0)
            stmt = (
                select(Node)
                .outerjoin(source_count, Node.id == source_count.c.node_id)
                .outerjoin(target_count, Node.id == target_count.c.node_id)
                .order_by(edge_total.desc(), Node.updated_at.desc())
            )
        elif sort == "fact_count":
            # Count facts linked to each node via node_facts junction table
            fact_count_sub = (
                select(NodeFact.node_id.label("node_id"), func.count(NodeFact.fact_id).label("cnt"))
                .group_by(NodeFact.node_id)
                .subquery()
            )
            stmt = (
                select(Node)
                .outerjoin(fact_count_sub, Node.id == fact_count_sub.c.node_id)
                .order_by(func.coalesce(fact_count_sub.c.cnt, 0).desc(), Node.updated_at.desc())
            )
        else:
            stmt = select(Node).order_by(Node.updated_at.desc())
        if search:
            stmt = stmt.where(Node.concept.ilike(f"%{search}%"))
        if node_type:
            stmt = stmt.where(Node.node_type == node_type)
        stmt = stmt.offset(offset).limit(limit)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def count(self, search: str | None = None, node_type: str | None = None) -> int:
        """Count total nodes, optionally filtered by search and/or node_type."""
        stmt = select(func.count(Node.id))
        if search:
            stmt = stmt.where(Node.concept.ilike(f"%{search}%"))
        if node_type:
            stmt = stmt.where(Node.node_type == node_type)
        result = await self._session.execute(stmt)
        return result.scalar_one()

    async def delete(self, node_id: uuid.UUID) -> bool:
        """Delete a node by ID. Returns True if deleted, False if not found."""
        node = await self.get_by_id(node_id)
        if node is None:
            return False
        await self._session.delete(node)
        await self._session.flush()
        return True

    async def search_by_type(self, node_type: str, limit: int = 20) -> list[Node]:
        """Get nodes filtered by node_type."""
        stmt = select(Node).where(Node.node_type == node_type).order_by(Node.updated_at.desc()).limit(limit)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def get_perspectives_for_concept(self, concept_node_id: uuid.UUID) -> list[Node]:
        """Get all perspective nodes whose source_concept_id matches."""
        stmt = (
            select(Node)
            .where(Node.source_concept_id == concept_node_id, Node.node_type == "perspective")
            .order_by(Node.updated_at.desc())
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def count_children(self, parent_id: uuid.UUID) -> int:
        """Count the number of child nodes for a given parent."""
        stmt = select(func.count(Node.id)).where(Node.parent_id == parent_id)
        result = await self._session.execute(stmt)
        return result.scalar_one()

    async def get_children(self, parent_id: uuid.UUID) -> list[Node]:
        """Get all child nodes of a given parent."""
        stmt = select(Node).where(Node.parent_id == parent_id).order_by(Node.updated_at.desc())
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def list_all(self) -> list[Node]:
        """Return all nodes ordered by updated_at descending."""
        stmt = select(Node).order_by(Node.updated_at.desc())
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def search_by_trigram(
        self,
        query: str,
        threshold: float = 0.3,
        limit: int = 5,
        node_type: str | None = None,
    ) -> list[Node]:
        """Search nodes by concept name using pg_trgm similarity.

        Results are ranked by similarity with exact-match priority and
        shorter-concept tiebreaker, avoiding the problem where long
        compound concepts crowd out the exact target.
        """
        stmt = (
            select(Node)
            .where(func.similarity(Node.concept, query) >= threshold)
            .order_by(
                _exact_match_order(Node.concept, query),
                func.similarity(Node.concept, query).desc(),
                func.length(Node.concept).asc(),
            )
        )
        if node_type is not None:
            stmt = stmt.where(Node.node_type == node_type)
        stmt = stmt.limit(limit)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def get_stale_nodes(self, max_age_days: int = 30, limit: int = 20) -> list[Node]:
        """Get nodes whose updated_at + stale_after < now (i.e. overdue for refresh)."""
        now = _utcnow()
        # stale_after is in days; compare updated_at + interval(stale_after days) < now
        # Using a simpler approach: filter where updated_at < now - max_age_days
        cutoff = now - timedelta(days=max_age_days)
        stmt = select(Node).where(Node.updated_at < cutoff).order_by(Node.updated_at.asc()).limit(limit)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())
