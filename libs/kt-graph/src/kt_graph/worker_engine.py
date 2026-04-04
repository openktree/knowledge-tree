"""Worker-only graph engine that operates exclusively on write-db + Qdrant.

Workers that build the graph (node pipeline, ingest, bottom-up, etc.) use
this engine.  It has NO graph-db session at all -- every read/write goes
through the write-optimised database and Qdrant vector store.  The sync
worker is responsible for propagating changes to graph-db.
"""

from __future__ import annotations

import logging
import uuid
from collections import OrderedDict
from datetime import timedelta
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from qdrant_client import AsyncQdrantClient

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from kt_db.keys import key_to_uuid, make_node_key
from kt_db.models import Dimension, DimensionFact, Edge, Fact, Node, _utcnow
from kt_db.repositories.write_dimensions import WriteDimensionRepository
from kt_db.repositories.write_edges import WriteEdgeRepository
from kt_db.repositories.write_facts import WriteFactRepository
from kt_db.repositories.write_nodes import WriteNodeRepository
from kt_models.embeddings import EmbeddingService

logger = logging.getLogger(__name__)


class WorkerGraphEngine:
    """Graph engine for worker services -- write-db + Qdrant only.

    Workers never need a graph-db session.  All reads come from write-db
    (or the in-memory node cache for pipeline-created nodes), and all writes
    go to write-db.  Vector search is backed by Qdrant.
    """

    def __init__(
        self,
        write_session: AsyncSession,
        embedding_service: EmbeddingService | None = None,
        qdrant_client: AsyncQdrantClient | None = None,
    ) -> None:
        self._write_session = write_session
        self._embedding_service = embedding_service

        # Write repositories — always initialized (write_session is required)
        self._write_node_repo = WriteNodeRepository(write_session)
        self._write_edge_repo = WriteEdgeRepository(write_session)
        self._write_dim_repo = WriteDimensionRepository(write_session)
        self._write_fact_repo = WriteFactRepository(write_session)

        # Qdrant repositories (optional)
        self._qdrant_fact_repo = None
        self._qdrant_node_repo = None
        if qdrant_client is not None:
            from kt_qdrant.repositories.facts import QdrantFactRepository
            from kt_qdrant.repositories.nodes import QdrantNodeRepository

            self._qdrant_fact_repo = QdrantFactRepository(qdrant_client)
            self._qdrant_node_repo = QdrantNodeRepository(qdrant_client)

        # In-memory LRU cache for nodes created in this pipeline run.
        # Capped to prevent unbounded growth in long-running pipelines.
        self._node_cache: OrderedDict[uuid.UUID, Node] = OrderedDict()
        self._node_cache_max = 5000

        # Cache for edge UUID -> write-db key, populated by create_edge().
        # Capped to match _node_cache.
        self._edge_key_cache: OrderedDict[uuid.UUID, str] = OrderedDict()
        self._edge_key_cache_max = 5000

    # ── Properties ────────────────────────────────────────────────────

    @property
    def has_write_db(self) -> bool:
        return self._write_session is not None

    @property
    def has_graph_db(self) -> bool:
        return False

    async def commit(self) -> None:
        """Commit the write-db session.

        Callers should use this instead of accessing _write_session directly.
        """
        if self._write_session is not None:
            await self._write_session.commit()

    async def rollback(self) -> None:
        """Rollback the write-db session."""
        if self._write_session is not None:
            await self._write_session.rollback()

    # ── Helpers ───────────────────────────────────────────────────────

    @staticmethod
    def _write_node_to_node(wn: Any, **overrides: Any) -> Node:
        """Convert a WriteNode to a graph-db Node model instance.

        Centralises the WriteNode -> Node mapping so every call-site stays
        consistent.  Pass ``**overrides`` to replace specific fields (e.g.
        ``metadata_=custom_meta``).
        """
        fields: dict[str, Any] = {
            "id": wn.node_uuid,
            "concept": wn.concept,
            "node_type": wn.node_type,
            "entity_subtype": wn.entity_subtype,
            "parent_id": key_to_uuid(wn.parent_key) if wn.parent_key else None,
            "stale_after": wn.stale_after,
            "definition": wn.definition,
            "definition_source": wn.definition_source,
            "metadata_": wn.metadata_,
            "access_count": 0,
            "update_count": 0,
            "created_at": wn.created_at,
            "updated_at": wn.updated_at,
        }
        fields.update(overrides)
        return Node(**fields)

    async def _get_cached_or_write_db(self, node_id: uuid.UUID) -> Node | None:
        """Look up a node from cache, then write-db.

        Nodes created in the current pipeline run are cached and won't exist
        in graph-db until the sync worker propagates them.
        """
        cached = self._node_cache.get(node_id)
        if cached is not None:
            return cached
        wn = await self._write_node_repo.get_by_uuid(node_id)
        if wn is not None:
            return self._write_node_to_node(wn)
        return None

    def _write_fact_to_fact(self, wf: Any) -> Fact:
        """Convert a WriteFact to a graph-db Fact model instance."""
        return Fact(id=wf.id, content=wf.content, fact_type=wf.fact_type)

    async def _load_facts_preserving_order(self, fact_ids: list[uuid.UUID]) -> list[Fact]:
        """Load Fact objects by IDs from write-db, preserving the given order."""
        if not fact_ids:
            return []
        write_facts = await self._write_fact_repo.get_by_ids(fact_ids)
        wf_by_id = {wf.id: wf for wf in write_facts}
        return [self._write_fact_to_fact(wf_by_id[fid]) for fid in fact_ids if fid in wf_by_id]

    # ── Qdrant helpers ────────────────────────────────────────────────

    async def upsert_fact_to_qdrant(
        self,
        fact_id: uuid.UUID,
        embedding: list[float],
        fact_type: str | None = None,
        content: str | None = None,
    ) -> None:
        """Upsert a fact embedding to Qdrant (no-op if Qdrant not available)."""
        if self._qdrant_fact_repo is not None:
            try:
                await self._qdrant_fact_repo.upsert(
                    fact_id,
                    embedding,
                    fact_type=fact_type,
                    content=content,
                )
            except Exception:
                logger.warning("Failed to upsert fact %s to Qdrant", fact_id, exc_info=True)

    async def upsert_facts_to_qdrant(
        self,
        facts: list[tuple[uuid.UUID, list[float], str | None]]
        | list[tuple[uuid.UUID, list[float], str | None, str | None]]
        | list[tuple[uuid.UUID, list[float], str | None, str | None, list[uuid.UUID] | None]],
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

    async def _update_qdrant_node_id(
        self,
        fact_id: uuid.UUID,
        node_id: uuid.UUID,
        *,
        append: bool,
    ) -> None:
        """Append or remove a node_id in Qdrant fact payload (best-effort)."""
        if self._qdrant_fact_repo is None:
            return
        try:
            if append:
                await self._qdrant_fact_repo.append_node_id(fact_id, node_id)
            else:
                await self._qdrant_fact_repo.remove_node_id(fact_id, node_id)
        except Exception as exc:
            from qdrant_client.http.exceptions import ApiException

            if not isinstance(exc, (ConnectionError, TimeoutError, OSError, ApiException)):
                raise
            logger.warning(
                "Failed to %s node_id in Qdrant for fact %s",
                "append" if append else "remove",
                fact_id,
                exc_info=True,
            )

    # ── Node write operations ─────────────────────────────────────────

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
        """Create a new node in write-db + Qdrant.

        An in-memory Node object is returned (cached for subsequent methods).
        The sync worker propagates to graph-db.
        """
        node_key = make_node_key(node_type, concept)
        det_uuid = key_to_uuid(node_key)

        # Resolve parent/source keys for write-db
        parent_key: str | None = None
        if parent_id is not None:
            parent_node = await self._get_cached_or_write_db(parent_id)
            if parent_node:
                parent_key = make_node_key(parent_node.node_type, parent_node.concept)

        source_key: str | None = None
        if source_concept_id is not None:
            source_node = await self._get_cached_or_write_db(source_concept_id)
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
        await self.commit()

        # Build in-memory Node object (not persisted to graph-db).
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
        if len(self._node_cache) > self._node_cache_max:
            self._node_cache.popitem(last=False)  # evict oldest

        # Upsert embedding to Qdrant for vector search
        if embedding is not None and self._qdrant_node_repo is not None:
            try:
                await self._qdrant_node_repo.upsert(det_uuid, embedding, node_type=node_type, concept=concept)
            except Exception:
                logger.warning("Failed to upsert node %s to Qdrant", det_uuid, exc_info=True)
        return node

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
    ) -> None:
        """Create or update an edge in write-db.

        Returns None -- the edge appears in graph-db after sync.
        """
        source_node = await self._get_cached_or_write_db(source_id)
        target_node = await self._get_cached_or_write_db(target_id)
        if source_node is None or target_node is None:
            logger.warning("create_edge: source or target node not found")
            return

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
        await self.commit()

        # Cache edge UUID -> key for link_fact_to_edge
        self._edge_key_cache[key_to_uuid(edge_key)] = edge_key

    async def set_parent(self, node_id: uuid.UUID, parent_id: uuid.UUID) -> None:
        """Set the tree parent of a node in write-db."""
        if node_id == parent_id:
            raise ValueError(f"Cannot set node {node_id} as its own parent")
        ok, reason = await self._validate_parent_chain(node_id, parent_id)
        if not ok:
            raise ValueError(f"Invalid parent {parent_id} for node {node_id}: {reason}")

        node = await self._get_cached_or_write_db(node_id)
        parent = await self._get_cached_or_write_db(parent_id)
        if node and parent:
            await self._write_node_repo.upsert(
                node_type=node.node_type,
                concept=node.concept,
                parent_key=make_node_key(parent.node_type, parent.concept),
            )
            await self.commit()
            # Update cache so subsequent parent chain validation works
            if node_id in self._node_cache:
                self._node_cache[node_id].parent_id = parent_id

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
            parent_node = await self._get_cached_or_write_db(current)
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
            node = await self._get_cached_or_write_db(current)
            if node is None or node.parent_id is None:
                return False
            current = node.parent_id
        return False

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
    ) -> None:
        """Add a dimension (model perspective) to a node in write-db.

        Returns None -- the sync worker creates graph-db Dimension + DimensionFact rows.
        """
        node = await self._get_cached_or_write_db(node_id)
        if not node:
            logger.error(
                "add_dimension: node %s not found in cache or write-db -- dimension will NOT be stored",
                node_id,
            )
            return
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
        await self.commit()

    async def delete_dimensions(self, node_id: uuid.UUID) -> int:
        """Delete all dimensions for a node from write-db. Returns count deleted.

        Note: pre-existing graph-db dimensions are cleaned by the sync worker
        when it processes the write-db deletions.
        """
        wn = await self._write_node_repo.get_by_uuid(node_id)
        if wn is None:
            return 0
        return await self._write_dim_repo.delete_all_for_node(wn.key)

    async def delete_dimension(self, dimension_id: uuid.UUID, *, write_key: str | None = None) -> bool:
        """Delete a single dimension by write_key. Returns True if deleted."""
        if write_key:
            return await self._write_dim_repo.delete_by_key(write_key)
        return False

    async def set_node_definition(
        self,
        node_id: uuid.UUID,
        definition: str,
        source: str = "synthesized",
    ) -> None:
        """Set the synthesized definition for a node in write-db."""
        node = await self._get_cached_or_write_db(node_id)
        if node:
            await self._write_node_repo.upsert(
                node_type=node.node_type,
                concept=node.concept,
                definition=definition,
                definition_source=source,
            )
            await self.commit()
            # Update cache so pipeline can read definition back
            if node_id in self._node_cache:
                self._node_cache[node_id].definition = definition
                self._node_cache[node_id].definition_source = source

    async def link_fact_to_node(
        self,
        node_id: uuid.UUID,
        fact_id: uuid.UUID,
        relevance: float = 1.0,
        stance: str | None = None,
    ) -> None:
        """Link a fact to a node via write-db (appends to WriteNode.fact_ids).

        Also updates the Qdrant fact payload with the new node_id so that
        synthesis fact linking (MatchAny on node_ids) works immediately.
        """
        # Try cache first for node key
        if node_id in self._node_cache:
            node = self._node_cache[node_id]
            node_key = make_node_key(node.node_type, node.concept)
        else:
            wn = await self._write_node_repo.get_by_uuid(node_id)
            if wn is None:
                logger.warning("link_fact_to_node: node %s not found in write-db", node_id)
                return
            node_key = wn.key
        await self._write_node_repo.append_fact_id(node_key, str(fact_id))
        await self.commit()
        await self._update_qdrant_node_id(fact_id, node_id, append=True)

    async def unlink_fact_from_node(self, node_id: uuid.UUID, fact_id: uuid.UUID) -> bool:
        """Remove a fact-to-node link in write-db.

        Also updates the Qdrant fact payload to remove the node_id.
        """
        if node_id in self._node_cache:
            node = self._node_cache[node_id]
            node_key = make_node_key(node.node_type, node.concept)
        else:
            wn = await self._write_node_repo.get_by_uuid(node_id)
            if wn is None:
                logger.warning("unlink_fact_from_node: node %s not found in write-db", node_id)
                return False
            node_key = wn.key
        await self._write_node_repo.remove_fact_id(node_key, str(fact_id))
        await self.commit()
        await self._update_qdrant_node_id(fact_id, node_id, append=False)
        return True

    async def link_fact_to_edge(
        self,
        edge_id: uuid.UUID,
        fact_id: uuid.UUID,
        relevance_score: float = 1.0,
    ) -> None:
        """Link a fact to an edge via write-db (appends to WriteEdge.fact_ids)."""
        edge_key = self._edge_key_cache.get(edge_id)
        if edge_key is None:
            logger.warning(
                "link_fact_to_edge: edge %s not in cache — edge was likely "
                "created in a previous pipeline run. Fact link will be "
                "established by the sync worker instead.",
                edge_id,
            )
            return
        await self._write_edge_repo.append_fact_id(edge_key, str(fact_id))
        await self.commit()

    async def increment_access_count(self, node_id: uuid.UUID) -> None:
        """Increment a node's access_count by 1 (best-effort)."""
        node = await self._get_cached_or_write_db(node_id)
        if node:
            try:
                node_key = make_node_key(node.node_type, node.concept)
                await self._write_node_repo.increment_access_count(node_key)
                await self.commit()
            except Exception:
                logger.warning("Non-critical: failed to increment access_count for node %s", node_id)

    async def increment_update_count(self, node_id: uuid.UUID) -> None:
        """Increment a node's update_count by 1 (best-effort)."""
        node = await self._get_cached_or_write_db(node_id)
        if node:
            try:
                node_key = make_node_key(node.node_type, node.concept)
                await self._write_node_repo.increment_update_count(node_key)
                await self.commit()
            except Exception:
                logger.warning("Non-critical: failed to increment update_count for node %s", node_id)

    async def update_node(self, node_id: uuid.UUID, **kwargs: object) -> Node:
        """Update a node's fields via write-db and return the refreshed node."""
        wn = await self._write_node_repo.get_by_uuid(node_id)
        if wn is None:
            raise ValueError(f"Node not found in write-db: {node_id}")

        if "metadata_" in kwargs:
            await self._write_node_repo.update_metadata(wn.key, kwargs.pop("metadata_"))  # type: ignore[arg-type]

        # Pass remaining fields through upsert (concept, node_type, etc.)
        if kwargs:
            upsert_kwargs: dict[str, object] = {
                "node_type": wn.node_type,
                "concept": wn.concept,
            }
            upsert_kwargs.update(kwargs)
            await self._write_node_repo.upsert(**upsert_kwargs)  # type: ignore[arg-type]

        await self.commit()
        # Re-fetch to get updated state
        wn = await self._write_node_repo.get_by_uuid(node_id)
        return self._write_node_to_node(wn)  # type: ignore[arg-type]

    async def record_fact_rejection(
        self,
        node_id: uuid.UUID,
        fact_id: uuid.UUID,
    ) -> bool:
        """Record that a fact was rejected as irrelevant for a node."""
        return await self._write_fact_repo.record_fact_rejection(node_id, fact_id)

    async def get_rejected_fact_ids(self, node_id: uuid.UUID) -> set[uuid.UUID]:
        """Get all fact IDs rejected for a given node."""
        return await self._write_fact_repo.get_rejected_fact_ids(node_id)

    async def snapshot_node(self, node_id: uuid.UUID) -> None:
        """No-op — sync worker handles versioning for write-db nodes.

        Callers that need versioning should use WriteNodeVersionRepository
        directly (e.g. composite.py already does this).
        """
        logger.debug("snapshot_node: no-op on WorkerGraphEngine (node %s)", node_id)

    # ── Node read operations ──────────────────────────────────────────

    async def get_node(self, node_id: uuid.UUID) -> Node | None:
        """Get a node by ID (checks cache first, then write-db)."""
        return await self._get_cached_or_write_db(node_id)

    async def get_nodes_by_ids(self, node_ids: list[uuid.UUID]) -> list[Node]:
        """Get multiple nodes by their IDs, checking cache first."""
        result: list[Node] = []
        missing: list[uuid.UUID] = []
        for nid in node_ids:
            cached = self._node_cache.get(nid)
            if cached is not None:
                result.append(cached)
            else:
                missing.append(nid)
        if missing and self._write_node_repo is not None:
            write_nodes = await self._write_node_repo.get_by_uuids(missing)
            result.extend(self._write_node_to_node(wn) for wn in write_nodes)
        return result

    async def get_node_facts(self, node_id: uuid.UUID) -> list[Fact]:
        """Get all facts linked to a node via write-db fact_ids."""
        wn = await self._write_node_repo.get_by_uuid(node_id)
        if wn and wn.fact_ids:
            fact_uuids = [uuid.UUID(fid) for fid in wn.fact_ids]
            write_facts = await self._write_fact_repo.get_by_ids(fact_uuids)
            return [self._write_fact_to_fact(wf) for wf in write_facts]
        return []

    async def get_node_facts_with_sources(self, node_id: uuid.UUID) -> list[Fact]:
        """Get all facts linked to a node.

        Returns Fact objects with content and fact_type populated.
        Source attribution (FactSource) is not included on the returned
        objects — worker callers only need fact content for LLM prompts.
        For full citation data, use ReadGraphEngine which eagerly loads
        graph-db FactSource relations.
        """
        wn = await self._write_node_repo.get_by_uuid(node_id)
        if wn and wn.fact_ids:
            fact_uuids = [uuid.UUID(fid) for fid in wn.fact_ids]
            write_facts = await self._write_fact_repo.get_facts_with_sources_by_ids(fact_uuids)
            return [self._write_fact_to_fact(wf) for wf in write_facts]
        return []

    async def get_facts_by_ids(self, fact_ids: list[uuid.UUID]) -> list[Fact]:
        """Load facts by ID from write-db, preserving order."""
        return await self._load_facts_preserving_order(fact_ids)

    async def get_edges(self, node_id: uuid.UUID, direction: str = "both") -> list[Edge]:
        """Get all edges connected to a node from write-db."""
        node = await self._get_cached_or_write_db(node_id)
        if node is None:
            return []
        node_key = make_node_key(node.node_type, node.concept)
        write_edges = await self._write_edge_repo.get_edges_for_node(node_key)
        edges: list[Edge] = []
        for we in write_edges:
            edge_uuid = key_to_uuid(we.key)
            # Filter by direction
            src_uuid = key_to_uuid(we.source_node_key)
            tgt_uuid = key_to_uuid(we.target_node_key)
            if direction == "outgoing" and src_uuid != node_id:
                continue
            if direction == "incoming" and tgt_uuid != node_id:
                continue
            edge = Edge(
                id=edge_uuid,
                source_node_id=src_uuid,
                target_node_id=tgt_uuid,
                relationship_type=we.relationship_type,
                weight=we.weight,
                justification=we.justification,
            )
            edges.append(edge)
            # Populate edge key cache for link_fact_to_edge
            self._edge_key_cache[edge_uuid] = we.key
        return edges

    async def get_dimensions(self, node_id: uuid.UUID) -> list[Dimension]:
        """Get all dimensions for a node from write-db."""
        wn = await self._write_node_repo.get_by_uuid(node_id)
        if wn is None:
            return []
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

    async def get_dimensions_with_facts(self, node_id: uuid.UUID) -> list[Dimension]:
        """Get all dimensions for a node with dimension_facts populated from fact_ids."""
        wn = await self._write_node_repo.get_by_uuid(node_id)
        if wn is None:
            return []
        write_dims = await self._write_dim_repo.get_by_node_key(wn.key)
        dims: list[Dimension] = []
        for wd in write_dims:
            dim = Dimension(
                id=key_to_uuid(wd.key),
                node_id=node_id,
                model_id=wd.model_id,
                content=wd.content,
                confidence=wd.confidence,
                batch_index=wd.batch_index,
                is_definitive=wd.is_definitive,
                fact_count=wd.fact_count,
                write_key=wd.key,
            )
            dim.dimension_facts = [DimensionFact(fact_id=uuid.UUID(fid)) for fid in (wd.fact_ids or [])]
            dims.append(dim)
        return dims

    async def get_children(self, parent_id: uuid.UUID) -> list[Node]:
        """Get all child nodes of a given parent from write-db."""
        parent_wn = await self._write_node_repo.get_by_uuid(parent_id)
        if parent_wn is None:
            return []
        children_wn = await self._write_node_repo.get_children_by_parent_key(parent_wn.key)
        return [self._write_node_to_node(wn) for wn in children_wn]

    async def search_nodes(
        self,
        query: str,
        limit: int = 10,
        node_type: str | None = None,
    ) -> list[Node]:
        """Search nodes by concept name from write-db."""
        write_nodes = await self._write_node_repo.search_by_concept(query, limit=limit, node_type=node_type)
        return [self._write_node_to_node(wn) for wn in write_nodes]

    async def search_nodes_by_trigram(
        self,
        query: str,
        threshold: float = 0.3,
        limit: int = 5,
        node_type: str | None = None,
    ) -> list[Node]:
        """Search nodes by concept using pg_trgm similarity on write-db."""
        write_nodes = await self._write_node_repo.search_by_trigram(
            query,
            threshold=threshold,
            limit=limit,
            node_type=node_type,
        )
        return [self._write_node_to_node(wn) for wn in write_nodes]

    async def find_similar_nodes(
        self,
        embedding: list[float],
        threshold: float = 0.3,
        limit: int = 10,
        node_type: str | None = None,
    ) -> list[Node]:
        """Find nodes similar to the given embedding via Qdrant.

        Loads node data from write-db after getting IDs from Qdrant.
        """
        if self._qdrant_node_repo is None:
            logger.error("find_similar_nodes called but Qdrant node repo is not available")
            return []
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
        write_nodes = await self._write_node_repo.get_by_uuids(node_ids)
        id_to_node = {wn.node_uuid: self._write_node_to_node(wn) for wn in write_nodes}
        # Preserve Qdrant ordering
        return [id_to_node[nid] for nid in node_ids if nid in id_to_node]

    # ── Fact search operations ────────────────────────────────────────

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

    async def search_fact_pool_text(self, query: str, limit: int = 30) -> list[Fact]:
        """Text search across all facts from write-db."""
        write_facts = await self._write_fact_repo.search_text(query, limit=limit)
        return [self._write_fact_to_fact(wf) for wf in write_facts]

    async def search_fact_pool_trigram(
        self,
        query: str,
        threshold: float = 0.3,
        limit: int = 30,
    ) -> list[Fact]:
        """Search facts using trigram word_similarity on write-db."""
        write_facts = await self._write_fact_repo.search_trigram(query, threshold=threshold, limit=limit)
        return [self._write_fact_to_fact(wf) for wf in write_facts]

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
        write_facts = await self._write_fact_repo.search_text_excluding_rejected(
            query,
            node_id,
            limit=limit,
        )
        return [self._write_fact_to_fact(wf) for wf in write_facts]

    # ── Fact-based node discovery ─────────────────────────────────────

    async def find_nodes_sharing_facts(
        self,
        node_id: uuid.UUID,
        limit: int = 20,
    ) -> list[tuple[uuid.UUID, str, list[uuid.UUID]]]:
        """Find nodes that share facts with the given node via write-db."""
        return await self._write_fact_repo.find_nodes_sharing_facts(node_id, limit=limit)

    async def find_nodes_by_embedding_facts(
        self,
        query_embedding: list[float],
        source_node_id: uuid.UUID,
        threshold: float = 0.45,
        node_limit: int = 15,
    ) -> list[tuple[uuid.UUID, str, list[uuid.UUID]]]:
        """Find nodes via embedding-similar facts.

        Qdrant for vector search, relational join via write-db.
        """
        if self._qdrant_fact_repo is None:
            logger.error("find_nodes_by_embedding_facts called but Qdrant fact repo is not available")
            return []

        # Get fact IDs already linked to source node (to exclude)
        wn = await self._write_node_repo.get_by_uuid(source_node_id)
        source_fact_ids: list[uuid.UUID] = []
        if wn is not None and wn.fact_ids:
            source_fact_ids = [uuid.UUID(fid) for fid in wn.fact_ids]

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
        return await self._write_fact_repo.find_nodes_by_embedding_facts(
            candidate_fact_ids,
            source_node_id,
            node_limit=node_limit,
        )

    async def find_nodes_by_text_facts(
        self,
        query: str,
        source_node_id: uuid.UUID,
        threshold: float = 0.3,
        node_limit: int = 10,
    ) -> list[tuple[uuid.UUID, str, list[uuid.UUID]]]:
        """Find nodes via text-matching facts using pg_trgm on write-db."""
        return await self._write_fact_repo.find_nodes_by_text_facts(
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

        Uses Qdrant for batch vector search, then aggregates by node
        via write-db fact_ids arrays.
        """
        if self._qdrant_fact_repo is None:
            logger.error("find_nodes_with_similar_facts called but Qdrant fact repo is not available")
            return []

        # Collect all candidate fact IDs from Qdrant vector search
        all_candidate_facts: list[uuid.UUID] = []
        for emb in fact_embeddings:
            results = await self._qdrant_fact_repo.search_similar(
                emb,
                limit=20,
                score_threshold=threshold,
            )
            if results:
                all_candidate_facts.extend(r.fact_id for r in results)

        if not all_candidate_facts:
            return []

        # Use WriteFactRepository's indexed query instead of full table scan
        node_results = await self._write_fact_repo.find_nodes_by_embedding_facts(
            all_candidate_facts,
            exclude_node_id,
            node_limit=limit,
        )
        # Convert from (node_id, concept, fact_ids) to (node_id, count)
        return [(nid, len(fids)) for nid, _concept, fids in node_results]

    # ── Edge queries ──────────────────────────────────────────────────

    async def get_recent_edge_pairs(
        self,
        node_id: uuid.UUID,
        candidate_ids: list[uuid.UUID],
        staleness_days: int = 30,
    ) -> set[uuid.UUID]:
        """Return candidate node IDs that already have recent edges with node_id."""
        return await self._write_edge_repo.get_recent_edge_pairs(
            node_id,
            candidate_ids,
            staleness_days=staleness_days,
        )

    # ── Perspective queries ───────────────────────────────────────────

    async def get_perspectives(self, concept_node_id: uuid.UUID) -> list[Node]:
        """Get all perspective nodes for a concept from write-db."""
        from kt_db.write_models import WriteNode as _WN

        parent_wn = await self._write_node_repo.get_by_uuid(concept_node_id)
        if parent_wn is None:
            return []
        stmt = select(_WN).where(
            _WN.source_concept_key == parent_wn.key,
            _WN.node_type == "perspective",
        )
        result = await self._write_session.execute(stmt)
        return [self._write_node_to_node(wn) for wn in result.scalars().all()]

    # ── Pure utility methods ──────────────────────────────────────────

    def is_node_stale(self, node: Node) -> bool:
        """Check whether a node is past its stale_after window."""
        if node.updated_at is None or node.stale_after is None:
            return True
        now = _utcnow()
        stale_cutoff = node.updated_at + timedelta(days=node.stale_after)
        return now > stale_cutoff

    def compute_richness(self, node: Node, fact_count: int, dimension_count: int) -> float:
        """Compute a richness score for a node."""
        raw = fact_count * 0.1 + dimension_count * 0.2 + node.access_count * 0.01
        return min(1.0, raw)
