"""Ingest workflows: ingest confirmation and ingest partition.

Extracted from the monolith conversations.py workflow file. These workflows
handle the full ingest pipeline:
- ``ingest_confirm_wf`` — Full ingest: process sources, decompose, build nodes
- ``ingest_partition_wf`` — Parallel partition agent for large documents
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import timedelta
from typing import Any, cast

from hatchet_sdk import DurableContext

from kt_config.settings import get_settings
from kt_hatchet.client import get_hatchet
from kt_hatchet.lifespan import WorkerState
from kt_hatchet.models import (
    IngestBuildInput,
    IngestConfirmInput,
    IngestDecomposeInput,
    IngestDecomposeOutput,
    IngestPartitionInput,
    IngestPartitionOutput,
    ProposedNode,
)
from kt_hatchet.tracked_task import tracked_task

logger = logging.getLogger(__name__)

hatchet = get_hatchet()
_schedule_timeout = timedelta(minutes=get_settings().hatchet_schedule_timeout_minutes)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@asynccontextmanager
async def _open_sessions(state: WorkerState, graph_id: str | None = None) -> AsyncGenerator[tuple[None, Any], None]:
    """Open write-db session for worker pipelines.

    Yields ``(session, write_session)`` where ``session`` is **None**.
    When ``graph_id`` is set, resolves per-graph session factories.
    """
    _, write_sf = await state.resolve_sessions(graph_id)
    if write_sf is None:
        raise RuntimeError("Ingest worker requires write_session_factory")
    write_session = write_sf()
    try:
        yield None, write_session
    finally:
        await write_session.close()


@asynccontextmanager
async def _open_graph_session(state: WorkerState, graph_id: str | None = None) -> AsyncGenerator[Any, None]:
    """Open a graph-db session scoped to the correct graph schema."""
    graph_sf, _ = await state.resolve_sessions(graph_id)
    session = graph_sf()
    try:
        yield session
    finally:
        await session.close()


async def _build_agent_context(
    state: WorkerState,
    *,
    write_session: Any,
    user_id: str | None = None,
    graph_id: str | None = None,
) -> Any:
    """Build an AgentContext from WorkerState.

    ``session`` (graph-db) is NOT passed — ingest operates in
    write-db-only mode.  GraphEngine methods that have write-db
    fallbacks will use write-db; conversation/message tracking uses
    short-lived sessions from ``session_factory`` directly in the
    workflow (not passed through AgentContext).

    When ``user_id`` is provided, the user's API key is resolved from the
    database and per-request ``ModelGateway`` / ``EmbeddingService``
    instances are created instead of using the shared ones from ``WorkerState``.
    """
    from kt_agents_core.state import AgentContext
    from kt_hatchet.keys import resolve_user_api_key_cached

    api_key = await resolve_user_api_key_cached(state, user_id)
    if api_key:
        from kt_models.embeddings import EmbeddingService
        from kt_models.gateway import ModelGateway

        model_gateway = ModelGateway(api_key=api_key)
        embedding_service = EmbeddingService(api_key=api_key)
    else:
        model_gateway = state.model_gateway
        embedding_service = state.embedding_service

    resolved_sf, resolved_write_sf = await state.resolve_sessions(graph_id)

    # Resolve graph metadata + qdrant prefix so the engine factory can wire
    # a public-cache bridge for non-default graphs. The factory handles the
    # default-graph case (returns a bridge-less engine) on its own.
    graph_uuid: uuid.UUID | None = None
    qdrant_prefix = ""
    if graph_id and state.graph_resolver is not None:
        try:
            graph_uuid = uuid.UUID(graph_id)
            gs = await state.graph_resolver.resolve(graph_uuid)
            qdrant_prefix = gs.qdrant_collection_prefix
        except Exception:
            logger.warning("Failed to resolve graph %s for engine factory", graph_id, exc_info=True)
            graph_uuid = None
            qdrant_prefix = ""

    graph_engine = state.make_worker_engine(
        write_session,
        graph_id=graph_uuid,
        qdrant_collection_prefix=qdrant_prefix,
        embedding_service=embedding_service,
    )
    return AgentContext(
        graph_engine=graph_engine,
        provider_registry=state.provider_registry,
        model_gateway=model_gateway,
        embedding_service=embedding_service,
        session=None,
        emit_event=None,
        fetch_registry=state.fetch_registry,
        session_factory=resolved_sf,
        write_session_factory=resolved_write_sf,
        qdrant_client=state.qdrant_client,
    )


async def _resolve_graph_meta(
    state: WorkerState,
    graph_id: str | None,
) -> tuple[uuid.UUID | None, bool, bool]:
    """Return ``(graph_uuid, use_public_cache, contribute_to_public)``.

    For the default graph (or no resolver / unresolved graph), both
    toggles are False so the public-cache helpers no-op naturally — the
    default graph never participates as either source or target of the
    bridge.
    """
    if not graph_id or state.graph_resolver is None:
        return None, False, False
    try:
        graph_uuid = uuid.UUID(graph_id)
    except (TypeError, ValueError):
        logger.warning("Invalid graph_id %r in workflow input", graph_id)
        return None, False, False
    if state.default_graph_id is not None and graph_uuid == state.default_graph_id:
        return graph_uuid, False, False
    try:
        gs = await state.graph_resolver.resolve(graph_uuid)
    except Exception:
        logger.warning("Failed to resolve graph %s for public-cache hooks", graph_id, exc_info=True)
        return graph_uuid, False, False
    return graph_uuid, gs.graph.use_public_cache, gs.graph.contribute_to_public


# ══════════════════════════════════════════════════════════════
# Ingest confirmation workflow — full ingest pipeline
# ══════════════════════════════════════════════════════════════

ingest_confirm_wf = hatchet.workflow(
    name="ingest_confirm",
    input_validator=IngestConfirmInput,
)


@tracked_task(
    ingest_confirm_wf,
    task_type="ingest_confirm",
    durable=True,
    execution_timeout=timedelta(hours=12),
    schedule_timeout=_schedule_timeout,
)
async def handle_ingest(input: IngestConfirmInput, ctx: DurableContext) -> dict:
    """Run the full ingest pipeline: process sources, decompose, build nodes.

    Phase 1: Process ingest sources (text extraction, chunking)
    Phase 2: Decompose selected chunks into facts
    Phase 3: Run the ingest agent for node building
    """
    worker_state = cast(WorkerState, ctx.lifespan)

    from kt_db.repositories.conversations import ConversationRepository
    from kt_worker_ingest.agents.ingest_worker import IngestWorker
    from kt_worker_ingest.ingest.pipeline import (
        DecompositionSummary,
        process_ingest_sources,
    )
    from kt_worker_ingest.ingest.public_cache import (
        apply_public_cache_lookups,
        contribute_processed_to_public,
    )

    ctx.log(f"Ingest confirm starting: conv={input.conversation_id}")

    # Resolve public-cache toggles ONCE per workflow run.  Both default to
    # False for the default graph itself / unresolved graph_id, so the
    # bridge helpers no-op without any extra guards in the body below.
    _pcache_graph_uuid, _use_public_cache, _contribute_to_public = await _resolve_graph_meta(
        worker_state, input.graph_id
    )
    cache_hit_source_ids: set[str] = set()

    msg_uuid = uuid.UUID(input.message_id)
    conv_uuid = uuid.UUID(input.conversation_id)

    # Mark as running
    async with _open_graph_session(worker_state, input.graph_id) as session:
        repo = ConversationRepository(session)
        await repo.update_message(msg_uuid, status="running")
        await session.commit()

    try:
        # ── Phase 1: Process sources ──────────────────────────────
        async with _open_sessions(worker_state, input.graph_id) as (_, write_session):
            agent_ctx = await _build_agent_context(
                worker_state,
                write_session=write_session,
                user_id=input.user_id,
            )

            # process_ingest_sources needs graph-db for IngestSource table;
            # open a short-lived session scoped to the correct graph schema.
            async with _open_graph_session(worker_state, input.graph_id) as graph_session:
                processed = await process_ingest_sources(
                    conv_uuid,
                    graph_session,
                    agent_ctx.file_data_store,
                    write_session=write_session,
                )
                await graph_session.commit()
            await write_session.commit()

        if not processed:
            raise ValueError("No sources could be processed")

        ctx.log(f"Phase 1 complete: {len(processed)} sources processed")

        # Keep gRPC stream alive between phases
        ctx.refresh_timeout("4h")

        # ── Phase 2: Decompose sources via shared workflow ────────
        # Uses the same Hatchet workflow as bottom-up (decompose_sources_wf)
        # so all source decomposition follows a single code path:
        # extract facts → entity extraction → seed creation.

        # ── Public-cache lookups (before decomposition) ────────────
        cache_hit_source_ids: set[str] = set()
        async with _open_sessions(worker_state, input.graph_id) as (_, write_session):
            agent_ctx = await _build_agent_context(
                worker_state,
                write_session=write_session,
                user_id=input.user_id,
                graph_id=input.graph_id,
            )
            try:
                _, cache_hit_source_ids, _hit_summaries = await apply_public_cache_lookups(
                    agent_ctx.graph_engine,
                    processed,
                    use_public_cache=_use_public_cache,
                    chunk_selection=None,
                )
            except Exception:
                logger.warning("public cache lookup phase failed", exc_info=True)
                cache_hit_source_ids = set()

            if cache_hit_source_ids:
                ctx.log(f"Public cache: {len(cache_hit_source_ids)} hit(s)")

        # Separate text vs image sources
        text_source_ids: list[str] = []
        image_source_ids: list[str] = []
        for ps in processed:
            if not ps.raw_source_id:
                continue
            if ps.is_image:
                image_source_ids.append(str(ps.raw_source_id))
            else:
                text_source_ids.append(str(ps.raw_source_id))

        all_source_ids = text_source_ids + image_source_ids

        # Dispatch the shared decomposition workflow (same as bottom-up)
        from kt_hatchet.client import run_workflow
        from kt_hatchet.models import DecomposeSourcesOutput

        decompose_result = await run_workflow(
            "decompose_sources",
            {
                "raw_source_ids": all_source_ids,
                "image_source_ids": image_source_ids,
                "concept": "",
                "query_context": "",
                "message_id": input.message_id,
                "conversation_id": input.conversation_id,
                "graph_id": input.graph_id,
            },
        )

        # aio_result() returns {task_name: output} — unwrap the task key
        if isinstance(decompose_result, dict) and len(decompose_result) == 1:
            decompose_result = next(iter(decompose_result.values()))
        decompose_output = DecomposeSourcesOutput.model_validate(decompose_result)

        # Map to DecompositionSummary for downstream compatibility
        decomp_summary = DecompositionSummary(
            total_facts=decompose_output.total_fact_count,
            total_chunks_processed=len(processed),
            total_sources=len(processed),
            inserted_fact_ids=list(decompose_output.fact_ids),
        )

        # ── Contribute to public graph (after decomposition) ───────
        async with _open_sessions(worker_state, input.graph_id) as (_, write_session):
            agent_ctx = await _build_agent_context(
                worker_state,
                write_session=write_session,
                user_id=input.user_id,
                graph_id=input.graph_id,
            )
            try:
                contributed = await contribute_processed_to_public(
                    agent_ctx.graph_engine,
                    processed,
                    contribute_to_public=_contribute_to_public,
                    share_with_public_graph=input.share_with_public_graph,
                    cache_hit_source_ids=cache_hit_source_ids,
                )
                if contributed:
                    ctx.log(f"Public contribute: pushed {contributed} source(s) upstream")
            except Exception:
                logger.warning("public contribute phase failed", exc_info=True)

            await write_session.commit()

        ctx.log(
            f"Phase 2 complete: {decomp_summary.total_facts} facts from {decomp_summary.total_chunks_processed} chunks"
        )

        # Keep gRPC stream alive before node building
        ctx.refresh_timeout("4h")

        # ── Phase 2.5: Build content index + backfill fact counts ──
        from kt_worker_ingest.ingest.content_index import (
            ContentIndex,
            backfill_fact_counts,
            build_content_index,
        )
        from kt_worker_ingest.ingest.partitioning import partition_for_parallel

        content_index: ContentIndex | None = None
        try:
            from kt_hatchet.keys import resolve_user_api_key_cached

            _resolved_key = await resolve_user_api_key_cached(worker_state, input.user_id)
            if _resolved_key:
                from kt_models.gateway import ModelGateway

                _model_gateway = ModelGateway(api_key=_resolved_key, graph_id=input.graph_id)
            else:
                _model_gateway = worker_state.model_gateway
            from kt_providers.fetch import FileDataStore as _FDS

            content_index = await build_content_index(
                processed,
                _model_gateway,
                _FDS(),
            )
            if content_index:
                backfill_fact_counts(
                    content_index,
                    total_facts=decomp_summary.total_facts,
                    fact_type_counts=decomp_summary.fact_type_counts,
                )
            ctx.log(f"Content index built: {len(content_index.entries) if content_index else 0} entries")
        except Exception:
            logger.warning("Content index build failed, proceeding without it", exc_info=True)
            content_index = None

        # ── Phase 3: Run ingest agent(s) (node building) ──────────
        partitions = (
            partition_for_parallel(content_index, input.nav_budget)
            if content_index and len(content_index.entries) > 0
            else None
        )

        if partitions and len(partitions) > 1:
            # Large document — fan out parallel ingest agents
            ctx.log(f"Large document: fanning out {len(partitions)} parallel ingest agents")

            from hatchet_sdk import TriggerWorkflowOptions

            all_titles = [e.title for e in content_index.entries] if content_index else []  # type: ignore[union-attr]
            child_meta = TriggerWorkflowOptions(
                additional_metadata={
                    "message_id": input.message_id,
                    "conversation_id": input.conversation_id,
                }
            )

            bulk_items = []
            for partition in partitions:
                bulk_items.append(
                    ingest_partition_wf.create_bulk_run_item(
                        input=IngestPartitionInput(
                            conversation_id=input.conversation_id,
                            message_id=input.message_id,
                            partition_id=partition.partition_id,
                            index_range_start=partition.index_range[0],
                            index_range_end=partition.index_range[1],
                            nav_budget=partition.nav_budget,
                            total_facts=decomp_summary.total_facts,
                            fact_type_counts=decomp_summary.fact_type_counts,
                            all_titles=all_titles,
                            partition_facts=partition.total_facts_in_partition,
                        ),
                        options=child_meta,
                    )
                )

            try:
                results = await ingest_partition_wf.aio_run_many(bulk_items)
            except Exception:
                logger.exception("Parallel ingest fan-out failed")
                results = []

            # Merge results
            all_created_nodes: list[str] = []
            all_created_edges: list[str] = []
            total_nav_used = 0
            partition_summaries: list[str] = []

            for raw_result in results:
                task_data = (
                    raw_result.get("run_ingest_partition", raw_result) if isinstance(raw_result, dict) else raw_result
                )
                out = IngestPartitionOutput.model_validate(task_data)
                all_created_nodes.extend(out.created_node_ids)
                all_created_edges.extend(out.created_edge_ids)
                total_nav_used += out.nav_used
                if out.summary:
                    partition_summaries.append(out.summary)

            # Build subgraph from merged results
            from kt_agents_core.results import build_ingest_subgraph

            async with _open_sessions(worker_state, input.graph_id) as (_, merge_ws):
                merge_ctx = await _build_agent_context(worker_state, write_session=merge_ws, user_id=input.user_id)
                subgraph = await build_ingest_subgraph(all_created_nodes, all_created_edges, merge_ctx)

            # Persist merged result
            merged_answer = "\n\n---\n\n".join(partition_summaries) if partition_summaries else ""
            async with _open_graph_session(worker_state, input.graph_id) as session:
                repo = ConversationRepository(session)
                await repo.update_message(
                    msg_uuid,
                    status="completed",
                    content=merged_answer,
                    nav_used=total_nav_used,
                    explore_used=0,
                    visited_nodes=all_created_nodes,
                    created_nodes=all_created_nodes,
                    created_edges=all_created_edges,
                    subgraph=subgraph,
                )
                await session.commit()

            ingest_nodes_created = len(all_created_nodes)
            ingest_edges_created = len(all_created_edges)
            ingest_nav_used = total_nav_used

        else:
            # Small document or no index — single agent (existing path)
            async with _open_sessions(worker_state, input.graph_id) as (_, write_session):
                agent_ctx = await _build_agent_context(
                    worker_state,
                    write_session=write_session,
                    user_id=input.user_id,
                )

                result = await IngestWorker(agent_ctx).run(
                    input.conversation_id,
                    processed,
                    input.nav_budget,
                    decomp_summary,
                    content_index=content_index,
                )

                await write_session.commit()

            # Persist result
            async with _open_graph_session(worker_state, input.graph_id) as session:
                repo = ConversationRepository(session)
                await repo.update_message(
                    msg_uuid,
                    status="completed",
                    content=result.answer or "",
                    nav_used=result.nav_used,
                    explore_used=result.explore_used,
                    visited_nodes=result.visited_nodes,
                    created_nodes=result.created_nodes,
                    created_edges=result.created_edges,
                    subgraph=result.subgraph,
                )
                await session.commit()

            ingest_nodes_created = len(result.created_nodes) if result.created_nodes else 0
            ingest_edges_created = len(result.created_edges) if result.created_edges else 0
            ingest_nav_used = result.nav_used

        # ── Persist ingest research report ─────────────────────────
        try:
            from kt_db.repositories.research_reports import ResearchReportRepository

            async with _open_graph_session(worker_state, input.graph_id) as session:
                await ResearchReportRepository(session).create(
                    message_id=msg_uuid,
                    conversation_id=conv_uuid,
                    nodes_created=ingest_nodes_created,
                    edges_created=ingest_edges_created,
                    waves_completed=1,
                    nav_budget=input.nav_budget,
                    nav_used=ingest_nav_used,
                    scope_summaries=[f"Ingestion: {ingest_nodes_created} nodes, {ingest_edges_created} edges"],
                    total_prompt_tokens=0,
                    total_completion_tokens=0,
                    total_cost_usd=0.0,
                    usage_by_model=None,
                    usage_by_task=None,
                    report_type="ingestion",
                    workflow_run_id=ctx.workflow_run_id,
                )
                await session.commit()
        except Exception:
            logger.warning(
                "Failed to persist ingest research report for message %s",
                input.message_id,
                exc_info=True,
            )

    except Exception as e:
        logger.exception("Ingest failed: conv=%s", input.conversation_id)
        async with _open_graph_session(worker_state, input.graph_id) as session:
            repo = ConversationRepository(session)
            await repo.update_message(msg_uuid, status="failed", error=str(e))
            await session.commit()
        raise

    ctx.log(f"Ingest confirm complete: conv={input.conversation_id}")
    return {}


# ══════════════════════════════════════════════════════════════
# Ingest partition workflow — one parallel agent per partition
# ══════════════════════════════════════════════════════════════

ingest_partition_wf = hatchet.workflow(
    name="ingest_partition",
    input_validator=IngestPartitionInput,
)


@tracked_task(
    ingest_partition_wf,
    task_type="ingest_partition",
    durable=True,
    execution_timeout=timedelta(hours=4),
    schedule_timeout=_schedule_timeout,
)
async def run_ingest_partition(input: IngestPartitionInput, ctx: DurableContext) -> dict:
    """Run ingest agent on a partition of the content index.

    Each partition agent gets the full content index (for TOC context) but
    only its assigned range is accessible via get_summary/browse_facts.
    """
    worker_state = cast(WorkerState, ctx.lifespan)

    from kt_worker_ingest.agents.ingest_worker import IngestWorker
    from kt_worker_ingest.ingest.content_index import ContentIndex, IndexEntry, backfill_fact_counts
    from kt_worker_ingest.ingest.pipeline import (
        reconstruct_decomp_summary,
        reconstruct_processed_sources,
    )

    ctx.log(
        f"Partition {input.partition_id[:8]} starting: "
        f"entries [{input.index_range_start}-{input.index_range_end}), "
        f"budget={input.nav_budget}"
    )

    conv_uuid = uuid.UUID(input.conversation_id)

    # Reconstruct content index from the partition data
    # We build a lightweight ContentIndex with all titles for TOC context
    entries: list[IndexEntry] = []
    for i, title in enumerate(input.all_titles):
        entries.append(
            IndexEntry(
                idx=i,
                title=title,
                summary="",  # Summaries loaded on-demand via get_summary
                char_count=0,
                source_name="",
            )
        )

    content_index = ContentIndex(entries=entries)
    backfill_fact_counts(
        content_index,
        total_facts=input.total_facts,
        fact_type_counts=input.fact_type_counts,
    )

    partition_range = (input.index_range_start, input.index_range_end)

    # Reconstruct decomp summary and processed sources
    async with _open_graph_session(worker_state, input.graph_id) as session:
        decomp_summary = await reconstruct_decomp_summary(conv_uuid, session)
        processed_sources = await reconstruct_processed_sources(conv_uuid, session)

    # Run the ingest agent scoped to this partition
    async with _open_sessions(worker_state, input.graph_id) as (_, write_session):
        agent_ctx = await _build_agent_context(
            worker_state,
            write_session=write_session,
        )

        result = await IngestWorker(agent_ctx).run(
            input.conversation_id,
            processed_sources,
            input.nav_budget,
            decomp_summary,
            content_index=content_index,
            partition_index_range=partition_range,
        )

        await write_session.commit()

    ctx.log(
        f"Partition {input.partition_id[:8]} complete: "
        f"{len(result.created_nodes)} nodes, {len(result.created_edges)} edges"
    )

    return IngestPartitionOutput(
        created_node_ids=result.created_nodes,
        created_edge_ids=result.created_edges,
        nav_used=result.nav_used,
        summary=result.answer or "",
    ).model_dump()


# ══════════════════════════════════════════════════════════════
# Phased document ingest — decompose workflow (Phase 1)
# ══════════════════════════════════════════════════════════════

ingest_decompose_wf = hatchet.workflow(
    name="ingest_decompose",
    input_validator=IngestDecomposeInput,
)


@tracked_task(
    ingest_decompose_wf,
    task_type="ingest_decompose",
    durable=True,
    execution_timeout=timedelta(hours=6),
    schedule_timeout=_schedule_timeout,
)
async def handle_decompose(input: IngestDecomposeInput, ctx: DurableContext) -> dict:
    """Phase 1: Process sources, decompose facts, extract nodes, filter, prioritize.

    Stores proposed nodes in assistant message metadata_json.
    """
    worker_state = cast(WorkerState, ctx.lifespan)

    from kt_db.repositories.conversations import ConversationRepository
    from kt_worker_ingest.ingest.pipeline import (
        DecompositionSummary,
        process_ingest_sources,
    )
    from kt_worker_ingest.ingest.public_cache import (
        apply_public_cache_lookups,
        contribute_processed_to_public,
    )

    ctx.log(f"Ingest decompose starting: conv={input.conversation_id}")

    # Resolve public-cache toggles ONCE per workflow run.
    _pcache_graph_uuid, _use_public_cache, _contribute_to_public = await _resolve_graph_meta(
        worker_state, input.graph_id
    )

    msg_uuid = uuid.UUID(input.message_id)
    conv_uuid = uuid.UUID(input.conversation_id)

    # Mark as running
    async with _open_graph_session(worker_state, input.graph_id) as session:
        repo = ConversationRepository(session)
        await repo.update_message(msg_uuid, status="running")
        await session.commit()

    try:
        # ── Phase 1: Process sources (idempotent) ────────────────
        async with _open_sessions(worker_state, input.graph_id) as (_, write_session):
            agent_ctx = await _build_agent_context(
                worker_state,
                write_session=write_session,
                user_id=input.user_id,
            )

            # process_ingest_sources needs graph-db for IngestSource table;
            # open a short-lived session scoped to the correct graph schema.
            async with _open_graph_session(worker_state, input.graph_id) as graph_session:
                processed = await process_ingest_sources(
                    conv_uuid,
                    graph_session,
                    agent_ctx.file_data_store,
                    write_session=write_session,
                )
                await graph_session.commit()
            await write_session.commit()

        if not processed:
            raise ValueError("No sources could be processed")

        ctx.log(f"Processing complete: {len(processed)} sources")

        ctx.refresh_timeout("4h")

        # ── Phase 2: Decompose into facts + extract entities + create seeds
        # Uses the same Hatchet workflow as bottom-up (decompose_sources_wf)
        # so all source decomposition follows a single code path.

        # ── Public-cache lookups (before decomposition) ────────────
        cache_hit_source_ids: set[str] = set()
        async with _open_sessions(worker_state, input.graph_id) as (_, write_session):
            agent_ctx = await _build_agent_context(
                worker_state,
                write_session=write_session,
                user_id=input.user_id,
                graph_id=input.graph_id,
            )
            try:
                _, cache_hit_source_ids, _hit_summaries = await apply_public_cache_lookups(
                    agent_ctx.graph_engine,
                    processed,
                    use_public_cache=_use_public_cache,
                    chunk_selection=None,
                )
            except Exception:
                logger.warning("public cache lookup phase failed", exc_info=True)

            if cache_hit_source_ids:
                ctx.log(f"Public cache: {len(cache_hit_source_ids)} hit(s)")

        # Separate text vs image sources
        text_source_ids: list[str] = []
        image_source_ids: list[str] = []
        for ps in processed:
            if not ps.raw_source_id:
                continue
            if ps.is_image:
                image_source_ids.append(str(ps.raw_source_id))
            else:
                text_source_ids.append(str(ps.raw_source_id))

        all_source_ids = text_source_ids + image_source_ids

        # Dispatch the shared decomposition workflow (same as bottom-up)
        from kt_hatchet.client import run_workflow
        from kt_hatchet.models import DecomposeSourcesOutput

        decompose_result = await run_workflow(
            "decompose_sources",
            {
                "raw_source_ids": all_source_ids,
                "image_source_ids": image_source_ids,
                "concept": "",
                "query_context": "",
                "message_id": input.message_id,
                "conversation_id": input.conversation_id,
                "graph_id": input.graph_id,
            },
        )

        # aio_result() returns {task_name: output} — unwrap the task key
        if isinstance(decompose_result, dict) and len(decompose_result) == 1:
            decompose_result = next(iter(decompose_result.values()))
        decompose_output = DecomposeSourcesOutput.model_validate(decompose_result)

        # Map to DecompositionSummary for downstream compatibility
        decomp_summary = DecompositionSummary(
            total_facts=decompose_output.total_fact_count,
            total_chunks_processed=len(processed),
            total_sources=len(processed),
            inserted_fact_ids=list(decompose_output.fact_ids),
        )

        # ── Contribute to public graph (after decomposition) ───────
        async with _open_sessions(worker_state, input.graph_id) as (_, write_session):
            agent_ctx = await _build_agent_context(
                worker_state,
                write_session=write_session,
                user_id=input.user_id,
                graph_id=input.graph_id,
            )
            try:
                contributed = await contribute_processed_to_public(
                    agent_ctx.graph_engine,
                    processed,
                    contribute_to_public=_contribute_to_public,
                    share_with_public_graph=input.share_with_public_graph,
                    cache_hit_source_ids=cache_hit_source_ids,
                )
                if contributed:
                    ctx.log(f"Public contribute: pushed {contributed} source(s) upstream")
            except Exception:
                logger.warning("public contribute phase failed", exc_info=True)

            await write_session.commit()

        ctx.log(
            f"Decomposition complete: {decomp_summary.total_facts} facts "
            f"from {decomp_summary.total_chunks_processed} chunks, "
            f"{len(decompose_output.seed_keys)} seeds"
        )

        # ── Post-job fact dedup ──────────────────────────────────
        # All just-inserted facts have dedup_status='pending'. Run the
        # dedup workflow synchronously here so that by the time the
        # user confirms and ingest_build_wf fans out node pipelines,
        # every surviving fact is 'ready' and no junction row will
        # reference a loser UUID.
        if decomp_summary.inserted_fact_ids:
            try:
                from kt_hatchet.client import run_workflow

                # graph_slug is required by Hatchet concurrency expression
                _dedup_slug = "default"
                if input.graph_id and worker_state.graph_resolver:
                    try:
                        _gs = await worker_state.graph_resolver.resolve(uuid.UUID(input.graph_id))
                        _dedup_slug = _gs.graph.slug
                    except Exception:
                        logger.debug("Could not resolve graph slug for dedup", exc_info=True)
                dedup_input: dict[str, object] = {
                    "fact_ids": decomp_summary.inserted_fact_ids,
                    "graph_slug": _dedup_slug,
                }
                if input.graph_id:
                    dedup_input["graph_id"] = input.graph_id
                await run_workflow("dedup_pending_facts_wf", dedup_input)
                ctx.log(f"Dedup complete for {len(decomp_summary.inserted_fact_ids)} facts")
            except Exception:
                logger.warning(
                    "Failed to run dedup_pending_facts_wf during ingest decompose",
                    exc_info=True,
                )

        # ── Post-job seed dedup ───────────────────────────────────
        # store_seeds_from_extracted_nodes writes seeds as status=pending.
        # Dispatch seed_dedup_batch to run text search → embedding → LLM
        # multiplex → promote pending→active/merged/ambiguous. Without
        # this, auto_build (which filters status=active) would skip all
        # newly extracted seeds.
        if decompose_output.seed_keys:
            try:
                from kt_hatchet.client import run_workflow

                seed_dedup_input: dict[str, object] = {
                    "seed_keys": list(decompose_output.seed_keys),
                    "scope_id": input.conversation_id or "",
                }
                if input.graph_id:
                    seed_dedup_input["graph_id"] = input.graph_id
                await run_workflow("seed_dedup_batch", seed_dedup_input)
                ctx.log(f"Seed dedup complete for {len(decompose_output.seed_keys)} seeds")
            except Exception:
                logger.warning(
                    "Failed to run seed_dedup_batch during ingest decompose",
                    exc_info=True,
                )

        ctx.refresh_timeout("2h")

        # ── Build proposals directly from seeds ───────────────────
        # Seeds are created during decomposition (step 2). The old
        # extract→filter→prioritize pipeline was replaced by the seed
        # system — all seeds with enough facts become nodes automatically.

        from kt_db.keys import key_to_uuid
        from kt_db.repositories.write_seeds import WriteSeedRepository

        proposed_nodes: list[ProposedNode] = []
        try:
            async with _open_sessions(worker_state, input.graph_id) as (_, write_session):
                if write_session is not None:
                    seed_repo = WriteSeedRepository(write_session)
                    seeds = await seed_repo.list_seeds(
                        exclude_merged=True,
                        limit=500,
                    )

                    for seed in seeds:
                        if seed.status in ("garbage",):
                            continue

                        existing_id = None
                        if seed.status == "promoted" and seed.promoted_node_key:
                            existing_id = str(key_to_uuid(seed.promoted_node_key))

                        aliases = (seed.metadata_ or {}).get("aliases", []) if seed.metadata_ else []

                        proposed_nodes.append(
                            ProposedNode(
                                name=seed.name,
                                node_type=seed.node_type,
                                entity_subtype=seed.entity_subtype,
                                priority=5,
                                selected=True,
                                seed_key=seed.key,
                                existing_node_id=existing_id,
                                fact_count=seed.fact_count,
                                aliases=aliases,
                            )
                        )
        except Exception:
            logger.debug("Seed listing failed during ingest decompose", exc_info=True)

        ctx.log(f"Built {len(proposed_nodes)} proposals from seeds")

        # ── Auto-build: fan out node_pipeline_wf for all seeds ────
        created_node_ids: list[str] = []
        created_edge_ids: list[str] = []

        if proposed_nodes:
            from hatchet_sdk import TriggerWorkflowOptions

            from kt_db.keys import make_seed_key as _make_seed_key
            from kt_hatchet.models import BuildNodeInput
            from kt_worker_nodes.workflows.node_pipeline import node_pipeline_wf

            node_meta = TriggerWorkflowOptions(
                additional_metadata={
                    "message_id": input.message_id,
                    "conversation_id": input.conversation_id,
                }
            )

            bulk_items = []
            for pn in proposed_nodes:
                sk = pn.seed_key or _make_seed_key(pn.node_type, pn.name)
                bulk_items.append(
                    node_pipeline_wf.create_bulk_run_item(
                        input=BuildNodeInput(
                            scope_id="ingest_build",
                            concept=pn.name,
                            node_type=pn.node_type,
                            entity_subtype=pn.entity_subtype,
                            seed_key=sk,
                            existing_node_id=pn.existing_node_id,
                            message_id=input.message_id,
                            conversation_id=input.conversation_id,
                            graph_id=input.graph_id,
                        ),
                        options=node_meta,
                    )
                )

            ctx.log(f"Auto-build: dispatching {len(bulk_items)} node pipelines")
            ctx.refresh_timeout("4h")

            results = await node_pipeline_wf.aio_run_many(bulk_items)

            for result in results:
                create_data: dict = result.get("create_node", {}) if isinstance(result, dict) else {}
                dim_data: dict = result.get("generate_dimensions", {}) if isinstance(result, dict) else {}
                node_id = create_data.get("node_id")
                if node_id:
                    created_node_ids.append(node_id)
                created_edge_ids.extend(dim_data.get("edge_ids", []))

            ctx.log(f"Auto-build: created {len(created_node_ids)} nodes, {len(created_edge_ids)} edges")

        # ── Persist research report ───────────────────────────────
        if created_node_ids:
            try:
                from kt_db.repositories.research_reports import ResearchReportRepository

                async with _open_graph_session(worker_state, input.graph_id) as session:
                    await ResearchReportRepository(session).create(
                        message_id=msg_uuid,
                        conversation_id=conv_uuid,
                        nodes_created=len(created_node_ids),
                        edges_created=len(created_edge_ids),
                        waves_completed=1,
                        explore_budget=0,
                        explore_used=0,
                        nav_budget=len(proposed_nodes),
                        nav_used=len(created_node_ids),
                        scope_summaries=[f"Built {len(created_node_ids)} nodes, {len(created_edge_ids)} edges"],
                        total_prompt_tokens=0,
                        total_completion_tokens=0,
                        total_cost_usd=0.0,
                        usage_by_model=None,
                        usage_by_task=None,
                        report_type="ingestion",
                        workflow_run_id=ctx.workflow_run_id,
                    )
                    await session.commit()
            except Exception:
                logger.warning("Failed to persist research report", exc_info=True)

        output = IngestDecomposeOutput(
            fact_count=decomp_summary.total_facts,
            source_count=len(processed),
            proposed_nodes=proposed_nodes,
            content_summary=decomp_summary.source_summaries[0].get("name", "")
            if decomp_summary.source_summaries
            else "",
            key_topics=decomp_summary.key_topics[:20],
            fact_type_counts=decomp_summary.fact_type_counts,
            nodes_created=len(created_node_ids),
            edges_created=len(created_edge_ids),
            created_node_ids=created_node_ids,
            created_edge_ids=created_edge_ids,
        )

        async with _open_graph_session(worker_state, input.graph_id) as session:
            repo = ConversationRepository(session)
            await repo.update_message(
                msg_uuid,
                status="completed",
                content=f"Built {len(created_node_ids)} nodes and {len(created_edge_ids)} edges from {decomp_summary.total_facts} facts.",
                metadata_json=output.model_dump(),
                created_nodes=created_node_ids,
                created_edges=created_edge_ids,
            )
            await session.commit()

    except Exception as e:
        logger.exception("Ingest decompose failed: conv=%s", input.conversation_id)
        async with _open_graph_session(worker_state, input.graph_id) as session:
            repo = ConversationRepository(session)
            await repo.update_message(msg_uuid, status="failed", error=str(e))
            await session.commit()
        raise

    ctx.log(f"Ingest decompose+build complete: {len(created_node_ids)} nodes from {len(proposed_nodes)} seeds")
    return {}


# ══════════════════════════════════════════════════════════════
# Phased document ingest — build workflow (Phase 2)
# ══════════════════════════════════════════════════════════════

ingest_build_wf = hatchet.workflow(
    name="ingest_build",
    input_validator=IngestBuildInput,
)


@tracked_task(
    ingest_build_wf,
    task_type="ingest_build",
    durable=True,
    execution_timeout=timedelta(hours=6),
    schedule_timeout=_schedule_timeout,
)
async def handle_build(input: IngestBuildInput, ctx: DurableContext) -> dict:
    """Phase 2: Build user-confirmed nodes from document ingest.

    Follows the same pattern as bottom_up_build_wf.
    """
    from kt_hatchet.models import BuildNodeInput
    from kt_hatchet.utils import resolve_perspective_source_ids

    state = cast(WorkerState, ctx.lifespan)

    msg_uuid = uuid.UUID(input.message_id)
    conv_uuid = uuid.UUID(input.conversation_id)

    # Mark as running
    async with _open_graph_session(state, input.graph_id) as session:
        from kt_db.repositories.conversations import ConversationRepository

        repo = ConversationRepository(session)
        await repo.update_message(msg_uuid, status="running")
        await session.commit()

    ctx.log(f"Starting ingest build (Phase 2): {len(input.selected_nodes)} nodes")

    try:
        # ── Phase: Create nodes via node_pipeline_wf ─────────────
        from hatchet_sdk import TriggerWorkflowOptions

        from kt_worker_nodes.workflows.node_pipeline import node_pipeline_wf

        node_meta = TriggerWorkflowOptions(
            additional_metadata={
                "message_id": input.message_id,
                "conversation_id": input.conversation_id,
            }
        )

        from kt_db.keys import make_seed_key as _make_seed_key

        bulk_items = []
        for node in input.selected_nodes:
            sk = node.seed_key or _make_seed_key(node.name)
            bulk_items.append(
                node_pipeline_wf.create_bulk_run_item(
                    input=BuildNodeInput(
                        scope_id="ingest_build",
                        concept=node.name,
                        node_type=node.node_type,
                        entity_subtype=node.entity_subtype,
                        seed_key=sk,
                        existing_node_id=node.existing_node_id,
                        message_id=input.message_id,
                        conversation_id=input.conversation_id,
                    ),
                    options=node_meta,
                )
            )

        results = await node_pipeline_wf.aio_run_many(bulk_items)

        ctx.refresh_timeout("30m")

        # ── Collect results ──────────────────────────────────────
        created_node_ids: list[str] = []
        created_edge_ids: list[str] = []
        built_nodes: list[dict[str, str]] = []

        for result in results:
            create_data: dict = result.get("create_node", {}) if isinstance(result, dict) else {}
            dim_data: dict = result.get("generate_dimensions", {}) if isinstance(result, dict) else {}
            node_id = create_data.get("node_id")
            if node_id:
                created_node_ids.append(node_id)
                built_nodes.append(
                    {
                        "node_id": node_id,
                        "concept": create_data.get("concept", ""),
                        "node_type": create_data.get("node_type", "concept"),
                    }
                )
                created_edge_ids.extend(dim_data.get("edge_ids", []))

        ctx.log(f"Created {len(created_node_ids)} nodes, {len(created_edge_ids)} edges")

        # ── Phase: Build perspectives ────────────────────────────
        perspective_plans: list[dict[str, Any]] = []
        for node in input.selected_nodes:
            if node.perspectives:
                for persp in node.perspectives:
                    perspective_plans.append(
                        {
                            "claim": persp.claim,
                            "antithesis": persp.antithesis,
                            "source_concept_id": node.name,
                        }
                    )

        perspective_node_count = 0
        if perspective_plans and built_nodes:
            perspective_plans = resolve_perspective_source_ids(perspective_plans, built_nodes)

            ctx.log(f"Building {len(perspective_plans)} perspective pairs")

            from kt_hatchet.models import BuildCompositeInput
            from kt_worker_nodes.workflows.composite import build_composite_task

            composite_items = []
            for plan in perspective_plans:
                source_concept_id = plan.get("source_concept_id", "")
                persp_source_ids = [source_concept_id] if source_concept_id else []
                persp_source_ids.extend([n for n in created_node_ids if n != source_concept_id])

                # Thesis
                composite_items.append(
                    build_composite_task.create_bulk_run_item(
                        input=BuildCompositeInput(
                            node_type="perspective",
                            concept=plan["claim"],
                            source_node_ids=persp_source_ids,
                            parent_concept=source_concept_id,
                            conversation_id=input.conversation_id,
                            message_id=input.message_id,
                            scope_id="ingest_build",
                        ),
                    )
                )

                # Antithesis
                if plan.get("antithesis"):
                    composite_items.append(
                        build_composite_task.create_bulk_run_item(
                            input=BuildCompositeInput(
                                node_type="perspective",
                                concept=plan["antithesis"],
                                source_node_ids=persp_source_ids,
                                parent_concept=source_concept_id,
                                conversation_id=input.conversation_id,
                                message_id=input.message_id,
                                scope_id="ingest_build",
                            ),
                        )
                    )

            if composite_items:
                try:
                    composite_results = await build_composite_task.aio_run_many(composite_items)
                    for cr in composite_results:
                        cr_data = cr if isinstance(cr, dict) else {}
                        cr_node_id = cr_data.get("node_id")
                        if cr_node_id and cr_node_id not in created_node_ids:
                            created_node_ids.append(cr_node_id)
                            perspective_node_count += 1
                        for eid in cr_data.get("draws_from_edge_ids", []):
                            if eid not in created_edge_ids:
                                created_edge_ids.append(eid)
                except Exception:
                    logger.exception("Ingest build: composite perspective build failed")

            ctx.log(f"Built {perspective_node_count} perspective nodes")

        # ── Persist research report ─────────────────────────────────
        try:
            from kt_db.repositories.research_reports import ResearchReportRepository

            async with _open_graph_session(state, input.graph_id) as session:
                await ResearchReportRepository(session).create(
                    message_id=msg_uuid,
                    conversation_id=conv_uuid,
                    nodes_created=len(created_node_ids),
                    edges_created=len(created_edge_ids),
                    waves_completed=1,
                    explore_budget=0,
                    explore_used=0,
                    nav_budget=len(input.selected_nodes),
                    nav_used=len(created_node_ids),
                    scope_summaries=[
                        f"Built {len(created_node_ids)} nodes "
                        f"({len(created_node_ids) - perspective_node_count} core, "
                        f"{perspective_node_count} perspectives), "
                        f"{len(created_edge_ids)} edges"
                    ],
                    total_prompt_tokens=0,
                    total_completion_tokens=0,
                    total_cost_usd=0.0,
                    usage_by_model=None,
                    usage_by_task=None,
                    report_type="ingestion",
                    workflow_run_id=ctx.workflow_run_id,
                )
                await session.commit()
        except Exception:
            logger.warning("Failed to persist research report", exc_info=True)

        # ── Update message with results ──────────────────────────
        async with _open_graph_session(state, input.graph_id) as session:
            from kt_db.repositories.conversations import ConversationRepository

            repo = ConversationRepository(session)
            await repo.update_message(
                msg_uuid,
                status="completed",
                content=f"Built {len(created_node_ids)} nodes and {len(created_edge_ids)} edges.",
                created_nodes=created_node_ids,
                created_edges=created_edge_ids,
                nav_used=len(created_node_ids),
                explore_used=0,
            )
            await session.commit()

    except Exception as e:
        logger.exception("Ingest build failed: conv=%s", input.conversation_id)
        async with _open_graph_session(state, input.graph_id) as session:
            from kt_db.repositories.conversations import ConversationRepository

            repo = ConversationRepository(session)
            await repo.update_message(msg_uuid, status="failed", error=str(e))
            await session.commit()
        raise

    ctx.log(f"Ingest build complete: {len(created_node_ids)} nodes, {len(created_edge_ids)} edges")

    return {}
