"""HatchetPipeline -- session-per-phase wrapper around the original sub-pipelines.

Each public method corresponds to one phase of the node build pipeline.
The method opens its own DB session, builds an AgentContext, delegates to
the original sub-pipeline classes (NodeCreationPipeline, DimensionPipeline,
etc.), commits, and returns a serializable dict.

Hatchet task functions in node_pipeline.py are thin wrappers::

    pipeline = HatchetPipeline(cast(WorkerState, ctx.lifespan))
    result = await pipeline.create(concept=..., node_type=..., explore_budget=...)

This replaces the ad-hoc per-task AgentContext construction that was scattered
across node_pipeline.py, and brings back the enrich_batch phase that was
silently missing from the Hatchet DAG.
"""

from __future__ import annotations

import logging
import uuid
from contextlib import asynccontextmanager
from typing import Any, AsyncGenerator

from kt_agents_core.state import PipelineState
from kt_hatchet.lifespan import WorkerState

logger = logging.getLogger(__name__)


class HatchetPipeline:
    """Session-per-phase node build pipeline for Hatchet workers.

    Instantiate with WorkerState (available from ``ctx.lifespan`` in every
    Hatchet task).  Each phase method manages its own DB session -- no shared
    state across phases.
    """

    def __init__(self, state: WorkerState, api_key: str | None = None) -> None:
        self._state = state
        self._api_key = api_key

    @asynccontextmanager
    async def _open_sessions(self) -> AsyncGenerator[tuple[None, Any], None]:
        """Open write-db session for worker pipelines.

        Workers operate in write-db-only mode — all reads route through
        write-db or Qdrant via WorkerGraphEngine.  No graph-db sessions
        are opened.
        """
        write_session = None
        if self._state.write_session_factory is not None:
            write_session = self._state.write_session_factory()
        try:
            yield None, write_session
        finally:
            if write_session is not None:
                await write_session.close()

    # -- Internal helpers --------------------------------------------------

    async def _build_ctx(
        self,
        session: Any,
        emit_event: Any | None = None,
        write_session: Any | None = None,
    ) -> Any:
        """Build AgentContext from WorkerState and an open session.

        When ``self._api_key`` is set (BYOK), per-request ModelGateway and
        EmbeddingService are created instead of using the shared worker instances.
        """
        from kt_agents_core.state import AgentContext
        from kt_graph.worker_engine import WorkerGraphEngine

        if self._api_key:
            from kt_models.embeddings import EmbeddingService
            from kt_models.gateway import ModelGateway

            model_gateway = ModelGateway(api_key=self._api_key)
            embedding_service = EmbeddingService(api_key=self._api_key)
        else:
            model_gateway = self._state.model_gateway  # type: ignore[assignment]
            embedding_service = self._state.embedding_service  # type: ignore[assignment]

        graph_engine = WorkerGraphEngine(
            write_session,
            embedding_service,
            qdrant_client=self._state.qdrant_client,
        )
        return AgentContext(
            graph_engine=graph_engine,
            provider_registry=self._state.provider_registry,  # type: ignore[arg-type]
            model_gateway=model_gateway,
            embedding_service=embedding_service,
            session=None,
            session_factory=self._state.session_factory,
            content_fetcher=self._state.content_fetcher,  # type: ignore[arg-type]
            emit_event=emit_event,
            write_session_factory=self._state.write_session_factory,
            qdrant_client=self._state.qdrant_client,
        )

    async def _sync_seed_facts_to_node(
        self,
        node_id: uuid.UUID,
        write_session: Any,
        graph_engine: Any,
    ) -> list[uuid.UUID] | None:
        """Look up the seed for a node and link any unlinked seed facts.

        Returns the full list of seed fact IDs if a seed was found, else None.
        """
        from kt_db.repositories.write_nodes import WriteNodeRepository
        from kt_db.repositories.write_seeds import WriteSeedRepository

        write_node_repo = WriteNodeRepository(write_session)
        wn = await write_node_repo.get_by_uuid(node_id)
        if wn is None:
            return None

        seed_repo = WriteSeedRepository(write_session)
        seed = await seed_repo.get_seed_by_promoted_node_key(wn.key)
        if seed is None:
            return None

        seed_fact_ids = await seed_repo.get_all_descendant_facts(seed.key)
        if not seed_fact_ids:
            return None

        # Link any seed facts not yet linked to the node.
        # Route through write-db (append to WriteNode.fact_ids) instead of
        # graph_engine.link_fact_to_node, because seed facts may not have
        # been synced to graph-db yet — writing to node_facts directly
        # would violate the FK constraint on facts.
        existing = set(str(fid) for fid in (wn.fact_ids or []))
        linked = 0
        for fid in seed_fact_ids:
            fid_str = str(fid)
            if fid_str not in existing:
                await write_node_repo.append_fact_id(wn.key, fid_str)
                existing.add(fid_str)
                linked += 1
        if linked:
            await write_session.commit()
            logger.info(
                "sync_seed_facts: linked %d seed facts to node '%s'",
                linked,
                wn.concept,
            )

        return seed_fact_ids

    # -- Phase 1 + 1.5 + 2: classify, gather/enrich, create ---------------

    async def create(
        self,
        concept: str,
        node_type: str,
        seed_key: str,
        query: str = "",
        entity_subtype: str | None = None,
        existing_node_id: str | None = None,
    ) -> dict:
        """Phases 1 + 1.5 + 2: classify concept, build node from pool facts.

        Fact gathering (external search + decomposition) is the responsibility
        of the ScopePlannerAgent -- it populates the fact pool before the node
        builder tasks run.  This method only reads from the existing pool:

        1. ``classify_and_gather_batch`` -- dedup check, pool search, classify
           action as create / enrich / refresh / skip.  External search is
           disabled (explore_budget=0).
        2. ``enrich_batch``  -- for existing nodes: link new pool facts (was
           missing from the previous Hatchet DAG)
        3. ``create_batch``  -- for new/stale nodes: create DB row + link facts

        Returns a serializable dict: ``{node_id, action, concept, node_type,
        explore_charged}``.
        """
        from kt_worker_nodes.pipelines.models import CreateNodeTask
        from kt_worker_nodes.pipelines.nodes.pipeline import NodeCreationPipeline

        task = CreateNodeTask(name=concept, node_type=node_type, seed_key=seed_key, entity_subtype=entity_subtype)
        orch_state = PipelineState(
            query=query or concept,
            nav_budget=100,
            explore_budget=0,  # planner already gathered facts -- no external search here
        )

        if existing_node_id:
            # Caller already resolved this as an existing node — enrich directly
            from kt_db.models import Node
            from kt_db.repositories.write_nodes import WriteNodeRepository

            nid = uuid.UUID(existing_node_id)
            async with self._open_sessions() as (_, write_session):
                ctx = await self._build_ctx(None, write_session=write_session)
                node_pipeline = NodeCreationPipeline(ctx)

                wn = await WriteNodeRepository(write_session).get_by_uuid(nid) if write_session else None
                if wn:
                    task.action = "enrich"
                    task.existing_node = Node(id=nid, concept=wn.concept, node_type=wn.node_type)
                    await node_pipeline.enrich_batch([task], orch_state)
                else:
                    # Node disappeared — fall back to normal classify
                    await node_pipeline.classify_and_gather_batch([task], orch_state)
                    if task.action == "enrich":
                        await node_pipeline.enrich_batch([task], orch_state)
                    elif task.action in ("create", "refresh"):
                        await node_pipeline.create_batch([task], orch_state)

                try:
                    await write_session.commit()
                except Exception:
                    await write_session.rollback()

            # Extract scalar values while task is still populated
            node_id_str = str(task.node.id) if task.node is not None else existing_node_id
            result_node_type = task.node.node_type if task.node is not None else node_type
        else:
            async with self._open_sessions() as (_, write_session):
                ctx = await self._build_ctx(None, write_session=write_session)
                node_pipeline = NodeCreationPipeline(ctx)

                await node_pipeline.classify_and_gather_batch([task], orch_state)

                if task.action == "enrich":
                    # Phase 1.5 -- was absent from the old Hatchet DAG
                    await node_pipeline.enrich_batch([task], orch_state)
                elif task.action in ("create", "refresh"):
                    await node_pipeline.create_batch([task], orch_state)
                # "skip" / "error": task.node stays None

                # create_batch commits internally then runs dedup which can fail
                # (e.g. deadlock). If the session is in a rolled-back state, roll
                # back explicitly so we can still read the task results -- the node
                # was already committed by create_batch's internal commit.
                try:
                    await write_session.commit()
                except Exception:
                    await write_session.rollback()

            # Extract scalar values before the session closes -- accessing
            # expired ORM attributes after session.close() raises
            # DetachedInstanceError.
            node_id_str = str(task.node.id) if task.node is not None else None
            result_node_type = task.node.node_type if task.node is not None else node_type

        return {
            "node_id": node_id_str,
            "action": task.action,
            "concept": concept,
            "node_type": result_node_type,
            "explore_charged": task.explore_charged,
        }

    # -- Phase 2.5: pool enrichment (for recalculate) ---------------------

    async def enrich(self, node_id: str) -> dict:
        """Search the fact pool for unlinked facts and attach them to the node.

        Uses ``PoolEnricher`` to find facts by embedding + text similarity
        that aren't yet linked, link them, and regenerate dimensions if the
        new-fact ratio exceeds the threshold.

        Returns ``{node_id, new_facts_linked, dimensions_regenerated}``.
        """
        from kt_db.models import Node
        from kt_db.repositories.write_nodes import WriteNodeRepository
        from kt_worker_nodes.pipelines.nodes.enrichment import PoolEnricher

        nid = uuid.UUID(node_id)

        async with self._open_sessions() as (_, write_session):
            if write_session is None:
                raise RuntimeError("enrich: write_session is required")
            ctx = await self._build_ctx(None, write_session=write_session)

            wn = await WriteNodeRepository(write_session).get_by_uuid(nid)
            node = Node(id=nid, concept=wn.concept, node_type=wn.node_type) if wn else None
            if node is None:
                logger.warning("enrich: node %s not found", node_id)
                return {"node_id": node_id, "new_facts_linked": 0, "dimensions_regenerated": False}

            # Ensure the node has an embedding -- without one, pool search
            # by similarity is skipped entirely and the node can never grow.
            if node.embedding is None and ctx.embedding_service:
                try:
                    embedding = await ctx.embedding_service.embed_text(node.concept)
                    node.embedding = embedding  # in-memory for pipeline use
                    # Upsert to Qdrant (embeddings no longer stored in graph-db)
                    await ctx.graph_engine.upsert_node_to_qdrant(
                        node.id,
                        embedding,
                        node_type=node.node_type,
                        concept=node.concept,
                    )
                    logger.info("enrich: generated missing embedding for node %s ('%s')", node_id, node.concept)
                except Exception:
                    logger.warning(
                        "enrich: failed to generate embedding for node %s",
                        node_id,
                        exc_info=True,
                    )

            has_embedding = node.embedding is not None
            logger.info(
                "enrich: node %s ('%s') has_embedding=%s, embedding_service=%s",
                node_id,
                node.concept,
                has_embedding,
                ctx.embedding_service is not None,
            )

            enricher = PoolEnricher(ctx)
            result = await enricher.enrich(node)
            await write_session.commit()

        return {
            "node_id": node_id,
            "new_facts_linked": result.new_facts_linked,
            "dimensions_regenerated": result.dimensions_regenerated,
        }

    # -- Phase 3: dimensions -----------------------------------------------

    async def dimensions(self, node_id: str) -> dict:
        """Phase 3: generate dimensions for a node.

        Returns ``{node_id, node_type, dimensions_created, fact_count}``.
        ``node_type`` is forwarded to the caller so the Hatchet task can
        compute the edge fan-out targets without an extra DB query.
        """
        from kt_db.models import Node
        from kt_db.repositories.write_nodes import WriteNodeRepository
        from kt_worker_nodes.pipelines.dimensions.pipeline import DimensionPipeline

        nid = uuid.UUID(node_id)

        async with self._open_sessions() as (_, write_session):
            if write_session is None:
                raise RuntimeError("dimensions: write_session is required")
            ctx = await self._build_ctx(None, write_session=write_session)

            wn = await WriteNodeRepository(write_session).get_by_uuid(nid)
            node = Node(id=nid, concept=wn.concept, node_type=wn.node_type) if wn else None
            if node is None:
                logger.warning("dimensions: node %s not found", node_id)
                return {"node_id": node_id, "node_type": "concept", "dimensions_created": 0, "fact_count": 0}

            # Sync seed facts to node before reading
            await self._sync_seed_facts_to_node(nid, write_session, ctx.graph_engine)

            # Route fact read through GraphEngine (write-db when available)
            facts = await ctx.graph_engine.get_node_facts(nid)
            dim_mode = "neutral" if node.node_type == "concept" else node.node_type
            node_type = node.node_type

            dim_pipeline = DimensionPipeline(ctx)
            dim_result = await dim_pipeline.generate_and_store(node, facts, mode=dim_mode)

            await write_session.commit()

            # Track fact count at build time for staleness detection.
            # Committed after graph-db so the watermark only advances
            # when the graph-db side has succeeded.
            # When facts is empty, no dimensions were generated and
            # no watermark update is needed — the write session has
            # no pending changes to commit.
            if facts:
                write_node_repo = WriteNodeRepository(write_session)
                node_key = write_node_repo.node_key(node_type, node.concept)
                await write_node_repo.update_facts_at_last_build(node_key, len(facts))
                await write_session.commit()

        dimensions_created = len(dim_result.dim_results)
        if not facts:
            logger.info("dimensions: node %s ('%s') has 0 facts — skipping dimension generation", node_id, node.concept)
        elif dimensions_created == 0:
            logger.warning(
                "dimensions: node %s ('%s') has %d facts but generated 0 dimensions",
                node_id,
                node.concept,
                len(facts),
            )

        return {
            "node_id": node_id,
            "node_type": node_type,
            "dimensions_created": dimensions_created,
            "fact_count": len(facts),
        }

    async def full_dimensions(self, node_id: str) -> dict:
        """Full-mode dimensions: delete all existing, then regenerate from ALL facts.

        Resumable on retry: a ``dim_rebuild_in_progress`` metadata flag tracks
        whether a previous attempt already deleted the old dimensions.  On retry
        only draft (non-definitive) dimensions are removed so that definitive
        dimensions committed during the failed attempt are preserved and
        ``_batch_facts`` resumes from the first unclaimed fact batch.

        Same return shape as ``dimensions()``:
        ``{node_id, node_type, dimensions_created, fact_count}``.
        """
        from kt_db.keys import make_node_key
        from kt_db.models import Node
        from kt_db.repositories.write_dimensions import WriteDimensionRepository
        from kt_db.repositories.write_nodes import WriteNodeRepository
        from kt_worker_nodes.pipelines.dimensions.pipeline import DimensionPipeline

        nid = uuid.UUID(node_id)

        async with self._open_sessions() as (_, write_session):
            if write_session is None:
                raise RuntimeError("full_dimensions: write_session is required")
            ctx = await self._build_ctx(None, write_session=write_session)

            write_node_repo = WriteNodeRepository(write_session)
            wn = await write_node_repo.get_by_uuid(nid)
            node = Node(id=nid, concept=wn.concept, node_type=wn.node_type) if wn else None
            if node is None:
                logger.warning("full_dimensions: node %s not found", node_id)
                return {"node_id": node_id, "node_type": "concept", "dimensions_created": 0, "fact_count": 0}

            # Sync seed facts first
            await self._sync_seed_facts_to_node(nid, write_session, ctx.graph_engine)

            node_key = make_node_key(node.node_type, node.concept)
            dim_repo = WriteDimensionRepository(write_session)
            metadata = dict(wn.metadata_ or {})
            rebuild_in_progress = metadata.get("dim_rebuild_in_progress", False)
            deleted = 0

            if not rebuild_in_progress:
                # First attempt — delete all existing dims and mark rebuild started.
                # The early commit is intentional: it makes the deletion durable so
                # that on retry _batch_facts can resume from committed definitive
                # dims rather than re-deleting everything.
                deleted = await dim_repo.delete_all_for_node(node_key)
                await dim_repo.delete_convergence_report(node_key)
                await dim_repo.delete_divergent_claims(node_key)
                metadata["dim_rebuild_in_progress"] = True
                await write_node_repo.update_metadata(node_key, metadata)
                await write_session.commit()
                if deleted:
                    logger.info("full_dimensions: deleted %d existing dimensions for node %s", deleted, node_id)
            else:
                # Retry — keep definitive dims from previous attempt, delete only drafts
                deleted = await dim_repo.delete_drafts_for_node(node_key)
                await dim_repo.delete_convergence_report(node_key)
                await dim_repo.delete_divergent_claims(node_key)
                await write_session.commit()
                logger.info(
                    "full_dimensions: retry — kept definitive dims, deleted %d drafts for node %s",
                    deleted,
                    node_id,
                )

            # Regenerate from all facts (writes go to write-db only; sync
            # worker propagates to graph-db, so no graph-db commit needed).
            facts = await ctx.graph_engine.get_node_facts(nid)
            dim_mode = "neutral" if node.node_type == "concept" else node.node_type
            node_type = node.node_type

            dim_pipeline = DimensionPipeline(ctx)
            dim_result = await dim_pipeline.generate_and_store(node, facts, mode=dim_mode)

            # Clear the rebuild flag on success — uses a targeted jsonb key
            # removal to avoid overwriting concurrent metadata changes.
            await write_node_repo.remove_metadata_key(node_key, "dim_rebuild_in_progress")

            if facts:
                await write_node_repo.update_facts_at_last_build(node_key, len(facts))
            await write_session.commit()

        dimensions_created = len(dim_result.dim_results)
        logger.info(
            "full_dimensions: node %s ('%s') — %d dims from %d facts (deleted %d old)",
            node_id,
            node.concept,
            dimensions_created,
            len(facts),
            deleted,
        )
        return {
            "node_id": node_id,
            "node_type": node_type,
            "dimensions_created": dimensions_created,
            "fact_count": len(facts),
        }

    # -- Phase 3.5: definition ---------------------------------------------

    async def definition(self, node_id: str) -> dict:
        """Phase 3.5: generate a textual definition for a node.

        Returns ``{node_id, has_definition}``.
        """
        from kt_db.repositories.write_nodes import WriteNodeRepository
        from kt_worker_nodes.pipelines.definitions.pipeline import DefinitionPipeline

        nid = uuid.UUID(node_id)

        async with self._open_sessions() as (_, write_session):
            if write_session is None:
                raise RuntimeError("definition: write_session is required")
            ctx = await self._build_ctx(None, write_session=write_session)

            wn = await WriteNodeRepository(write_session).get_by_uuid(nid)
            if wn is None:
                logger.warning("definition: node %s not found", node_id)
                return {"node_id": node_id, "has_definition": False}

            def_pipeline = DefinitionPipeline(ctx)
            definition = await def_pipeline.generate_definition(nid, wn.concept)
            await write_session.commit()

        return {"node_id": node_id, "has_definition": definition is not None}

    # -- Phase 4: edge resolution ------------------------------------------

    async def edges(
        self,
        node_id: str,
        concept: str = "",
        node_type: str = "concept",
    ) -> dict:
        """Phase 4: resolve edges from candidates for a node.

        ``concept`` and ``node_type`` are passed from the pipeline input to
        avoid a graph-db lookup for the current node.  The node was just
        created in write-db / Qdrant and may not be in graph-db yet.

        The candidate-based resolver handles both same-type and cross-type
        edges in one pass.

        Returns ``{edge_ids, edges_created}``.
        """
        from kt_db.models import Node
        from kt_worker_nodes.pipelines.edges.pipeline import EdgePipeline
        from kt_worker_nodes.pipelines.models import CreateNodeTask

        nid = uuid.UUID(node_id)

        # Build a lightweight Node object from the pipeline input.
        # No graph-db read required — concept/node_type come from the
        # earlier create_node task via the Hatchet input.
        node = Node(id=nid, concept=concept, node_type=node_type)

        async with self._open_sessions() as (_, write_session):
            ctx = await self._build_ctx(None, write_session=write_session)

            task = CreateNodeTask(name=concept, node_type=node_type, seed_key=f"{node_type}:{concept}")
            task.node = node
            task.action = "create"

            orch_state = PipelineState(
                query=concept,
                nav_budget=100,
                explore_budget=0,
            )

            edge_pipeline = EdgePipeline(ctx)
            await edge_pipeline.resolve_from_candidates_batch([task], orch_state)

            await write_session.commit()

        edge_ids = [str(eid) for eid in task.result.get("edge_ids", [])]
        return {"edge_ids": edge_ids, "edges_created": task.edges_created}

    # -- Phase 4.5: refresh existing edge justifications ---------------------

    async def refresh_edge_justifications(
        self,
        node_id: str,
        concept: str,
        node_type: str,
    ) -> dict:
        """Refresh justifications on existing edges touching this node.

        Checks if accepted edge candidates have added facts not yet reflected
        in the edge's fact_ids. For changed edges, regenerates the LLM
        justification and updates the weight.

        Returns ``{edges_refreshed}``.
        """
        from kt_db.models import Node
        from kt_worker_nodes.pipelines.edges.resolver import EdgeResolver

        nid = __import__("uuid").UUID(node_id)
        node = Node(id=nid, concept=concept, node_type=node_type)

        async with self._open_sessions() as (_, write_session):
            ctx = await self._build_ctx(None, write_session=write_session)
            resolver = EdgeResolver(ctx)
            result = await resolver.refresh_existing_edges(node)
            await write_session.commit()

        return result
