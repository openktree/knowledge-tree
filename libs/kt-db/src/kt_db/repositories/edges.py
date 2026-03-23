import logging
import uuid
from datetime import timedelta

from sqlalchemy import ColumnElement, and_, func, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from kt_config.types import canonicalize_edge_ids
from kt_db.models import Edge, EdgeFact, Fact, Node, _utcnow

logger = logging.getLogger(__name__)


class EdgeRepository:
    """Repository for Edge CRUD with graph traversal queries."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_id(self, edge_id: uuid.UUID) -> Edge | None:
        """Find an edge by its ID with edge_facts eagerly loaded."""
        result = await self._session.execute(
            select(Edge).options(selectinload(Edge.edge_facts)).where(Edge.id == edge_id)
        )
        return result.scalar_one_or_none()

    async def list_paginated(
        self,
        offset: int = 0,
        limit: int = 20,
        relationship_type: str | None = None,
        node_id: uuid.UUID | None = None,
        search: str | None = None,
    ) -> list[Edge]:
        """List edges with pagination and optional filters."""
        stmt = select(Edge).options(selectinload(Edge.edge_facts)).order_by(Edge.created_at.desc())
        if relationship_type:
            stmt = stmt.where(Edge.relationship_type == relationship_type)
        if node_id:
            stmt = stmt.where(or_(Edge.source_node_id == node_id, Edge.target_node_id == node_id))
        if search:
            stmt = stmt.where(Edge.justification.ilike(f"%{search}%"))
        stmt = stmt.offset(offset).limit(limit)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def count(
        self,
        relationship_type: str | None = None,
        node_id: uuid.UUID | None = None,
        search: str | None = None,
    ) -> int:
        """Count total edges, optionally filtered."""
        stmt = select(func.count(Edge.id))
        if relationship_type:
            stmt = stmt.where(Edge.relationship_type == relationship_type)
        if node_id:
            stmt = stmt.where(or_(Edge.source_node_id == node_id, Edge.target_node_id == node_id))
        if search:
            stmt = stmt.where(Edge.justification.ilike(f"%{search}%"))
        result = await self._session.execute(stmt)
        return result.scalar_one()

    async def list_all(self) -> list[Edge]:
        """Return all edges with edge_facts eagerly loaded."""
        stmt = select(Edge).options(selectinload(Edge.edge_facts)).order_by(Edge.created_at.desc())
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def delete(self, edge_id: uuid.UUID) -> bool:
        """Delete an edge by ID. Returns True if deleted, False if not found."""
        result = await self._session.execute(select(Edge).where(Edge.id == edge_id))
        edge = result.scalar_one_or_none()
        if edge is None:
            return False
        await self._session.delete(edge)
        await self._session.flush()
        return True

    async def get_recent_edge_pairs(
        self,
        node_id: uuid.UUID,
        candidate_ids: list[uuid.UUID],
        staleness_days: int = 30,
    ) -> set[uuid.UUID]:
        """Return candidate node IDs that already have an edge
        updated within staleness_days. These should be skipped."""
        if not candidate_ids:
            return set()
        cutoff = _utcnow() - timedelta(days=staleness_days)
        stmt = select(Edge).where(
            Edge.updated_at >= cutoff,
            or_(
                and_(Edge.source_node_id == node_id, Edge.target_node_id.in_(candidate_ids)),
                and_(Edge.target_node_id == node_id, Edge.source_node_id.in_(candidate_ids)),
            ),
        )
        result = await self._session.execute(stmt)
        recent: set[uuid.UUID] = set()
        for edge in result.scalars().all():
            other = edge.target_node_id if edge.source_node_id == node_id else edge.source_node_id
            recent.add(other)
        return recent

    async def get_edge_for_pair(
        self,
        node_a: uuid.UUID,
        node_b: uuid.UUID,
    ) -> Edge | None:
        """Find the strongest edge between two nodes (canonical ordering)."""
        lo, hi = (node_a, node_b) if node_a < node_b else (node_b, node_a)
        stmt = (
            select(Edge)
            .where(
                or_(
                    and_(Edge.source_node_id == lo, Edge.target_node_id == hi),
                    and_(Edge.source_node_id == hi, Edge.target_node_id == lo),
                ),
            )
            .order_by(func.abs(Edge.weight).desc())
            .limit(1)
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def create(
        self,
        source_node_id: uuid.UUID,
        target_node_id: uuid.UUID,
        relationship_type: str,
        weight: float = 0.5,
        created_by_query: uuid.UUID | None = None,
        metadata: dict[str, object] | None = None,
        justification: str | None = None,
    ) -> Edge:
        """Create a new edge or update existing one (upsert by source/target/type).

        Uses INSERT ... ON CONFLICT DO UPDATE on the unique constraint
        ``uq_edge_source_target_type`` to avoid race conditions.

        All edges are canonicalized (smaller UUID = source) so reverse
        duplicates are impossible.
        """
        source_node_id, target_node_id = canonicalize_edge_ids(
            source_node_id,
            target_node_id,
            relationship_type,
        )

        now = _utcnow()
        edge_id = uuid.uuid4()

        update_set: dict[str, object] = {
            "weight": weight,
            "updated_at": now,
        }
        if metadata is not None:
            update_set["metadata"] = metadata
        if justification is not None:
            update_set["justification"] = justification

        stmt = (
            pg_insert(Edge)
            .values(
                id=edge_id,
                source_node_id=source_node_id,
                target_node_id=target_node_id,
                relationship_type=relationship_type,
                weight=weight,
                created_by_query=created_by_query,
                metadata_=metadata,
                justification=justification,
                created_at=now,
                updated_at=now,
            )
            .on_conflict_do_update(
                constraint="uq_edge_source_target_type",
                set_=update_set,
            )
            .returning(Edge.id)
        )
        result = await self._session.execute(stmt)
        returned_id = result.scalar_one()

        # The upsert may have updated an existing row whose ORM instance is
        # already tracked in the session's identity map with stale attribute
        # values.  We need to expunge it so that get_by_id re-loads a fresh
        # instance from the database rather than returning the stale one.
        existing = await self._session.get(Edge, returned_id)  # type: ignore[arg-type]
        if existing is not None:
            self._session.expire(existing)

        # Fetch the full ORM object with eager-loaded relationships
        edge = await self.get_by_id(returned_id)
        assert edge is not None  # noqa: S101
        return edge

    async def get_edge(
        self,
        source_id: uuid.UUID,
        target_id: uuid.UUID,
        rel_type: str,
    ) -> Edge | None:
        """Find a specific edge by source, target, and relationship type.

        For undirected edge types the IDs are canonicalized so the lookup
        succeeds regardless of argument order.
        """
        source_id, target_id = canonicalize_edge_ids(source_id, target_id, rel_type)
        stmt = select(Edge).where(
            Edge.source_node_id == source_id,
            Edge.target_node_id == target_id,
            Edge.relationship_type == rel_type,
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_edges(
        self,
        node_id: uuid.UUID,
        direction: str = "both",
        types: list[str] | None = None,
    ) -> list[Edge]:
        """Get edges connected to a node.

        Args:
            node_id: The node to query edges for.
            direction: "outgoing", "incoming", or "both".
            types: Optional list of relationship types to filter by.

        Returns:
            List of edges matching the criteria.
        """
        conditions: list[ColumnElement[bool]] = []

        if direction == "outgoing":
            conditions.append(Edge.source_node_id == node_id)
        elif direction == "incoming":
            conditions.append(Edge.target_node_id == node_id)
        else:  # both
            conditions.append(or_(Edge.source_node_id == node_id, Edge.target_node_id == node_id))

        if types:
            conditions.append(Edge.relationship_type.in_(types))

        stmt = select(Edge).options(selectinload(Edge.edge_facts)).where(*conditions)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def get_neighbors(
        self,
        node_id: uuid.UUID,
        depth: int = 1,
        types: list[str] | None = None,
    ) -> list[Node]:
        """Get neighboring nodes up to a given depth.

        Args:
            node_id: The starting node.
            depth: How many hops to traverse (default 1).
            types: Optional edge type filter.

        Returns:
            List of neighbor Node objects (excluding the starting node).
        """
        visited: set[uuid.UUID] = {node_id}
        frontier: set[uuid.UUID] = {node_id}
        all_neighbor_ids: set[uuid.UUID] = set()

        for _ in range(depth):
            if not frontier:
                break
            next_frontier: set[uuid.UUID] = set()
            for fid in frontier:
                edges = await self.get_edges(fid, direction="both", types=types)
                for edge in edges:
                    neighbor_id = edge.target_node_id if edge.source_node_id == fid else edge.source_node_id
                    if neighbor_id not in visited:
                        visited.add(neighbor_id)
                        next_frontier.add(neighbor_id)
                        all_neighbor_ids.add(neighbor_id)
            frontier = next_frontier

        if not all_neighbor_ids:
            return []

        stmt = select(Node).where(Node.id.in_(all_neighbor_ids))
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def link_fact_to_edge(
        self,
        edge_id: uuid.UUID,
        fact_id: uuid.UUID,
        relevance_score: float = 1.0,
    ) -> EdgeFact | None:
        """Link a fact to an edge. Returns None if link already exists."""
        stmt = select(EdgeFact).where(EdgeFact.edge_id == edge_id, EdgeFact.fact_id == fact_id)
        result = await self._session.execute(stmt)
        if result.scalar_one_or_none() is not None:
            return None

        link = EdgeFact(edge_id=edge_id, fact_id=fact_id, relevance_score=relevance_score)
        self._session.add(link)
        await self._session.flush()
        return link

    async def get_edge_facts(self, edge_id: uuid.UUID) -> list[Fact]:
        """Get all facts linked to an edge with sources eagerly loaded."""
        from kt_db.models import FactSource

        stmt = (
            select(Fact)
            .join(EdgeFact, EdgeFact.fact_id == Fact.id)
            .where(EdgeFact.edge_id == edge_id)
            .options(selectinload(Fact.sources).selectinload(FactSource.raw_source))
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def delete_non_structural_edges(self, node_id: uuid.UUID) -> int:
        """Delete all edges for a node. Returns count deleted."""
        edges = await self.get_edges(node_id, direction="both")
        count = 0
        for edge in edges:
            await self._session.delete(edge)
            count += 1
        if count:
            await self._session.flush()
        return count
