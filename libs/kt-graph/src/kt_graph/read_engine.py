from __future__ import annotations

import logging
import uuid
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING, Any, TypedDict

if TYPE_CHECKING:
    from qdrant_client import AsyncQdrantClient

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload

from kt_db.models import Dimension, Edge, EdgeFact, Fact, Node, NodeFact, NodeVersion, _utcnow
from kt_db.repositories.edges import EdgeRepository
from kt_db.repositories.facts import FactRepository
from kt_db.repositories.nodes import NodeRepository

logger = logging.getLogger(__name__)


class SubgraphResult(TypedDict):
    nodes: list[Node]
    edges: list[Edge]
    edge_fact_ids: dict[uuid.UUID, list[uuid.UUID]]


@dataclass
class PathStep:
    """A single step in a graph path."""

    node_id: uuid.UUID
    edge: Edge | None  # None for the source node (first step)


class ReadGraphEngine:
    """Read-only graph engine for API endpoints, MCP server, and synthesis agent reads.

    Reads from graph-db (+ Qdrant). NO write-db access.

    Supports two modes:
    - **API mode** (``session`` provided): repos are created once; the session
      lives for the entire request.
    - **Synthesis mode** (``session_factory`` provided): short-lived sessions
      are opened per method call to avoid holding connections during long agent runs.

    At least one of ``session`` or ``session_factory`` must be provided.
    """

    def __init__(
        self,
        session: AsyncSession | None = None,
        session_factory: async_sessionmaker[AsyncSession] | None = None,
        qdrant_client: AsyncQdrantClient | None = None,
    ) -> None:
        if session is None and session_factory is None:
            raise ValueError("ReadGraphEngine requires either session or session_factory")

        self._session = session
        self._session_factory = session_factory

        # Qdrant repositories (lazy-initialized when client is provided)
        self._qdrant_fact_repo = None
        self._qdrant_node_repo = None
        if qdrant_client is not None:
            from kt_qdrant.repositories.facts import QdrantFactRepository
            from kt_qdrant.repositories.nodes import QdrantNodeRepository

            self._qdrant_fact_repo = QdrantFactRepository(qdrant_client)
            self._qdrant_node_repo = QdrantNodeRepository(qdrant_client)

        # Cached repos for API mode (static session)
        if session is not None:
            self._node_repo: NodeRepository | None = NodeRepository(session)
            self._edge_repo: EdgeRepository | None = EdgeRepository(session)
            self._fact_repo: FactRepository | None = FactRepository(session)
        else:
            self._node_repo = None
            self._edge_repo = None
            self._fact_repo = None

    # ── Session management ────────────────────────────────────────────

    @asynccontextmanager
    async def _get_session(self) -> AsyncGenerator[AsyncSession, None]:
        """Yield a graph-db session for read operations.

        When a static session was provided, yield it directly.
        When a session_factory was provided, open a short-lived session.
        """
        if self._session is not None:
            yield self._session
        elif self._session_factory is not None:
            async with self._session_factory() as s:
                yield s
        else:
            raise RuntimeError("ReadGraphEngine requires either session or session_factory")

    def _repos_from_session(self, session: AsyncSession) -> tuple[NodeRepository, EdgeRepository, FactRepository]:
        """Return repos — cached when using a static session, fresh otherwise."""
        if self._node_repo is not None:
            return self._node_repo, self._edge_repo, self._fact_repo  # type: ignore[return-value]
        return NodeRepository(session), EdgeRepository(session), FactRepository(session)

    # ── Properties ────────────────────────────────────────────────────

    @property
    def has_write_db(self) -> bool:
        """Always False — ReadGraphEngine has no write-db access."""
        return False

    @property
    def has_graph_db(self) -> bool:
        """Always True — ReadGraphEngine always reads from graph-db."""
        return True

    # ── Internal helpers ──────────────────────────────────────────────

    async def _load_facts_preserving_order(self, fact_ids: list[uuid.UUID]) -> list[Fact]:
        """Load Fact objects by IDs, preserving the given order."""
        if not fact_ids:
            return []
        async with self._get_session() as session:
            result = await session.execute(select(Fact).where(Fact.id.in_(fact_ids)))
            facts_by_id = {f.id: f for f in result.scalars().all()}
            return [facts_by_id[fid] for fid in fact_ids if fid in facts_by_id]

    # ── Graph-db write operations (admin / test setup) ─────────────────
    # These write directly to graph-db.  Workers must NOT use these —
    # use WorkerGraphEngine instead.  These exist for:
    #   - API admin endpoints (delete, merge, update)
    #   - Integration tests that set up graph-db state

    async def create_node(self, concept: str, **kwargs: Any) -> Node:
        """Create a node directly in graph-db."""
        async with self._get_session() as session:
            node_repo, _, _ = self._repos_from_session(session)
            node = await node_repo.create(concept=concept, **kwargs)
            await session.commit()
            return node

    async def create_edge(
        self,
        source_id: uuid.UUID,
        target_id: uuid.UUID,
        rel_type: str,
        weight: float = 0.5,
        **kwargs: Any,
    ) -> Edge:
        """Create an edge directly in graph-db."""
        async with self._get_session() as session:
            _, edge_repo, _ = self._repos_from_session(session)
            edge = await edge_repo.create(
                source_node_id=source_id,
                target_node_id=target_id,
                relationship_type=rel_type,
                weight=weight,
                **kwargs,
            )
            await session.commit()
            return edge

    async def update_node(self, node_id: uuid.UUID, **kwargs: Any) -> Node:
        """Update a node's fields directly in graph-db."""
        async with self._get_session() as session:
            node_repo, _, _ = self._repos_from_session(session)
            await node_repo.update_fields(node_id, **kwargs)
            await session.commit()
            node = await node_repo.get_by_id(node_id)
            if node is None:
                raise ValueError(f"Node not found: {node_id}")
            return node

    async def update_fact(self, fact_id: uuid.UUID, **kwargs: Any) -> Fact:
        """Update a fact's fields directly in graph-db."""
        async with self._get_session() as session:
            _, _, fact_repo = self._repos_from_session(session)
            await fact_repo.update_fields(fact_id, **kwargs)
            await session.commit()
            fact = await fact_repo.get_by_id(fact_id)
            if fact is None:
                raise ValueError(f"Fact not found: {fact_id}")
            return fact

    async def add_dimension(
        self,
        node_id: uuid.UUID,
        model_id: str,
        content: str,
        confidence: float,
        suggested_concepts: list[str] | None = None,
        batch_index: int = 0,
        fact_count: int = 0,
        is_definitive: bool = False,
        fact_ids: list[uuid.UUID] | None = None,
    ) -> Dimension:
        """Add a dimension directly in graph-db."""
        async with self._get_session() as session:
            dim = Dimension(
                id=uuid.uuid4(),
                node_id=node_id,
                model_id=model_id,
                content=content,
                confidence=confidence,
                suggested_concepts=suggested_concepts,
                batch_index=batch_index,
                fact_count=fact_count,
                is_definitive=is_definitive,
            )
            session.add(dim)
            await session.flush()
            if fact_ids:
                from kt_db.models import DimensionFact

                for fid in fact_ids:
                    session.add(DimensionFact(dimension_id=dim.id, fact_id=fid))
                await session.flush()
            await session.commit()
            return dim

    async def link_fact_to_node(
        self,
        node_id: uuid.UUID,
        fact_id: uuid.UUID,
        relevance: float = 1.0,
        stance: str | None = None,
    ) -> NodeFact | None:
        """Link a fact to a node directly in graph-db."""
        async with self._get_session() as session:
            _, _, fact_repo = self._repos_from_session(session)
            result = await fact_repo.link_to_node(node_id, fact_id, relevance_score=relevance, stance=stance)
            await session.commit()
            return result

    async def save_version(self, node_id: uuid.UUID) -> NodeVersion:
        """Save a snapshot of the current node state as a new version."""
        async with self._get_session() as session:
            node_repo, _, _ = self._repos_from_session(session)
            node = await node_repo.get_by_id(node_id)
            if node is None:
                raise ValueError(f"Node not found: {node_id}")
            max_ver_stmt = select(func.coalesce(func.max(NodeVersion.version_number), 0)).where(
                NodeVersion.node_id == node_id
            )
            result = await session.execute(max_ver_stmt)
            max_ver: int = result.scalar_one()
            snapshot: dict[str, object] = {
                "concept": node.concept,
                "attractor": node.attractor,
                "filter_id": node.filter_id,
                "max_content_tokens": node.max_content_tokens,
                "update_count": node.update_count,
                "access_count": node.access_count,
            }
            version = NodeVersion(
                id=uuid.uuid4(),
                node_id=node_id,
                version_number=max_ver + 1,
                snapshot=snapshot,
            )
            session.add(version)
            await session.flush()
            await session.commit()
            return version

    async def increment_access_count(self, node_id: uuid.UUID) -> None:
        """Increment a node's access_count by 1 (best-effort, graph-db)."""
        try:
            async with self._get_session() as session:
                node_repo, _, _ = self._repos_from_session(session)
                await node_repo.increment_access_count(node_id)
        except Exception:
            logger.warning("Non-critical: failed to increment access_count for node %s", node_id)

    async def increment_update_count(self, node_id: uuid.UUID) -> None:
        """Increment a node's update_count by 1 (best-effort, graph-db)."""
        try:
            async with self._get_session() as session:
                node_repo, _, _ = self._repos_from_session(session)
                await node_repo.increment_update_count(node_id)
        except Exception:
            logger.warning("Non-critical: failed to increment update_count for node %s", node_id)

    async def set_node_definition(
        self,
        node_id: uuid.UUID,
        definition: str,
        source: str = "synthesized",
    ) -> None:
        """Set the synthesized definition for a node directly in graph-db."""
        async with self._get_session() as session:
            node_repo, _, _ = self._repos_from_session(session)
            from kt_db.models import _utcnow as _now

            await node_repo.update_fields(
                node_id,
                definition=definition,
                definition_source=source,
                definition_generated_at=_now(),
            )
            await session.commit()

    # ── Node reads ────────────────────────────────────────────────────

    async def get_node(self, node_id: uuid.UUID) -> Node | None:
        """Get a node by ID."""
        async with self._get_session() as session:
            node_repo, _, _ = self._repos_from_session(session)
            return await node_repo.get_by_id(node_id)

    async def get_nodes_by_ids(self, node_ids: list[uuid.UUID]) -> list[Node]:
        """Get multiple nodes by their IDs."""
        async with self._get_session() as session:
            node_repo, _, _ = self._repos_from_session(session)
            return await node_repo.get_by_ids(node_ids)

    async def search_nodes(
        self,
        query: str,
        limit: int = 10,
        node_type: str | None = None,
    ) -> list[Node]:
        """Search nodes by concept name (text search)."""
        async with self._get_session() as session:
            node_repo, _, _ = self._repos_from_session(session)
            return await node_repo.search_by_concept(query, limit=limit, node_type=node_type)

    async def search_nodes_by_trigram(
        self,
        query: str,
        threshold: float = 0.3,
        limit: int = 5,
        node_type: str | None = None,
    ) -> list[Node]:
        """Search nodes by concept using pg_trgm similarity."""
        async with self._get_session() as session:
            node_repo, _, _ = self._repos_from_session(session)
            return await node_repo.search_by_trigram(query, threshold=threshold, limit=limit, node_type=node_type)

    async def find_similar_nodes(
        self,
        embedding: list[float],
        threshold: float = 0.3,
        limit: int = 10,
        node_type: str | None = None,
    ) -> list[Node]:
        """Find nodes similar to the given embedding via Qdrant."""
        if self._qdrant_node_repo is None:
            logger.error("find_similar_nodes called but Qdrant node repo is not available")
            return []
        score_threshold = 1.0 - threshold
        results = await self._qdrant_node_repo.search_similar(
            embedding,
            limit=limit,
            score_threshold=score_threshold,
            node_type=node_type,
        )
        if not results:
            return []
        node_ids = [r.node_id for r in results]
        async with self._get_session() as session:
            node_repo, _, _ = self._repos_from_session(session)
            nodes = await node_repo.get_by_ids(node_ids)
        id_to_node = {n.id: n for n in nodes}
        return [id_to_node[nid] for nid in node_ids if nid in id_to_node]

    async def list_nodes(
        self,
        offset: int = 0,
        limit: int = 20,
        search: str | None = None,
        node_type: str | None = None,
        sort: str = "updated_at",
    ) -> list[Node]:
        """List nodes with pagination and optional node_type filter."""
        async with self._get_session() as session:
            node_repo, _, _ = self._repos_from_session(session)
            return await node_repo.list_paginated(
                offset=offset, limit=limit, search=search, node_type=node_type, sort=sort
            )

    async def count_nodes(self, search: str | None = None, node_type: str | None = None) -> int:
        """Count total nodes, optionally filtered by node_type."""
        async with self._get_session() as session:
            node_repo, _, _ = self._repos_from_session(session)
            return await node_repo.count(search=search, node_type=node_type)

    async def get_children(self, parent_id: uuid.UUID) -> list[Node]:
        """Get all child nodes of a given parent."""
        async with self._get_session() as session:
            node_repo, _, _ = self._repos_from_session(session)
            return await node_repo.get_children(parent_id)

    async def count_children(self, parent_id: uuid.UUID) -> int:
        """Count the number of child nodes for a given parent."""
        async with self._get_session() as session:
            node_repo, _, _ = self._repos_from_session(session)
            return await node_repo.count_children(parent_id)

    async def list_all_nodes(self) -> list[Node]:
        """Return all nodes ordered by updated_at descending."""
        async with self._get_session() as session:
            node_repo, _, _ = self._repos_from_session(session)
            return await node_repo.list_all()

    async def delete_node(self, node_id: uuid.UUID) -> bool:
        """Delete a node by ID."""
        async with self._get_session() as session:
            node_repo, _, _ = self._repos_from_session(session)
            return await node_repo.delete(node_id)

    # ── Edge reads ────────────────────────────────────────────────────

    async def get_edges(self, node_id: uuid.UUID, direction: str = "both") -> list[Edge]:
        """Get all edges connected to a node."""
        async with self._get_session() as session:
            _, edge_repo, _ = self._repos_from_session(session)
            return await edge_repo.get_edges(node_id, direction=direction)

    async def get_edge_by_id(self, edge_id: uuid.UUID) -> Edge | None:
        """Get a single edge by ID with edge_facts loaded."""
        async with self._get_session() as session:
            _, edge_repo, _ = self._repos_from_session(session)
            return await edge_repo.get_by_id(edge_id)

    async def list_edges(
        self,
        offset: int = 0,
        limit: int = 20,
        relationship_type: str | None = None,
        node_id: uuid.UUID | None = None,
        search: str | None = None,
    ) -> list[Edge]:
        """List edges with pagination and optional filters."""
        async with self._get_session() as session:
            _, edge_repo, _ = self._repos_from_session(session)
            return await edge_repo.list_paginated(
                offset=offset,
                limit=limit,
                relationship_type=relationship_type,
                node_id=node_id,
                search=search,
            )

    async def count_edges(
        self,
        relationship_type: str | None = None,
        node_id: uuid.UUID | None = None,
        search: str | None = None,
    ) -> int:
        """Count total edges, optionally filtered."""
        async with self._get_session() as session:
            _, edge_repo, _ = self._repos_from_session(session)
            return await edge_repo.count(
                relationship_type=relationship_type,
                node_id=node_id,
                search=search,
            )

    async def get_neighbors(
        self,
        node_id: uuid.UUID,
        depth: int = 1,
        types: list[str] | None = None,
    ) -> list[Node]:
        """Get neighboring nodes up to a given depth."""
        async with self._get_session() as session:
            _, edge_repo, _ = self._repos_from_session(session)
            return await edge_repo.get_neighbors(node_id, depth=depth, types=types)

    async def delete_edge(self, edge_id: uuid.UUID) -> bool:
        """Delete an edge by ID."""
        async with self._get_session() as session:
            _, edge_repo, _ = self._repos_from_session(session)
            return await edge_repo.delete(edge_id)

    async def get_edge_facts(self, edge_id: uuid.UUID) -> list[Fact]:
        """Get all facts linked to an edge."""
        async with self._get_session() as session:
            _, edge_repo, _ = self._repos_from_session(session)
            return await edge_repo.get_edge_facts(edge_id)

    async def list_all_edges(self) -> list[Edge]:
        """Return all edges with edge_facts eagerly loaded."""
        async with self._get_session() as session:
            _, edge_repo, _ = self._repos_from_session(session)
            return await edge_repo.list_all()

    # ── Subgraph and batch queries ────────────────────────────────────

    async def get_subgraph(
        self,
        node_ids: list[uuid.UUID],
        depth: int = 0,
    ) -> SubgraphResult:
        """Get a subgraph containing the specified nodes and edges between them."""
        if not node_ids:
            return SubgraphResult(nodes=[], edges=[], edge_fact_ids={})

        async with self._get_session() as session:
            all_ids = set(node_ids)

            for _ in range(depth):
                id_list = list(all_ids)
                neighbor_edge_stmt = select(Edge).where(
                    (Edge.source_node_id.in_(id_list)) | (Edge.target_node_id.in_(id_list))
                )
                neighbor_edge_result = await session.execute(neighbor_edge_stmt)
                neighbor_edges = list(neighbor_edge_result.scalars().all())

                new_ids: set[uuid.UUID] = set()
                for e in neighbor_edges:
                    new_ids.add(e.source_node_id)
                    new_ids.add(e.target_node_id)

                if not new_ids - all_ids:
                    break
                all_ids |= new_ids

            node_stmt = select(Node).where(Node.id.in_(list(all_ids))).options(selectinload(Node.convergence_report))
            node_result = await session.execute(node_stmt)
            nodes = list(node_result.scalars().all())

            parent_ids = {n.parent_id for n in nodes if n.parent_id is not None and n.parent_id not in all_ids}
            if parent_ids:
                parent_stmt = (
                    select(Node).where(Node.id.in_(list(parent_ids))).options(selectinload(Node.convergence_report))
                )
                parent_result = await session.execute(parent_stmt)
                parent_nodes = list(parent_result.scalars().all())
                nodes.extend(parent_nodes)
                all_ids |= parent_ids

            id_list_final = list(all_ids)
            edge_stmt = select(Edge).where(
                Edge.source_node_id.in_(id_list_final),
                Edge.target_node_id.in_(id_list_final),
            )
            edge_result = await session.execute(edge_stmt)
            edges = list(edge_result.scalars().all())

            # Batch-load edge->fact_id mappings
            edge_fact_ids: dict[uuid.UUID, list[uuid.UUID]] = {}
            if edges:
                edge_ids = [e.id for e in edges]
                ef_stmt = select(EdgeFact.edge_id, EdgeFact.fact_id).where(EdgeFact.edge_id.in_(edge_ids))
                ef_result = await session.execute(ef_stmt)
                for row in ef_result.all():
                    edge_fact_ids.setdefault(row.edge_id, []).append(row.fact_id)

            return SubgraphResult(nodes=nodes, edges=edges, edge_fact_ids=edge_fact_ids)

    async def get_edges_with_targets(
        self,
        node_id: uuid.UUID,
        *,
        limit: int = 50,
        offset: int = 0,
        edge_type: str | None = None,
    ) -> dict[str, Any]:
        """Get edges for a node with target-node info and edge fact counts.

        Returns ``{"edges": [...], "total": int}`` where each edge dict has
        ``edge``, ``other_node_id``, ``other_concept``, ``other_node_type``,
        and ``fact_count``.
        """
        async with self._get_session() as session:
            _, edge_repo, _ = self._repos_from_session(session)
            edges = await edge_repo.get_edges(node_id, direction="both")

            if edge_type:
                edges = [e for e in edges if e.relationship_type == edge_type]

            # Batch edge fact counts
            edge_ids = [e.id for e in edges]
            edge_fact_counts: dict[uuid.UUID, int] = {}
            if edge_ids:
                stmt = (
                    select(EdgeFact.edge_id, func.count(EdgeFact.fact_id))
                    .where(EdgeFact.edge_id.in_(edge_ids))
                    .group_by(EdgeFact.edge_id)
                )
                result = await session.execute(stmt)
                edge_fact_counts = {row[0]: row[1] for row in result.all()}

            # Sort by fact count descending
            edges.sort(key=lambda e: edge_fact_counts.get(e.id, 0), reverse=True)
            total = len(edges)
            page = edges[offset : offset + limit]

            # Batch-fetch target node info for this page
            target_ids: set[uuid.UUID] = set()
            for e in page:
                target_ids.add(e.source_node_id if e.source_node_id != node_id else e.target_node_id)

            target_nodes: dict[uuid.UUID, tuple[str, str]] = {}
            if target_ids:
                stmt = select(Node.id, Node.concept, Node.node_type).where(Node.id.in_(list(target_ids)))
                result = await session.execute(stmt)
                for row in result.all():
                    target_nodes[row.id] = (row.concept, row.node_type)

            items = []
            for e in page:
                other_id = e.target_node_id if e.source_node_id == node_id else e.source_node_id
                concept, node_type_val = target_nodes.get(other_id, ("unknown", "concept"))
                items.append(
                    {
                        "edge": e,
                        "other_node_id": other_id,
                        "other_concept": concept,
                        "other_node_type": node_type_val,
                        "fact_count": edge_fact_counts.get(e.id, 0),
                    }
                )

            return {"edges": items, "total": total}

    async def batch_get_neighbors_with_concepts(
        self,
        node_ids: list[uuid.UUID],
    ) -> dict[uuid.UUID, list[tuple[uuid.UUID, str, Edge]]]:
        """For each node, return its neighbors as ``(neighbor_id, concept, edge)``.

        Fetches all edges touching any of the given nodes in one query,
        then batch-loads the neighbor node concepts.
        """
        if not node_ids:
            return {}

        async with self._get_session() as session:
            id_set = set(node_ids)
            edge_stmt = select(Edge).where((Edge.source_node_id.in_(node_ids)) | (Edge.target_node_id.in_(node_ids)))
            edge_result = await session.execute(edge_stmt)
            edges = list(edge_result.scalars().all())

            # Collect all neighbor IDs
            neighbor_ids: set[uuid.UUID] = set()
            for e in edges:
                if e.source_node_id not in id_set:
                    neighbor_ids.add(e.source_node_id)
                if e.target_node_id not in id_set:
                    neighbor_ids.add(e.target_node_id)
                # Also include nodes in id_set that are neighbors of each other
                neighbor_ids.add(e.source_node_id)
                neighbor_ids.add(e.target_node_id)

            # Batch-fetch concepts
            concept_map: dict[uuid.UUID, str] = {}
            if neighbor_ids:
                stmt = select(Node.id, Node.concept).where(Node.id.in_(list(neighbor_ids)))
                result = await session.execute(stmt)
                concept_map = {row.id: row.concept for row in result.all()}

            # Build adjacency map
            adjacency: dict[uuid.UUID, list[tuple[uuid.UUID, str, Edge]]] = {nid: [] for nid in node_ids}
            for e in edges:
                if e.source_node_id in id_set:
                    neighbor = e.target_node_id
                    adjacency.setdefault(e.source_node_id, []).append(
                        (neighbor, concept_map.get(neighbor, "unknown"), e)
                    )
                if e.target_node_id in id_set:
                    neighbor = e.source_node_id
                    adjacency.setdefault(e.target_node_id, []).append(
                        (neighbor, concept_map.get(neighbor, "unknown"), e)
                    )

            return adjacency

    # ── Fact reads ────────────────────────────────────────────────────

    async def get_node_facts(self, node_id: uuid.UUID) -> list[Fact]:
        """Get all facts linked to a node."""
        async with self._get_session() as session:
            _, _, fact_repo = self._repos_from_session(session)
            return await fact_repo.get_facts_by_node(node_id)

    async def get_node_facts_with_sources(self, node_id: uuid.UUID) -> list[Fact]:
        """Get all facts linked to a node with sources eagerly loaded."""
        async with self._get_session() as session:
            _, _, fact_repo = self._repos_from_session(session)
            return await fact_repo.get_facts_by_node_with_sources(node_id)

    async def get_node_facts_with_stance(self, node_id: uuid.UUID) -> list[tuple[Fact, str | None]]:
        """Get all facts linked to a node with their stance classification."""
        async with self._get_session() as session:
            _, _, fact_repo = self._repos_from_session(session)
            return await fact_repo.get_facts_by_node_with_stance(node_id)

    async def get_facts_by_ids(self, fact_ids: list[uuid.UUID]) -> list[Fact]:
        """Load facts by ID, preserving order."""
        return await self._load_facts_preserving_order(fact_ids)

    async def get_fact_ids_for_nodes(
        self,
        node_ids: list[uuid.UUID],
    ) -> dict[uuid.UUID, set[uuid.UUID]]:
        """Return {node_id: set(fact_id)} for the given nodes."""
        async with self._get_session() as session:
            _, _, fact_repo = self._repos_from_session(session)
            return await fact_repo.get_fact_ids_for_nodes(node_ids)

    async def get_fact_nodes(self, fact_id: uuid.UUID) -> list[tuple[Node, NodeFact]]:
        """Get all nodes linked to a fact with their link metadata."""
        async with self._get_session() as session:
            _, _, fact_repo = self._repos_from_session(session)
            return await fact_repo.get_nodes_for_fact(fact_id)

    async def list_facts(
        self,
        offset: int = 0,
        limit: int = 20,
        search: str | None = None,
        fact_type: str | None = None,
        author_org: str | None = None,
        source_domain: str | None = None,
    ) -> list[Fact]:
        """List facts with pagination and optional source filters."""
        async with self._get_session() as session:
            _, _, fact_repo = self._repos_from_session(session)
            return await fact_repo.list_paginated(
                offset=offset,
                limit=limit,
                search=search,
                fact_type=fact_type,
                author_org=author_org,
                source_domain=source_domain,
            )

    async def count_facts(
        self,
        search: str | None = None,
        fact_type: str | None = None,
        author_org: str | None = None,
        source_domain: str | None = None,
    ) -> int:
        """Count total facts."""
        async with self._get_session() as session:
            _, _, fact_repo = self._repos_from_session(session)
            return await fact_repo.count(
                search=search,
                fact_type=fact_type,
                author_org=author_org,
                source_domain=source_domain,
            )

    async def delete_fact(self, fact_id: uuid.UUID) -> bool:
        """Delete a fact by ID."""
        async with self._get_session() as session:
            _, _, fact_repo = self._repos_from_session(session)
            return await fact_repo.delete(fact_id)

    async def list_all_facts_with_sources(self) -> list[Fact]:
        """Return all facts with eagerly-loaded sources."""
        async with self._get_session() as session:
            _, _, fact_repo = self._repos_from_session(session)
            return await fact_repo.list_all_with_sources()

    # ── Dimension reads ───────────────────────────────────────────────

    async def get_dimensions(self, node_id: uuid.UUID) -> list[Dimension]:
        """Get all dimensions for a node."""
        async with self._get_session() as session:
            stmt = select(Dimension).where(Dimension.node_id == node_id)
            result = await session.execute(stmt)
            return list(result.scalars().all())

    async def get_dimensions_with_facts(self, node_id: uuid.UUID) -> list[Dimension]:
        """Get all dimensions for a node with dimension_facts eagerly loaded."""
        async with self._get_session() as session:
            stmt = (
                select(Dimension).where(Dimension.node_id == node_id).options(selectinload(Dimension.dimension_facts))
            )
            result = await session.execute(stmt)
            return list(result.scalars().all())

    # ── Search ────────────────────────────────────────────────────────

    async def search_fact_pool(
        self,
        embedding: list[float],
        limit: int = 30,
        threshold: float = 0.5,
    ) -> list[Fact]:
        """Search all facts by embedding similarity via Qdrant."""
        if self._qdrant_fact_repo is None:
            logger.error("search_fact_pool called but Qdrant fact repo is not available")
            return []
        results = await self._qdrant_fact_repo.search_similar(
            embedding,
            limit=limit,
            score_threshold=threshold,
        )
        if not results:
            return []
        fact_ids = [r.fact_id for r in results]
        return await self._load_facts_preserving_order(fact_ids)

    async def hybrid_search_facts(
        self,
        query: str,
        embedding: list[float],
        limit: int = 30,
        score_threshold: float = 0.3,
        fact_type: str | None = None,
    ) -> list[Fact]:
        """Hybrid search: vector similarity + keyword matching via Qdrant RRF.

        Falls back to pure vector search if hybrid search fails.
        """
        if self._qdrant_fact_repo is None:
            logger.error("hybrid_search_facts called but Qdrant fact repo is not available")
            return []
        try:
            results = await self._qdrant_fact_repo.hybrid_search(
                query_embedding=embedding,
                query_text=query,
                limit=limit,
                score_threshold=score_threshold,
                fact_type=fact_type,
            )
        except Exception:
            logger.warning("hybrid_search failed, falling back to vector search", exc_info=True)
            results = await self._qdrant_fact_repo.search_similar(
                embedding,
                limit=limit,
                score_threshold=score_threshold,
                fact_type=fact_type,
            )
        if not results:
            return []
        fact_ids = [r.fact_id for r in results]
        return await self._load_facts_preserving_order(fact_ids)

    async def search_fact_pool_text(self, query: str, limit: int = 30) -> list[Fact]:
        """Text search across all facts (fact pool pattern)."""
        async with self._get_session() as session:
            _, _, fact_repo = self._repos_from_session(session)
            return await fact_repo.search_fact_pool_text(query, limit=limit)

    async def search_fact_pool_trigram(
        self,
        query: str,
        threshold: float = 0.3,
        limit: int = 30,
    ) -> list[Fact]:
        """Search facts using trigram word_similarity (pg_trgm)."""
        async with self._get_session() as session:
            _, _, fact_repo = self._repos_from_session(session)
            return await fact_repo.search_fact_pool_trigram(query, threshold=threshold, limit=limit)

    async def find_nodes_sharing_facts(
        self,
        node_id: uuid.UUID,
        limit: int = 20,
    ) -> list[tuple[uuid.UUID, str, list[uuid.UUID]]]:
        """Find nodes that share facts with the given node."""
        async with self._get_session() as session:
            _, _, fact_repo = self._repos_from_session(session)
            return await fact_repo.find_nodes_sharing_facts(node_id, limit=limit)

    async def find_nodes_by_embedding_facts(
        self,
        query_embedding: list[float],
        source_node_id: uuid.UUID,
        threshold: float = 0.45,
        node_limit: int = 15,
    ) -> list[tuple[uuid.UUID, str, list[uuid.UUID]]]:
        """Find nodes via embedding-similar facts.

        Requires Qdrant for vector search; relational join uses graph-db NodeFact junction.
        """
        if self._qdrant_fact_repo is None:
            logger.error("find_nodes_by_embedding_facts called but Qdrant fact repo is not available")
            return []

        async with self._get_session() as session:
            # Get fact IDs already linked to source node (to exclude)
            source_facts_stmt = select(NodeFact.fact_id).where(NodeFact.node_id == source_node_id)
            source_result = await session.execute(source_facts_stmt)
            source_fact_ids = [row[0] for row in source_result.all()]

            # Search Qdrant for similar facts, excluding source node's facts
            qdrant_results = await self._qdrant_fact_repo.search_similar(
                query_embedding,
                limit=100,
                score_threshold=threshold,
                exclude_ids=source_fact_ids,
            )
            if not qdrant_results:
                return []

            candidate_fact_ids = [r.fact_id for r in qdrant_results]

            from sqlalchemy import literal_column

            stmt = (
                select(
                    NodeFact.node_id,
                    Node.concept,
                    func.array_agg(NodeFact.fact_id.distinct()).label("evidence_fact_ids"),
                    func.count(NodeFact.fact_id.distinct()).label("evidence_count"),
                )
                .join(Node, Node.id == NodeFact.node_id)
                .where(
                    NodeFact.fact_id.in_(candidate_fact_ids),
                    NodeFact.node_id != source_node_id,
                )
                .group_by(NodeFact.node_id, Node.concept)
                .order_by(literal_column("evidence_count").desc())
                .limit(node_limit)
            )
            result = await session.execute(stmt)
            return [(row[0], row[1], list(row[2])) for row in result.all()]

    async def find_nodes_by_text_facts(
        self,
        query: str,
        source_node_id: uuid.UUID,
        threshold: float = 0.3,
        node_limit: int = 10,
    ) -> list[tuple[uuid.UUID, str, list[uuid.UUID]]]:
        """Find nodes via text-matching facts using pg_trgm."""
        async with self._get_session() as session:
            _, _, fact_repo = self._repos_from_session(session)
            return await fact_repo.find_nodes_by_text_facts(
                query,
                source_node_id,
                threshold=threshold,
                node_limit=node_limit,
            )

    async def find_nodes_with_similar_facts(
        self,
        fact_embeddings: list[list[float]],
        exclude_node_id: uuid.UUID,
        threshold: float = 0.4,
        limit: int = 10,
    ) -> list[tuple[uuid.UUID, int]]:
        """Find nodes with facts similar to the given embeddings.

        Requires Qdrant for batch vector search, then aggregates by node via PG relational join.
        """
        if self._qdrant_fact_repo is None:
            logger.error("find_nodes_with_similar_facts called but Qdrant fact repo is not available")
            return []

        node_counts: dict[uuid.UUID, int] = {}
        async with self._get_session() as session:
            for emb in fact_embeddings:
                results = await self._qdrant_fact_repo.search_similar(
                    emb,
                    limit=20,
                    score_threshold=threshold,
                )
                if not results:
                    continue
                candidate_fact_ids = [r.fact_id for r in results]
                stmt = (
                    select(NodeFact.node_id)
                    .where(
                        NodeFact.fact_id.in_(candidate_fact_ids),
                        NodeFact.node_id != exclude_node_id,
                    )
                    .distinct()
                )
                result = await session.execute(stmt)
                for row in result.all():
                    nid = row[0]
                    node_counts[nid] = node_counts.get(nid, 0) + 1

        sorted_nodes = sorted(node_counts.items(), key=lambda x: x[1], reverse=True)
        return sorted_nodes[:limit]

    async def search_fact_pool_excluding_rejected(
        self,
        embedding: list[float],
        node_id: uuid.UUID,
        limit: int = 30,
        threshold: float = 0.5,
    ) -> list[Fact]:
        """Search fact pool by embedding, excluding facts rejected for this node."""
        if self._qdrant_fact_repo is None:
            logger.error("search_fact_pool_excluding_rejected called but Qdrant fact repo is not available")
            return []
        rejected_ids = await self.get_rejected_fact_ids(node_id)
        exclude_list = list(rejected_ids) if rejected_ids else None
        results = await self._qdrant_fact_repo.search_similar(
            embedding,
            limit=limit,
            score_threshold=threshold,
            exclude_ids=exclude_list,
        )
        if not results:
            return []
        fact_ids = [r.fact_id for r in results]
        return await self._load_facts_preserving_order(fact_ids)

    async def search_fact_pool_text_excluding_rejected(
        self,
        query: str,
        node_id: uuid.UUID,
        limit: int = 30,
    ) -> list[Fact]:
        """Text search fact pool, excluding facts rejected for this node."""
        async with self._get_session() as session:
            _, _, fact_repo = self._repos_from_session(session)
            return await fact_repo.search_fact_pool_text_excluding_rejected(
                query,
                node_id,
                limit=limit,
            )

    async def get_rejected_fact_ids(self, node_id: uuid.UUID) -> set[uuid.UUID]:
        """Get all fact IDs rejected for a given node."""
        async with self._get_session() as session:
            _, _, fact_repo = self._repos_from_session(session)
            return await fact_repo.get_rejected_fact_ids(node_id)

    # ── Perspectives and analytics ────────────────────────────────────

    async def get_perspectives(self, concept_node_id: uuid.UUID) -> list[Node]:
        """Get all perspective nodes for a concept."""
        async with self._get_session() as session:
            node_repo, _, _ = self._repos_from_session(session)
            return await node_repo.get_perspectives_for_concept(concept_node_id)

    async def get_stale_nodes(self, max_age_days: int = 30, limit: int = 20) -> list[Node]:
        """Get nodes that are overdue for refresh."""
        async with self._get_session() as session:
            node_repo, _, _ = self._repos_from_session(session)
            return await node_repo.get_stale_nodes(max_age_days=max_age_days, limit=limit)

    async def get_perspective_summary(self, node_id: uuid.UUID) -> dict[str, int]:
        """Return counts of supporting/challenging/neutral facts for a node."""
        facts_with_stance = await self.get_node_facts_with_stance(node_id)
        counts = {"supporting": 0, "challenging": 0, "neutral": 0, "unclassified": 0}
        for _fact, stance in facts_with_stance:
            if stance == "supports":
                counts["supporting"] += 1
            elif stance == "challenges":
                counts["challenging"] += 1
            elif stance == "neutral":
                counts["neutral"] += 1
            else:
                counts["unclassified"] += 1
        return counts

    # ── Versioning ────────────────────────────────────────────────────

    async def get_node_history(self, node_id: uuid.UUID) -> list[NodeVersion]:
        """Get the version history for a node, ordered by version number."""
        async with self._get_session() as session:
            stmt = select(NodeVersion).where(NodeVersion.node_id == node_id).order_by(NodeVersion.version_number)
            result = await session.execute(stmt)
            return list(result.scalars().all())

    # ── Path finding ──────────────────────────────────────────────────

    async def find_shortest_paths(
        self,
        source_id: uuid.UUID,
        target_id: uuid.UUID,
        max_depth: int = 6,
        limit: int = 5,
    ) -> list[list[PathStep]]:
        """Find shortest paths between two nodes using level-order BFS.

        Uses bulk edge loading per BFS level to avoid N+1 queries.
        """
        if source_id == target_id:
            return [[PathStep(node_id=source_id, edge=None)]]

        async with self._get_session() as session:
            _, edge_repo, _ = self._repos_from_session(session)

            # Each entry: (current_node_id, path_so_far, visited_set)
            current_level: list[tuple[uuid.UUID, list[PathStep], set[uuid.UUID]]] = [
                (source_id, [PathStep(node_id=source_id, edge=None)], {source_id}),
            ]

            results: list[list[PathStep]] = []
            shortest_length: int | None = None

            for depth in range(max_depth):
                if not current_level or len(results) >= limit:
                    break
                if shortest_length is not None and depth >= shortest_length:
                    break

                # Bulk-load edges for all frontier nodes in one query
                frontier_ids = list({entry[0] for entry in current_level})
                edges_by_node = await edge_repo.get_edges_for_nodes(frontier_ids, direction="both")

                next_level: list[tuple[uuid.UUID, list[PathStep], set[uuid.UUID]]] = []

                for current_id, path, visited in current_level:
                    if len(results) >= limit:
                        break

                    for edge in edges_by_node.get(current_id, []):
                        neighbor_id = edge.target_node_id if edge.source_node_id == current_id else edge.source_node_id

                        if neighbor_id in visited:
                            continue

                        new_step = PathStep(node_id=neighbor_id, edge=edge)
                        new_path = [*path, new_step]

                        if neighbor_id == target_id:
                            results.append(new_path)
                            shortest_length = len(new_path) - 1
                            if len(results) >= limit:
                                break
                        else:
                            new_visited = visited | {neighbor_id}
                            next_level.append((neighbor_id, new_path, new_visited))

                current_level = next_level

            return results

    # ── Node merging (admin operation, graph-db only) ─────────────────

    async def merge_nodes(self, keep_id: uuid.UUID, absorb_id: uuid.UUID) -> Node:
        """Merge absorb_id into keep_id.

        Transfers all facts, edges, and dimensions from the absorbed node
        to the kept node, then deletes the absorbed node.
        """
        async with self._get_session() as session:
            node_repo, edge_repo, fact_repo = self._repos_from_session(session)

            id_a, id_b = sorted([keep_id, absorb_id])
            lock_a = id_a.int & 0x7FFFFFFF
            lock_b = id_b.int & 0x7FFFFFFF
            await session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": lock_a})
            if lock_a != lock_b:
                await session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": lock_b})

            keep = await node_repo.get_by_id(keep_id)
            if keep is None:
                raise ValueError(f"Keep node not found: {keep_id}")
            absorb = await node_repo.get_by_id(absorb_id)
            if absorb is None:
                raise ValueError(f"Absorb node not found: {absorb_id}")

            # Transfer facts
            absorb_facts_stmt = select(NodeFact).where(NodeFact.node_id == absorb_id)
            result = await session.execute(absorb_facts_stmt)
            absorb_nfs = list(result.scalars().all())
            for nf in absorb_nfs:
                try:
                    await fact_repo.link_to_node(keep_id, nf.fact_id, nf.relevance_score)
                except Exception:
                    pass

            # Transfer edges
            absorb_edges = await edge_repo.get_edges(absorb_id, direction="both")
            for edge in absorb_edges:
                new_source = keep_id if edge.source_node_id == absorb_id else edge.source_node_id
                new_target = keep_id if edge.target_node_id == absorb_id else edge.target_node_id
                if new_source == new_target:
                    continue
                try:
                    existing_edge = await edge_repo.get_edge(
                        new_source,
                        new_target,
                        edge.relationship_type,
                    )
                    if existing_edge:
                        merged_weight = (existing_edge.weight + edge.weight) / 2.0
                        existing_edge.weight = merged_weight
                        existing_edge.updated_at = _utcnow()
                        await session.flush()
                        target_edge_id = existing_edge.id
                    else:
                        new_edge = await edge_repo.create(
                            source_node_id=new_source,
                            target_node_id=new_target,
                            relationship_type=edge.relationship_type,
                            weight=edge.weight,
                        )
                        target_edge_id = new_edge.id

                    for ef in edge.edge_facts:
                        await edge_repo.link_fact_to_edge(target_edge_id, ef.fact_id, ef.relevance_score)
                except Exception:
                    logger.debug(
                        "Error redirecting edge %s during merge",
                        edge.id,
                        exc_info=True,
                    )

            # Transfer dimensions
            dim_stmt = select(Dimension).where(Dimension.node_id == absorb_id)
            dim_result = await session.execute(dim_stmt)
            absorb_dims = list(dim_result.scalars().all())
            for dim in absorb_dims:
                dim.node_id = keep_id
            await session.flush()

            await node_repo.delete(absorb_id)

            await session.refresh(keep)
            return keep

    # ── Utilities ─────────────────────────────────────────────────────

    def compute_richness(self, node: Node, fact_count: int, dimension_count: int) -> float:
        """Compute a richness score for a node."""
        raw = fact_count * 0.1 + dimension_count * 0.2 + node.access_count * 0.01
        return min(1.0, raw)

    def is_node_stale(self, node: Node) -> bool:
        """Check whether a node is past its stale_after window."""
        if node.updated_at is None or node.stale_after is None:
            return True
        now = _utcnow()
        stale_cutoff = node.updated_at + timedelta(days=node.stale_after)
        return now > stale_cutoff
