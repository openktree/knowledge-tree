from __future__ import annotations

import asyncio
import logging
import random
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING, TypedDict, TypeVar

if TYPE_CHECKING:
    from qdrant_client import AsyncQdrantClient

from sqlalchemy import delete, func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from kt_db.models import Dimension, DimensionFact, Edge, EdgeFact, Fact, Node, NodeFact, NodeVersion, _utcnow
from kt_db.repositories.edges import EdgeRepository
from kt_db.repositories.facts import FactRepository
from kt_db.repositories.nodes import NodeRepository
from kt_models.embeddings import EmbeddingService

logger = logging.getLogger(__name__)

_T = TypeVar("_T")


class SubgraphResult(TypedDict):
    nodes: list[Node]
    edges: list[Edge]
    edge_fact_ids: dict[uuid.UUID, list[uuid.UUID]]


def _is_deadlock(exc: Exception) -> bool:
    """Check if an exception is a PostgreSQL deadlock (SQLSTATE 40P01)."""
    if isinstance(exc, DBAPIError) and exc.orig is not None:
        sqlstate = getattr(exc.orig, "sqlstate", None)
        if sqlstate == "40P01":
            return True
        if "DeadlockDetected" in type(exc.orig).__name__:
            return True
    return False


async def _retry_on_deadlock(
    session: AsyncSession,
    operation: Callable[[], Awaitable[_T]],
    max_retries: int = 3,
) -> _T:
    """Execute *operation* inside a savepoint, retrying on deadlock.

    PostgreSQL breaks deadlocks by aborting one victim transaction.  When the
    statement runs inside a SAVEPOINT the abort is scoped to that savepoint and
    the outer transaction remains usable.  We retry with exponential back-off
    and jitter so the competing sessions don't re-deadlock immediately.
    """
    for attempt in range(max_retries + 1):
        try:
            async with session.begin_nested():
                result = await operation()
            return result
        except DBAPIError as exc:
            if _is_deadlock(exc) and attempt < max_retries:
                delay = 0.05 * (2**attempt) + random.uniform(0, 0.05)
                logger.debug(
                    "Deadlock detected (attempt %d/%d), retrying in %.3fs",
                    attempt + 1,
                    max_retries,
                    delay,
                )
                await asyncio.sleep(delay)
                continue
            raise
    # Unreachable but keeps mypy happy
    raise RuntimeError("_retry_on_deadlock: exceeded max_retries")


@dataclass
class PathStep:
    """A single step in a graph path."""

    node_id: uuid.UUID
    edge: Edge | None  # None for the source node (first step)


class GraphEngine:
    """Core graph engine providing all graph operations.

    Composes NodeRepository, EdgeRepository, and FactRepository to provide
    a unified interface for graph manipulation.

    When ``write_session`` is provided (Phase 3 dual-DB architecture),
    write operations for nodes, edges, and dimensions are routed to the
    write-optimized database.  The sync worker propagates changes to
    the graph-db.  Read operations always use the graph-db session.
    """

    def __init__(
        self,
        session: AsyncSession | None = None,
        embedding_service: EmbeddingService | None = None,
        write_session: AsyncSession | None = None,
        qdrant_client: AsyncQdrantClient | None = None,
    ) -> None:
        self._session = session
        self._embedding_service = embedding_service
        self._node_repo = NodeRepository(session) if session is not None else None
        self._edge_repo = EdgeRepository(session) if session is not None else None
        self._fact_repo = FactRepository(session) if session is not None else None
        self._write_session = write_session

        # In-memory cache for nodes created in this pipeline run.
        # Populated by create_node() so that subsequent write-db methods
        # (add_dimension, create_edge, set_parent, etc.) can resolve
        # node_type + concept for key generation without graph-db reads.
        self._node_cache: dict[uuid.UUID, Node] = {}

        # Cache for edge UUID -> write-db key, populated by create_edge()
        self._edge_key_cache: dict[uuid.UUID, str] = {}

        # Qdrant repositories (lazy-initialized when client is provided)
        self._qdrant_fact_repo = None
        self._qdrant_node_repo = None
        if qdrant_client is not None:
            from kt_qdrant.repositories.facts import QdrantFactRepository
            from kt_qdrant.repositories.nodes import QdrantNodeRepository

            self._qdrant_fact_repo = QdrantFactRepository(qdrant_client)
            self._qdrant_node_repo = QdrantNodeRepository(qdrant_client)

        # Write repositories (lazy-initialized when write_session is set)
        self._write_node_repo = None
        self._write_edge_repo = None
        self._write_dim_repo = None
        self._write_fact_repo = None
        if write_session is not None:
            from kt_db.repositories.write_dimensions import WriteDimensionRepository
            from kt_db.repositories.write_edges import WriteEdgeRepository
            from kt_db.repositories.write_facts import WriteFactRepository
            from kt_db.repositories.write_nodes import WriteNodeRepository

            self._write_node_repo = WriteNodeRepository(write_session)
            self._write_edge_repo = WriteEdgeRepository(write_session)
            self._write_dim_repo = WriteDimensionRepository(write_session)
            self._write_fact_repo = WriteFactRepository(write_session)

    @property
    def has_write_db(self) -> bool:
        """Whether a write-optimized database session is available."""
        return self._write_session is not None

    @property
    def has_graph_db(self) -> bool:
        """Whether a graph-db (read) session is available."""
        return self._session is not None

    def _require_graph_session(self) -> AsyncSession:
        """Return the graph-db session or raise if not available."""
        if self._session is None:
            raise RuntimeError(
                "This GraphEngine operation requires a graph-db session, "
                "but was created in write-only mode. Pass a graph-db session "
                "or use a write-db-routed method instead."
            )
        return self._session

    def _require_node_repo(self) -> NodeRepository:
        if self._node_repo is None:
            self._require_graph_session()  # will raise
        return self._node_repo  # type: ignore[return-value]

    def _require_edge_repo(self) -> EdgeRepository:
        if self._edge_repo is None:
            self._require_graph_session()  # will raise
        return self._edge_repo  # type: ignore[return-value]

    def _require_fact_repo(self) -> FactRepository:
        if self._fact_repo is None:
            self._require_graph_session()  # will raise
        return self._fact_repo  # type: ignore[return-value]

    async def _get_cached_or_db(self, node_id: uuid.UUID) -> Node | None:
        """Look up a node from cache, then write-db, then graph-db.

        Nodes created in the current pipeline run are cached and won't exist
        in graph-db until the sync worker propagates them.  When write-db is
        available it is preferred over graph-db to avoid pool pressure.
        """
        cached = self._node_cache.get(node_id)
        if cached is not None:
            return cached
        if self._write_node_repo is not None:
            wn = await self._write_node_repo.get_by_uuid(node_id)
            if wn is not None:
                from kt_db.keys import key_to_uuid

                parent_id = key_to_uuid(wn.parent_key) if wn.parent_key else None
                return Node(
                    id=wn.node_uuid,
                    concept=wn.concept,
                    node_type=wn.node_type,
                    definition=wn.definition,
                    metadata_=wn.metadata_,
                    parent_id=parent_id,
                    stale_after=wn.stale_after,
                    created_at=wn.created_at,
                    updated_at=wn.updated_at,
                )
        if self._node_repo is not None:
            return await self._node_repo.get_by_id(node_id)
        return None

    # ── Qdrant helpers ────────────────────────────────────────────────

    async def _load_facts_preserving_order(self, fact_ids: list[uuid.UUID]) -> list[Fact]:
        """Load Fact objects by IDs, preserving the given order.

        When write-db is available, loads from WriteFact and converts to
        transient Fact objects for pipeline compatibility.  Otherwise reads
        from graph-db.
        """
        if not fact_ids:
            return []

        if self._write_fact_repo is not None:
            write_facts = await self._write_fact_repo.get_by_ids(fact_ids)
            wf_by_id = {wf.id: wf for wf in write_facts}
            result_facts: list[Fact] = []
            for fid in fact_ids:
                wf = wf_by_id.get(fid)
                if wf is not None:
                    result_facts.append(Fact(id=wf.id, content=wf.content, fact_type=wf.fact_type))
            return result_facts

        session = self._require_graph_session()
        result = await session.execute(select(Fact).where(Fact.id.in_(fact_ids)))
        facts_by_id = {f.id: f for f in result.scalars().all()}
        return [facts_by_id[fid] for fid in fact_ids if fid in facts_by_id]

    async def get_facts_by_ids(self, fact_ids: list[uuid.UUID]) -> list[Fact]:
        """Public interface to load facts by ID.

        Routes to write-db when available, falls back to graph-db.
        """
        return await self._load_facts_preserving_order(fact_ids)

    async def upsert_fact_to_qdrant(
        self,
        fact_id: uuid.UUID,
        embedding: list[float],
        fact_type: str | None = None,
    ) -> None:
        """Upsert a fact embedding to Qdrant (no-op if Qdrant not available)."""
        if self._qdrant_fact_repo is not None:
            try:
                await self._qdrant_fact_repo.upsert(fact_id, embedding, fact_type=fact_type)
            except Exception:
                logger.warning("Failed to upsert fact %s to Qdrant", fact_id, exc_info=True)

    async def upsert_facts_to_qdrant(
        self,
        facts: list[tuple[uuid.UUID, list[float], str | None]],
    ) -> None:
        """Batch upsert fact embeddings to Qdrant (no-op if Qdrant not available)."""
        if self._qdrant_fact_repo is not None and facts:
            try:
                await self._qdrant_fact_repo.upsert_batch(facts)
            except Exception:
                logger.warning("Failed to batch upsert %d facts to Qdrant", len(facts), exc_info=True)

    async def upsert_node_to_qdrant(
        self,
        node_id: uuid.UUID,
        embedding: list[float],
        node_type: str | None = None,
        concept: str | None = None,
    ) -> None:
        """Upsert a node embedding to Qdrant (no-op if Qdrant not available)."""
        if self._qdrant_node_repo is not None:
            try:
                await self._qdrant_node_repo.upsert(node_id, embedding, node_type=node_type, concept=concept)
            except Exception:
                logger.warning("Failed to upsert node %s to Qdrant", node_id, exc_info=True)

    # ── Node operations ──────────────────────────────────────────────

    async def create_node(
        self,
        concept: str,
        embedding: list[float] | None = None,
        attractor: str | None = None,
        filter_id: str | None = None,
        max_content_tokens: int = 500,
        node_type: str = "concept",
        parent_id: uuid.UUID | None = None,
        source_concept_id: uuid.UUID | None = None,
        metadata_: dict | None = None,
        entity_subtype: str | None = None,
    ) -> Node:
        """Create a new node in the graph.

        When write-db is available: writes to write-db ONLY + Qdrant.
        An in-memory Node object is returned (cached for subsequent methods).
        The sync worker propagates to graph-db.
        """
        if self._write_node_repo is not None:
            from kt_db.keys import key_to_uuid, make_node_key

            node_key = make_node_key(node_type, concept)
            det_uuid = key_to_uuid(node_key)

            # Resolve parent/source keys for write-db
            parent_key = None
            if parent_id is not None:
                parent_node = await self._get_cached_or_db(parent_id)
                if parent_node:
                    parent_key = make_node_key(parent_node.node_type, parent_node.concept)

            source_key = None
            if source_concept_id is not None:
                source_node = await self._get_cached_or_db(source_concept_id)
                if source_node:
                    source_key = make_node_key(source_node.node_type, source_node.concept)

            await self._write_node_repo.upsert(
                node_type=node_type,
                concept=concept,
                parent_key=parent_key,
                source_concept_key=source_key,
                attractor=attractor,
                filter_id=filter_id,
                max_content_tokens=max_content_tokens,
                metadata_=metadata_,
                entity_subtype=entity_subtype,
            )
            await self._write_session.commit()  # type: ignore[union-attr]

            # Build in-memory Node object (not persisted to graph-db).
            # Sync worker will create the graph-db row later.
            node = Node(
                id=det_uuid,
                concept=concept,
                node_type=node_type,
                parent_id=parent_id,
                source_concept_id=source_concept_id,
                attractor=attractor,
                filter_id=filter_id,
                max_content_tokens=max_content_tokens,
                metadata_=metadata_,
                entity_subtype=entity_subtype,
                embedding=embedding,
            )
            self._node_cache[det_uuid] = node

            # Upsert embedding to Qdrant for vector search
            if embedding is not None and self._qdrant_node_repo is not None:
                try:
                    await self._qdrant_node_repo.upsert(det_uuid, embedding, node_type=node_type, concept=concept)
                except Exception:
                    logger.warning("Failed to upsert node %s to Qdrant", det_uuid, exc_info=True)
            return node

        node = await self._node_repo.create(
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
        # Set embedding as transient property for in-memory use
        node.embedding = embedding
        # Upsert embedding to Qdrant for vector search
        if embedding is not None and self._qdrant_node_repo is not None:
            try:
                await self._qdrant_node_repo.upsert(node.id, embedding, node_type=node_type, concept=concept)
            except Exception:
                logger.warning("Failed to upsert node %s to Qdrant", node.id, exc_info=True)
        return node

    async def get_node(self, node_id: uuid.UUID) -> Node | None:
        """Get a node by ID (checks cache first for pipeline-created nodes)."""
        return await self._get_cached_or_db(node_id)

    async def increment_access_count(self, node_id: uuid.UUID) -> None:
        """Increment a node's access_count by 1 (best-effort)."""
        if self._write_node_repo is not None:
            from kt_db.keys import make_node_key

            node = await self._get_cached_or_db(node_id)
            if node:
                try:
                    node_key = make_node_key(node.node_type, node.concept)
                    await self._write_node_repo.increment_access_count(node_key)
                    await self._write_session.commit()  # type: ignore[union-attr]
                except Exception:
                    logger.warning("Non-critical: failed to increment access_count for node %s", node_id)
                return

        try:
            await _retry_on_deadlock(
                self._session,
                lambda: self._node_repo.increment_access_count(node_id),
            )
        except Exception:
            logger.warning("Non-critical: failed to increment access_count for node %s", node_id)

    async def increment_update_count(self, node_id: uuid.UUID) -> None:
        """Increment a node's update_count by 1 (best-effort)."""
        if self._write_node_repo is not None:
            from kt_db.keys import make_node_key

            node = await self._get_cached_or_db(node_id)
            if node:
                try:
                    node_key = make_node_key(node.node_type, node.concept)
                    await self._write_node_repo.increment_update_count(node_key)
                    await self._write_session.commit()  # type: ignore[union-attr]
                except Exception:
                    logger.warning("Non-critical: failed to increment update_count for node %s", node_id)
                return

        try:
            await _retry_on_deadlock(
                self._session,
                lambda: self._node_repo.increment_update_count(node_id),
            )
        except Exception:
            logger.warning("Non-critical: failed to increment update_count for node %s", node_id)

    async def update_node(self, node_id: uuid.UUID, **kwargs: object) -> Node:
        """Update a node's fields and return the refreshed node.

        Routes metadata updates to write-db when available.
        """
        if self._write_node_repo is not None and "metadata_" in kwargs:
            wn = await self._write_node_repo.get_by_uuid(node_id)
            if wn is not None:
                await self._write_node_repo.update_metadata(wn.key, kwargs["metadata_"])  # type: ignore[arg-type]
                await self._write_session.commit()  # type: ignore[union-attr]
                return Node(
                    id=wn.node_uuid,
                    concept=wn.concept,
                    node_type=wn.node_type,
                    definition=wn.definition,
                    metadata_=kwargs["metadata_"],  # type: ignore[arg-type]
                )
        await self._node_repo.update_fields(node_id, **kwargs)
        node = await self._node_repo.get_by_id(node_id)
        if node is None:
            raise ValueError(f"Node not found: {node_id}")
        await self._session.refresh(node)
        return node

    async def search_nodes(
        self,
        query: str,
        limit: int = 10,
        node_type: str | None = None,
    ) -> list[Node]:
        """Search nodes by concept name (text search)."""
        return await self._node_repo.search_by_concept(query, limit=limit, node_type=node_type)

    async def find_similar_nodes(
        self,
        embedding: list[float],
        threshold: float = 0.3,
        limit: int = 10,
        node_type: str | None = None,
    ) -> list[Node]:
        """Find nodes similar to the given embedding.

        Requires Qdrant for vector search.
        """
        if self._qdrant_node_repo is None:
            logger.error("find_similar_nodes called but Qdrant node repo is not available")
            return []
        # Qdrant uses similarity scores (higher = more similar)
        # Convert: score_threshold = 1 - distance_threshold
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
        nodes = await self._node_repo.get_by_ids(node_ids)
        # Preserve Qdrant ordering
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
        return await self._node_repo.list_paginated(
            offset=offset, limit=limit, search=search, node_type=node_type, sort=sort
        )

    async def count_nodes(self, search: str | None = None, node_type: str | None = None) -> int:
        """Count total nodes, optionally filtered by node_type."""
        return await self._node_repo.count(search=search, node_type=node_type)

    async def delete_node(self, node_id: uuid.UUID) -> bool:
        """Delete a node by ID."""
        return await self._node_repo.delete(node_id)

    # ── Edge operations ──────────────────────────────────────────────

    async def create_edge(
        self,
        source_id: uuid.UUID,
        target_id: uuid.UUID,
        rel_type: str,
        weight: float = 0.5,
        query_id: uuid.UUID | None = None,
        justification: str | None = None,
        metadata: dict[str, object] | None = None,
        fact_ids: list[uuid.UUID] | None = None,
    ) -> Edge | None:
        """Create or update an edge between two nodes.

        When write-db is available: writes to write-db ONLY (no deadlocks).
        Fact IDs are stored on the write-db row; the sync worker creates both
        the graph-db edge and EdgeFact junction rows atomically.
        Returns None when using write-db (edge appears after sync).
        """
        if self._write_edge_repo is not None:
            from kt_db.keys import make_node_key

            source_node = await self._get_cached_or_db(source_id)
            target_node = await self._get_cached_or_db(target_id)
            if source_node is None or target_node is None:
                logger.warning("create_edge: source or target node not found")
                return None

            source_key = make_node_key(source_node.node_type, source_node.concept)
            target_key = make_node_key(target_node.node_type, target_node.concept)

            fact_id_strs = [str(fid) for fid in fact_ids] if fact_ids else None

            edge_key = await self._write_edge_repo.upsert(
                rel_type=rel_type,
                source_node_key=source_key,
                target_node_key=target_key,
                weight=weight,
                justification=justification,
                fact_ids=fact_id_strs,
                metadata_=metadata,
            )
            await self._write_session.commit()  # type: ignore[union-attr]

            # Cache edge UUID -> key for link_fact_to_edge
            from kt_db.keys import key_to_uuid

            self._edge_key_cache[key_to_uuid(edge_key)] = edge_key

            # Write-db only — sync worker creates graph-db edge + EdgeFacts
            return None

        return await _retry_on_deadlock(
            self._session,
            lambda: self._edge_repo.create(
                source_node_id=source_id,
                target_node_id=target_id,
                relationship_type=rel_type,
                weight=weight,
                created_by_query=query_id,
                justification=justification,
                metadata=metadata,
            ),
        )

    async def set_parent(self, node_id: uuid.UUID, parent_id: uuid.UUID) -> None:
        """Set the tree parent of a node.

        When write-db is available: writes to write-db ONLY.
        The sync worker propagates to graph-db.
        """
        if node_id == parent_id:
            raise ValueError(f"Cannot set node {node_id} as its own parent")
        ok, reason = await self._validate_parent_chain(node_id, parent_id)
        if not ok:
            raise ValueError(f"Invalid parent {parent_id} for node {node_id}: {reason}")

        if self._write_node_repo is not None:
            from kt_db.keys import make_node_key

            node = await self._get_cached_or_db(node_id)
            parent = await self._get_cached_or_db(parent_id)
            if node and parent:
                await self._write_node_repo.upsert(
                    node_type=node.node_type,
                    concept=node.concept,
                    parent_key=make_node_key(parent.node_type, parent.concept),
                )
                await self._write_session.commit()  # type: ignore[union-attr]
                # Update cache so subsequent parent chain validation works
                if node_id in self._node_cache:
                    self._node_cache[node_id].parent_id = parent_id
            return

        # Fallback: no write-db, write directly to graph-db
        await self._node_repo.update_fields(node_id, parent_id=parent_id)

    async def _validate_parent_chain(
        self,
        node_id: uuid.UUID,
        proposed_parent_id: uuid.UUID,
        max_depth: int = 50,
    ) -> tuple[bool, str]:
        """Validate that proposed_parent_id leads to a root node."""
        from kt_config.types import DEFAULT_PARENTS

        root_ids = set(DEFAULT_PARENTS.values())

        if proposed_parent_id in root_ids:
            return True, ""

        current = proposed_parent_id
        visited: set[uuid.UUID] = set()
        for _ in range(max_depth):
            if current == node_id:
                return False, "would create a cycle"
            if current in visited:
                return False, "existing cycle in chain"
            if current in root_ids:
                return True, ""
            visited.add(current)
            parent_node = await self._get_cached_or_db(current)
            if parent_node is None or parent_node.parent_id is None:
                return False, f"chain ends at {current} which is not a root node"
            current = parent_node.parent_id

        return False, f"chain exceeds max depth ({max_depth}) without reaching root"

    async def chain_reaches_root(
        self,
        node_id: uuid.UUID,
        max_depth: int = 50,
    ) -> bool:
        """Check whether node_id's parent chain reaches a well-known root."""
        from kt_config.types import DEFAULT_PARENTS

        root_ids = set(DEFAULT_PARENTS.values())
        if node_id in root_ids:
            return True

        current = node_id
        visited: set[uuid.UUID] = set()
        for _ in range(max_depth):
            if current in root_ids:
                return True
            if current in visited:
                return False
            visited.add(current)
            node = await self._get_cached_or_db(current)
            if node is None or node.parent_id is None:
                return False
            current = node.parent_id
        return False

    async def get_children(self, parent_id: uuid.UUID) -> list[Node]:
        """Get all child nodes of a given parent.

        Routes to write-db when available using parent_key lookup.
        """
        if self._write_node_repo is not None:
            # Find the parent's write-db key
            parent_wn = await self._write_node_repo.get_by_uuid(parent_id)
            if parent_wn is not None:
                children_wn = await self._write_node_repo.get_children_by_parent_key(parent_wn.key)
                return [
                    Node(
                        id=wn.node_uuid,
                        concept=wn.concept,
                        node_type=wn.node_type,
                        definition=wn.definition,
                        metadata_=wn.metadata_,
                    )
                    for wn in children_wn
                ]
            return []
        return await self._node_repo.get_children(parent_id)

    async def count_children(self, parent_id: uuid.UUID) -> int:
        """Count the number of child nodes for a given parent."""
        return await self._node_repo.count_children(parent_id)

    async def get_edge_by_id(self, edge_id: uuid.UUID) -> Edge | None:
        """Get a single edge by ID with edge_facts loaded."""
        return await self._edge_repo.get_by_id(edge_id)

    async def list_edges(
        self,
        offset: int = 0,
        limit: int = 20,
        relationship_type: str | None = None,
        node_id: uuid.UUID | None = None,
        search: str | None = None,
    ) -> list[Edge]:
        """List edges with pagination and optional filters."""
        return await self._edge_repo.list_paginated(
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
        return await self._edge_repo.count(
            relationship_type=relationship_type,
            node_id=node_id,
            search=search,
        )

    async def get_nodes_by_ids(self, node_ids: list[uuid.UUID]) -> list[Node]:
        """Get multiple nodes by their IDs.

        Routes to write-db when available, converting WriteNode to
        transient Node objects for pipeline compatibility.
        """
        if self._write_node_repo is not None:
            write_nodes = await self._write_node_repo.get_by_uuids(node_ids)
            return [Node(id=wn.node_uuid, concept=wn.concept, node_type=wn.node_type) for wn in write_nodes]
        return await self._node_repo.get_by_ids(node_ids)

    async def delete_edge(self, edge_id: uuid.UUID) -> bool:
        """Delete an edge by ID."""
        return await self._edge_repo.delete(edge_id)

    async def get_edges(self, node_id: uuid.UUID, direction: str = "both") -> list[Edge]:
        """Get all edges connected to a node."""
        return await self._edge_repo.get_edges(node_id, direction=direction)

    async def get_neighbors(
        self,
        node_id: uuid.UUID,
        depth: int = 1,
        types: list[str] | None = None,
    ) -> list[Node]:
        """Get neighboring nodes up to a given depth."""
        return await self._edge_repo.get_neighbors(node_id, depth=depth, types=types)

    async def get_subgraph(
        self,
        node_ids: list[uuid.UUID],
        depth: int = 0,
    ) -> SubgraphResult:
        """Get a subgraph containing the specified nodes and edges between them."""
        if not node_ids:
            return SubgraphResult(nodes=[], edges=[], edge_fact_ids={})

        all_ids = set(node_ids)

        for _ in range(depth):
            id_list = list(all_ids)
            neighbor_edge_stmt = select(Edge).where(
                (Edge.source_node_id.in_(id_list)) | (Edge.target_node_id.in_(id_list))
            )
            neighbor_edge_result = await self._session.execute(neighbor_edge_stmt)
            neighbor_edges = list(neighbor_edge_result.scalars().all())

            new_ids: set[uuid.UUID] = set()
            for e in neighbor_edges:
                new_ids.add(e.source_node_id)
                new_ids.add(e.target_node_id)

            if not new_ids - all_ids:
                break
            all_ids |= new_ids

        node_stmt = select(Node).where(Node.id.in_(list(all_ids))).options(selectinload(Node.convergence_report))
        node_result = await self._session.execute(node_stmt)
        nodes = list(node_result.scalars().all())

        parent_ids = {n.parent_id for n in nodes if n.parent_id is not None and n.parent_id not in all_ids}
        if parent_ids:
            parent_stmt = (
                select(Node).where(Node.id.in_(list(parent_ids))).options(selectinload(Node.convergence_report))
            )
            parent_result = await self._session.execute(parent_stmt)
            parent_nodes = list(parent_result.scalars().all())
            nodes.extend(parent_nodes)
            all_ids |= parent_ids

        id_list_final = list(all_ids)
        edge_stmt = select(Edge).where(
            Edge.source_node_id.in_(id_list_final),
            Edge.target_node_id.in_(id_list_final),
        )
        edge_result = await self._session.execute(edge_stmt)
        edges = list(edge_result.scalars().all())

        # Batch-load edge→fact_id mappings with a lightweight query
        # instead of selectinload(Edge.edge_facts) which hydrates full ORM objects.
        edge_fact_ids: dict[uuid.UUID, list[uuid.UUID]] = {}
        if edges:
            from kt_db.models import EdgeFact

            edge_ids = [e.id for e in edges]
            ef_stmt = select(EdgeFact.edge_id, EdgeFact.fact_id).where(EdgeFact.edge_id.in_(edge_ids))
            ef_result = await self._session.execute(ef_stmt)
            for row in ef_result.all():
                edge_fact_ids.setdefault(row.edge_id, []).append(row.fact_id)

        return SubgraphResult(nodes=nodes, edges=edges, edge_fact_ids=edge_fact_ids)

    # ── Edge fact linking ──────────────────────────────────────────

    async def link_fact_to_edge(
        self,
        edge_id: uuid.UUID,
        fact_id: uuid.UUID,
        relevance_score: float = 1.0,
    ) -> EdgeFact | None:
        """Link a fact to an edge. Returns None if link already exists.

        When write-db is available: appends fact_id to WriteEdge.fact_ids.
        The sync worker creates EdgeFact junction rows from fact_ids.
        """
        if self._write_edge_repo is not None:
            edge_key = self._edge_key_cache.get(edge_id)
            if edge_key is None:
                logger.warning("link_fact_to_edge: edge %s not found in edge key cache", edge_id)
                return None
            await self._write_edge_repo.append_fact_id(edge_key, str(fact_id))
            await self._write_session.commit()  # type: ignore[union-attr]
            return None

        return await self._edge_repo.link_fact_to_edge(edge_id, fact_id, relevance_score)

    async def get_edge_facts(self, edge_id: uuid.UUID) -> list[Fact]:
        """Get all facts linked to an edge."""
        return await self._edge_repo.get_edge_facts(edge_id)

    async def delete_non_structural_edges(self, node_id: uuid.UUID) -> int:
        """Delete all non-structural edges for a node."""
        return await self._edge_repo.delete_non_structural_edges(node_id)

    # ── Fact linking ─────────────────────────────────────────────────

    async def link_fact_to_node(
        self,
        node_id: uuid.UUID,
        fact_id: uuid.UUID,
        relevance: float = 1.0,
        stance: str | None = None,
    ) -> NodeFact | None:
        """Link a fact to a node.

        When a write session is available, always routes through write-db
        (appends to WriteNode.fact_ids). The sync worker creates NodeFact
        junction rows in graph-db. This avoids FK violations when the fact
        exists in write-db but hasn't been synced to graph-db yet.

        Falls back to graph-db only when no write session is available
        (API / read-only contexts).
        """
        if self._write_node_repo is not None:
            # Try cache first for node key
            if node_id in self._node_cache:
                from kt_db.keys import make_node_key

                node = self._node_cache[node_id]
                node_key = make_node_key(node.node_type, node.concept)
            else:
                # Look up existing node in write-db
                wn = await self._write_node_repo.get_by_uuid(node_id)
                if wn is not None:
                    node_key = wn.key
                else:
                    # Node not in write-db — fall through to graph-db
                    return await _retry_on_deadlock(
                        self._session,
                        lambda: self._fact_repo.link_to_node(
                            node_id, fact_id, relevance_score=relevance, stance=stance
                        ),
                    )
            await self._write_node_repo.append_fact_id(node_key, str(fact_id))
            await self._write_session.commit()  # type: ignore[union-attr]
            return None

        return await _retry_on_deadlock(
            self._session,
            lambda: self._fact_repo.link_to_node(node_id, fact_id, relevance_score=relevance, stance=stance),
        )

    async def unlink_fact_from_node(self, node_id: uuid.UUID, fact_id: uuid.UUID) -> bool:
        """Remove a fact-to-node link.

        When a write session is available, always routes through write-db
        (removes from WriteNode.fact_ids). Falls back to graph-db only
        when no write session is available.
        """
        if self._write_node_repo is not None:
            if node_id in self._node_cache:
                from kt_db.keys import make_node_key

                node = self._node_cache[node_id]
                node_key = make_node_key(node.node_type, node.concept)
            else:
                wn = await self._write_node_repo.get_by_uuid(node_id)
                if wn is not None:
                    node_key = wn.key
                else:
                    return await self._fact_repo.unlink_from_node(node_id, fact_id)
            await self._write_node_repo.remove_fact_id(node_key, str(fact_id))
            await self._write_session.commit()  # type: ignore[union-attr]
            return True
        return await self._fact_repo.unlink_from_node(node_id, fact_id)

    async def get_fact_ids_for_nodes(
        self,
        node_ids: list[uuid.UUID],
    ) -> dict[uuid.UUID, set[uuid.UUID]]:
        """Return {node_id: set(fact_id)} for the given nodes."""
        return await self._fact_repo.get_fact_ids_for_nodes(node_ids)

    async def get_node_facts(self, node_id: uuid.UUID) -> list[Fact]:
        """Get all facts linked to a node.

        Routes to write-db when available: reads ``fact_ids`` from WriteNode,
        then loads WriteFact objects and converts to Fact. Falls back to
        graph-db for API (read-only) contexts.
        """
        if self._write_node_repo is not None and self._write_fact_repo is not None:
            wn = await self._write_node_repo.get_by_uuid(node_id)
            if wn and wn.fact_ids:
                fact_uuids = [uuid.UUID(fid) for fid in wn.fact_ids]
                write_facts = await self._write_fact_repo.get_by_ids(fact_uuids)
                return [Fact(id=wf.id, content=wf.content, fact_type=wf.fact_type) for wf in write_facts]
            return []
        return await self._fact_repo.get_facts_by_node(node_id)

    async def get_node_facts_with_sources(self, node_id: uuid.UUID) -> list[Fact]:
        """Get all facts linked to a node with sources eagerly loaded."""
        return await self._fact_repo.get_facts_by_node_with_sources(node_id)

    # ── Fact management ────────────────────────────────────────────────

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
        return await self._fact_repo.list_paginated(
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
        return await self._fact_repo.count(
            search=search,
            fact_type=fact_type,
            author_org=author_org,
            source_domain=source_domain,
        )

    async def update_fact(self, fact_id: uuid.UUID, **kwargs: object) -> Fact:
        """Update a fact's fields and return the refreshed fact.

        When write-db is available: updates WriteFact in write-db.
        Falls back to graph-db for the return value (Fact model).
        """
        if self._write_fact_repo is not None:
            await self._write_fact_repo.update_fields(fact_id, **kwargs)
            await self._write_session.commit()  # type: ignore[union-attr]
        else:
            await self._fact_repo.update_fields(fact_id, **kwargs)
        fact = await self._fact_repo.get_by_id(fact_id)
        if fact is None:
            raise ValueError(f"Fact not found: {fact_id}")
        await self._session.refresh(fact)
        return fact

    async def get_fact_nodes(self, fact_id: uuid.UUID) -> list[tuple[Node, NodeFact]]:
        """Get all nodes linked to a fact with their link metadata."""
        return await self._fact_repo.get_nodes_for_fact(fact_id)

    async def delete_fact(self, fact_id: uuid.UUID) -> bool:
        """Delete a fact by ID."""
        return await self._fact_repo.delete(fact_id)

    # ── Dimension management ─────────────────────────────────────────

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
    ) -> Dimension | None:
        """Add a dimension (model perspective) to a node.

        When write-db is available: writes to write-db ONLY with fact_ids.
        The sync worker creates the graph-db Dimension + DimensionFact rows.
        Returns None when using write-db.
        """
        if self._write_dim_repo is not None:
            from kt_db.keys import make_node_key

            node = await self._get_cached_or_db(node_id)
            if not node:
                logger.error(
                    "add_dimension: node %s not found in cache, write-db, or graph-db — dimension will NOT be stored",
                    node_id,
                )
                return None
            if node:
                node_key = make_node_key(node.node_type, node.concept)
                fact_id_strs = [str(fid) for fid in fact_ids] if fact_ids else None
                await self._write_dim_repo.upsert(
                    node_key=node_key,
                    model_id=model_id,
                    content=content,
                    confidence=confidence,
                    suggested_concepts=suggested_concepts,
                    batch_index=batch_index,
                    fact_count=fact_count,
                    is_definitive=is_definitive,
                    fact_ids=fact_id_strs,
                )
                await self._write_session.commit()  # type: ignore[union-attr]
            return None

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
        self._session.add(dim)
        await self._session.flush()

        if fact_ids:
            for fid in fact_ids:
                df = DimensionFact(dimension_id=dim.id, fact_id=fid)
                self._session.add(df)
            await self._session.flush()

        return dim

    async def get_dimensions(self, node_id: uuid.UUID) -> list[Dimension]:
        """Get all dimensions for a node.

        Routes to write-db when available.
        """
        if self._write_dim_repo is not None and self._write_node_repo is not None:
            wn = await self._write_node_repo.get_by_uuid(node_id)
            if wn is not None:
                write_dims = await self._write_dim_repo.get_by_node_key(wn.key)
                return [
                    Dimension(
                        node_id=node_id,
                        model_id=wd.model_id,
                        content=wd.content,
                        confidence=wd.confidence,
                    )
                    for wd in write_dims
                ]
            return []
        stmt = select(Dimension).where(Dimension.node_id == node_id)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def get_dimensions_with_facts(self, node_id: uuid.UUID) -> list[Dimension]:
        """Get all dimensions for a node with dimension_facts eagerly loaded."""
        stmt = select(Dimension).where(Dimension.node_id == node_id).options(selectinload(Dimension.dimension_facts))
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def delete_dimensions(self, node_id: uuid.UUID) -> int:
        """Delete all dimensions for a node. Returns count deleted."""
        stmt = delete(Dimension).where(Dimension.node_id == node_id)
        result = await self._session.execute(stmt)
        return result.rowcount  # type: ignore[return-value]

    async def delete_dimension(self, dimension_id: uuid.UUID) -> bool:
        """Delete a single dimension by ID. Returns True if deleted."""
        stmt = delete(Dimension).where(Dimension.id == dimension_id)
        result = await self._session.execute(stmt)
        return result.rowcount > 0  # type: ignore[operator]

    # ── Node definition ───────────────────────────────────────────────

    async def set_node_definition(
        self,
        node_id: uuid.UUID,
        definition: str,
        source: str = "synthesized",
    ) -> None:
        """Set the synthesized definition for a node.

        When write-db is available: writes to write-db ONLY.
        The sync worker propagates to graph-db.
        """
        if self._write_node_repo is not None:
            node = await self._get_cached_or_db(node_id)
            if node:
                await self._write_node_repo.upsert(
                    node_type=node.node_type,
                    concept=node.concept,
                    definition=definition,
                    definition_source=source,
                )
                await self._write_session.commit()  # type: ignore[union-attr]
                # Update cache so pipeline can read definition back
                if node_id in self._node_cache:
                    self._node_cache[node_id].definition = definition
                    self._node_cache[node_id].definition_source = source
            return

        # Fallback: no write-db, write directly to graph-db
        await self._node_repo.update_fields(
            node_id,
            definition=definition,
            definition_source=source,
            definition_generated_at=_utcnow(),
        )

    # ── Versioning ───────────────────────────────────────────────────

    async def save_version(self, node_id: uuid.UUID) -> NodeVersion:
        """Save a snapshot of the current node state as a new version."""
        node = await self._node_repo.get_by_id(node_id)
        if node is None:
            raise ValueError(f"Node not found: {node_id}")

        max_ver_stmt = select(func.coalesce(func.max(NodeVersion.version_number), 0)).where(
            NodeVersion.node_id == node_id
        )
        result = await self._session.execute(max_ver_stmt)
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
        self._session.add(version)
        await self._session.flush()
        return version

    async def get_node_history(self, node_id: uuid.UUID) -> list[NodeVersion]:
        """Get the version history for a node, ordered by version number."""
        stmt = select(NodeVersion).where(NodeVersion.node_id == node_id).order_by(NodeVersion.version_number)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    # ── Node merging ───────────────────────────────────────────────

    async def merge_nodes(self, keep_id: uuid.UUID, absorb_id: uuid.UUID) -> Node:
        """Merge absorb_id into keep_id."""
        id_a, id_b = sorted([keep_id, absorb_id])
        lock_a = id_a.int & 0x7FFFFFFF
        lock_b = id_b.int & 0x7FFFFFFF
        await self._session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": lock_a})
        if lock_a != lock_b:
            await self._session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": lock_b})

        keep = await self._node_repo.get_by_id(keep_id)
        if keep is None:
            raise ValueError(f"Keep node not found: {keep_id}")
        absorb = await self._node_repo.get_by_id(absorb_id)
        if absorb is None:
            raise ValueError(f"Absorb node not found: {absorb_id}")

        absorb_facts_stmt = select(NodeFact).where(NodeFact.node_id == absorb_id)
        result = await self._session.execute(absorb_facts_stmt)
        absorb_nfs = list(result.scalars().all())
        for nf in absorb_nfs:
            try:
                await self._fact_repo.link_to_node(keep_id, nf.fact_id, nf.relevance_score)
            except Exception:
                pass

        absorb_edges = await self._edge_repo.get_edges(absorb_id, direction="both")
        for edge in absorb_edges:
            new_source = keep_id if edge.source_node_id == absorb_id else edge.source_node_id
            new_target = keep_id if edge.target_node_id == absorb_id else edge.target_node_id
            if new_source == new_target:
                continue
            try:
                existing_edge = await self._edge_repo.get_edge(
                    new_source,
                    new_target,
                    edge.relationship_type,
                )
                if existing_edge:
                    merged_weight = (existing_edge.weight + edge.weight) / 2.0
                    existing_edge.weight = merged_weight
                    existing_edge.updated_at = _utcnow()
                    await self._session.flush()
                    target_edge_id = existing_edge.id
                else:
                    new_edge = await self._edge_repo.create(
                        source_node_id=new_source,
                        target_node_id=new_target,
                        relationship_type=edge.relationship_type,
                        weight=edge.weight,
                    )
                    target_edge_id = new_edge.id

                for ef in edge.edge_facts:
                    await self._edge_repo.link_fact_to_edge(target_edge_id, ef.fact_id, ef.relevance_score)
            except Exception:
                logger.debug(
                    "Error redirecting edge %s during merge",
                    edge.id,
                    exc_info=True,
                )

        dim_stmt = select(Dimension).where(Dimension.node_id == absorb_id)
        dim_result = await self._session.execute(dim_stmt)
        absorb_dims = list(dim_result.scalars().all())
        for dim in absorb_dims:
            dim.node_id = keep_id
        await self._session.flush()

        await self._node_repo.delete(absorb_id)

        await self._session.refresh(keep)
        return keep

    # ── Perspective & staleness ────────────────────────────────────────

    async def get_perspectives(self, concept_node_id: uuid.UUID) -> list[Node]:
        """Get all perspective nodes for a concept.

        Routes to write-db when available using source_concept_key.
        """
        if self._write_node_repo is not None:
            from sqlalchemy import select as _sel

            from kt_db.write_models import WriteNode as _WN

            parent_wn = await self._write_node_repo.get_by_uuid(concept_node_id)
            if parent_wn is not None:
                stmt = _sel(_WN).where(
                    _WN.source_concept_key == parent_wn.key,
                    _WN.node_type == "perspective",
                )
                result = await self._write_session.execute(stmt)  # type: ignore[union-attr]
                return [
                    Node(id=wn.node_uuid, concept=wn.concept, node_type=wn.node_type) for wn in result.scalars().all()
                ]
            return []
        return await self._node_repo.get_perspectives_for_concept(concept_node_id)

    async def get_stale_nodes(self, max_age_days: int = 30, limit: int = 20) -> list[Node]:
        """Get nodes that are overdue for refresh."""
        return await self._node_repo.get_stale_nodes(max_age_days=max_age_days, limit=limit)

    async def list_all_nodes(self) -> list[Node]:
        """Return all nodes ordered by updated_at descending."""
        return await self._node_repo.list_all()

    async def list_all_edges(self) -> list[Edge]:
        """Return all edges with edge_facts eagerly loaded."""
        return await self._edge_repo.list_all()

    async def list_all_facts_with_sources(self) -> list[Fact]:
        """Return all facts with eagerly-loaded sources."""
        return await self._fact_repo.list_all_with_sources()

    async def search_fact_pool(
        self,
        embedding: list[float],
        limit: int = 30,
        threshold: float = 0.5,
    ) -> list[Fact]:
        """Search all facts by embedding similarity (fact pool pattern).

        Requires Qdrant for vector search.
        """
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

    async def search_fact_pool_text(self, query: str, limit: int = 30) -> list[Fact]:
        """Text search across all facts (fact pool pattern).

        Routes to write-db when available, falls back to graph-db.
        """
        if self._write_fact_repo is not None:
            write_facts = await self._write_fact_repo.search_text(query, limit=limit)
            return [Fact(id=wf.id, content=wf.content, fact_type=wf.fact_type) for wf in write_facts]
        return await self._fact_repo.search_fact_pool_text(query, limit=limit)

    def is_node_stale(self, node: Node) -> bool:
        """Check whether a node is past its stale_after window."""
        if node.updated_at is None or node.stale_after is None:
            return True
        now = _utcnow()
        stale_cutoff = node.updated_at + timedelta(days=node.stale_after)
        return now > stale_cutoff

    async def find_nodes_sharing_facts(
        self,
        node_id: uuid.UUID,
        limit: int = 20,
    ) -> list[tuple[uuid.UUID, str, list[uuid.UUID]]]:
        """Find nodes that share facts with the given node.

        Routes to write-db when available (uses fact_ids array overlap),
        falls back to graph-db (uses node_facts junction table).
        """
        if self._write_fact_repo is not None:
            return await self._write_fact_repo.find_nodes_sharing_facts(node_id, limit=limit)
        return await self._fact_repo.find_nodes_sharing_facts(node_id, limit=limit)

    async def find_nodes_by_embedding_facts(
        self,
        query_embedding: list[float],
        source_node_id: uuid.UUID,
        threshold: float = 0.45,
        node_limit: int = 15,
    ) -> list[tuple[uuid.UUID, str, list[uuid.UUID]]]:
        """Find nodes via embedding-similar facts.

        Requires Qdrant for vector search; relational join uses write-db
        (WriteNode.fact_ids) when available, else graph-db (NodeFact junction).
        """
        if self._qdrant_fact_repo is None:
            logger.error("find_nodes_by_embedding_facts called but Qdrant fact repo is not available")
            return []

        # Get fact IDs already linked to source node (to exclude)
        source_fact_ids: list[uuid.UUID] = []
        if self._write_fact_repo is not None and self._write_node_repo is not None:
            # Read source node's fact_ids from write-db
            wn = await self._write_node_repo.get_by_uuid(source_node_id)
            if wn is not None and wn.fact_ids:
                source_fact_ids = [uuid.UUID(fid) for fid in wn.fact_ids]
        else:
            source_facts_stmt = select(NodeFact.fact_id).where(NodeFact.node_id == source_node_id)
            source_result = await self._session.execute(source_facts_stmt)
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

        # Relational join: which nodes own these facts?
        if self._write_fact_repo is not None:
            return await self._write_fact_repo.find_nodes_by_embedding_facts(
                candidate_fact_ids,
                source_node_id,
                node_limit=node_limit,
            )

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
        result = await self._session.execute(stmt)
        return [(row[0], row[1], list(row[2])) for row in result.all()]

    async def find_nodes_by_text_facts(
        self,
        query: str,
        source_node_id: uuid.UUID,
        threshold: float = 0.3,
        node_limit: int = 10,
    ) -> list[tuple[uuid.UUID, str, list[uuid.UUID]]]:
        """Find nodes via text-matching facts using pg_trgm.

        Routes to write-db when available, falls back to graph-db.
        """
        if self._write_fact_repo is not None:
            return await self._write_fact_repo.find_nodes_by_text_facts(
                query,
                source_node_id,
                threshold=threshold,
                node_limit=node_limit,
            )
        return await self._fact_repo.find_nodes_by_text_facts(
            query,
            source_node_id,
            threshold=threshold,
            node_limit=node_limit,
        )

    async def search_nodes_by_trigram(
        self,
        query: str,
        threshold: float = 0.3,
        limit: int = 5,
        node_type: str | None = None,
    ) -> list[Node]:
        """Search nodes by concept using pg_trgm similarity.

        Routes to write-db when available to avoid graph-db pool pressure
        during concurrent pipeline fan-out.
        """
        if self._write_node_repo is not None:
            from kt_db.keys import key_to_uuid

            write_nodes = await self._write_node_repo.search_by_trigram(
                query,
                threshold=threshold,
                limit=limit,
                node_type=node_type,
            )
            return [
                Node(
                    id=wn.node_uuid,
                    concept=wn.concept,
                    node_type=wn.node_type,
                    definition=wn.definition,
                    metadata_=wn.metadata_,
                    parent_id=key_to_uuid(wn.parent_key) if wn.parent_key else None,
                    stale_after=wn.stale_after,
                    created_at=wn.created_at,
                    updated_at=wn.updated_at,
                )
                for wn in write_nodes
            ]
        return await self._require_node_repo().search_by_trigram(
            query,
            threshold=threshold,
            limit=limit,
            node_type=node_type,
        )

    async def search_fact_pool_trigram(
        self,
        query: str,
        threshold: float = 0.3,
        limit: int = 30,
    ) -> list[Fact]:
        """Search facts using trigram word_similarity (pg_trgm).

        Routes to write-db when available, falls back to graph-db.
        """
        if self._write_fact_repo is not None:
            write_facts = await self._write_fact_repo.search_trigram(query, threshold=threshold, limit=limit)
            return [Fact(id=wf.id, content=wf.content, fact_type=wf.fact_type) for wf in write_facts]
        return await self._fact_repo.search_fact_pool_trigram(query, threshold=threshold, limit=limit)

    # ── Fact rejection ──────────────────────────────────────────────

    async def record_fact_rejection(
        self,
        node_id: uuid.UUID,
        fact_id: uuid.UUID,
    ) -> bool:
        """Record that a fact was rejected as irrelevant for a node.

        Routes to write-db when available, falls back to graph-db.
        """
        if self._write_fact_repo is not None:
            return await self._write_fact_repo.record_fact_rejection(node_id, fact_id)
        return await self._fact_repo.record_fact_rejection(node_id, fact_id)

    async def get_rejected_fact_ids(self, node_id: uuid.UUID) -> set[uuid.UUID]:
        """Get all fact IDs rejected for a given node.

        Routes to write-db when available, falls back to graph-db.
        """
        if self._write_fact_repo is not None:
            return await self._write_fact_repo.get_rejected_fact_ids(node_id)
        return await self._fact_repo.get_rejected_fact_ids(node_id)

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
        if self._write_fact_repo is not None:
            write_facts = await self._write_fact_repo.search_text_excluding_rejected(
                query,
                node_id,
                limit=limit,
            )
            return [Fact(id=wf.id, content=wf.content, fact_type=wf.fact_type) for wf in write_facts]
        return await self._fact_repo.search_fact_pool_text_excluding_rejected(
            query,
            node_id,
            limit=limit,
        )

    async def get_recent_edge_pairs(
        self,
        node_id: uuid.UUID,
        candidate_ids: list[uuid.UUID],
        staleness_days: int = 30,
    ) -> set[uuid.UUID]:
        """Return candidate node IDs that already have recent non-structural edges.

        Routes to write-db when available to avoid graph-db pool pressure.
        """
        if self._write_edge_repo is not None:
            return await self._write_edge_repo.get_recent_edge_pairs(
                node_id,
                candidate_ids,
                staleness_days=staleness_days,
            )
        return await self._edge_repo.get_recent_edge_pairs(
            node_id,
            candidate_ids,
            staleness_days=staleness_days,
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
            result = await self._session.execute(stmt)
            for row in result.all():
                nid = row[0]
                node_counts[nid] = node_counts.get(nid, 0) + 1

        sorted_nodes = sorted(node_counts.items(), key=lambda x: x[1], reverse=True)
        return sorted_nodes[:limit]

    async def get_node_facts_with_stance(self, node_id: uuid.UUID) -> list[tuple[Fact, str | None]]:
        """Get all facts linked to a node with their stance classification."""
        return await self._fact_repo.get_facts_by_node_with_stance(node_id)

    async def get_perspective_summary(self, node_id: uuid.UUID) -> dict[str, int]:
        """Return counts of supporting/challenging/neutral facts for a node."""
        facts_with_stance = await self._fact_repo.get_facts_by_node_with_stance(node_id)
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

    # ── Richness score ───────────────────────────────────────────────

    def compute_richness(self, node: Node, fact_count: int, dimension_count: int) -> float:
        """Compute a richness score for a node."""
        raw = fact_count * 0.1 + dimension_count * 0.2 + node.access_count * 0.01
        return min(1.0, raw)

    # ── Path finding ─────────────────────────────────────────────────

    async def find_shortest_paths(
        self,
        source_id: uuid.UUID,
        target_id: uuid.UUID,
        max_depth: int = 6,
        limit: int = 5,
    ) -> list[list[PathStep]]:
        """Find shortest paths between two nodes using level-order BFS.

        Uses bulk edge loading per BFS level to avoid N+1 queries.
        Total queries = O(depth) instead of O(nodes_explored).
        """
        if source_id == target_id:
            return [[PathStep(node_id=source_id, edge=None)]]

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
            edges_by_node = await self._edge_repo.get_edges_for_nodes(frontier_ids, direction="both")

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
