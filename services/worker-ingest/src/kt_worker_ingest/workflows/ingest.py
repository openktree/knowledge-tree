"""Ingest workflows: ingest confirmation and ingest partition.

Extracted from the monolith conversations.py workflow file. These workflows
handle the full ingest pipeline:
- ``ingest_confirm_wf`` — Full ingest: process sources, decompose, build nodes
- ``ingest_partition_wf`` — Parallel partition agent for large documents
"""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import timedelta
from typing import Any, cast

from hatchet_sdk import DurableContext

from kt_hatchet.lifespan import WorkerState
from kt_hatchet.models import (
    IngestBuildInput,
    IngestConfirmInput,
    IngestDecomposeInput,
    IngestDecomposeOutput,
    IngestPartitionInput,
    IngestPartitionOutput,
    ProposedNode,
    ProposedPerspective,
)
from kt_hatchet.client import get_hatchet
from kt_config.settings import get_settings

logger = logging.getLogger(__name__)

hatchet = get_hatchet()
_schedule_timeout = timedelta(minutes=get_settings().hatchet_schedule_timeout_minutes)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@asynccontextmanager
async def _open_sessions(state: WorkerState) -> AsyncGenerator[tuple[Any, Any], None]:
    """Open graph-db session and write-db session.

    Yields ``(session, write_session)`` — caller must commit as needed.
    """
    async with state.session_factory() as session:
        write_session = state.write_session_factory()
        try:
            yield session, write_session
        finally:
            await write_session.close()


async def _build_agent_context(
    state: WorkerState,
    session: Any,
    *,
    emit_event: Any | None = None,
    write_session: Any | None = None,
    api_key: str | None = None,
) -> Any:
    """Build an AgentContext from WorkerState and an open session.

    Optionally wires up an ``emit_event`` callback so that worker classes
    (IngestWorker) can emit progress events through ``ctx.emit()``.

    ``write_session`` is required for any task that writes facts, nodes,
    edges, or dimensions.

    When ``api_key`` is provided, per-request ``ModelGateway`` and
    ``EmbeddingService`` instances are created instead of using the shared
    ones from ``WorkerState``.
    """
    from kt_agents_core.state import AgentContext
    from kt_graph.engine import GraphEngine

    if api_key:
        from kt_models.gateway import ModelGateway
        from kt_models.embeddings import EmbeddingService

        model_gateway = ModelGateway(api_key=api_key)
        embedding_service = EmbeddingService(api_key=api_key)
    else:
        model_gateway = state.model_gateway
        embedding_service = state.embedding_service

    graph_engine = GraphEngine(
        session,
        embedding_service,
        write_session=write_session,
        qdrant_client=state.qdrant_client,
    )
    return AgentContext(
        graph_engine=graph_engine,
        provider_registry=state.provider_registry,
        model_gateway=model_gateway,
        embedding_service=embedding_service,
        session=session,
        emit_event=emit_event,
        content_fetcher=state.content_fetcher,
        session_factory=state.session_factory,
        write_session_factory=state.write_session_factory,
        qdrant_client=state.qdrant_client,
    )


def _make_emit_callback(emit: Any) -> Any:
    """Wrap an emit coroutine to match the EventCallback interface.

    EventCallback expected by AgentContext and ingest pipeline has signature
    ``(event_type, **data) -> None``.
    """

    async def callback(event_type: str, **data: Any) -> None:
        try:
            await emit(event_type, data)
        except Exception:
            logger.warning("Failed to emit event %s", event_type, exc_info=True)

    return callback


# ══════════════════════════════════════════════════════════════
# Ingest confirmation workflow — full ingest pipeline
# ══════════════════════════════════════════════════════════════

ingest_confirm_wf = hatchet.workflow(
    name="ingest_confirm",
    input_validator=IngestConfirmInput,
)


@ingest_confirm_wf.durable_task(execution_timeout=timedelta(hours=12), schedule_timeout=_schedule_timeout)
async def handle_ingest(input: IngestConfirmInput, ctx: DurableContext) -> dict:
    """Run the full ingest pipeline: process sources, decompose, build nodes.

    Phase 1: Process ingest sources (text extraction, chunking)
    Phase 2: Decompose selected chunks into facts
    Phase 3: Run the ingest agent for node building
    """
    from kt_models.usage import start_usage_tracking
    from kt_hatchet.usage_helpers import flush_usage_to_db

    worker_state = cast(WorkerState, ctx.lifespan)
    start_usage_tracking()

    async def emit(event_type: str, payload: dict) -> None:
        try:
            await ctx.aio_put_stream(json.dumps({"type": event_type, **payload}))
        except Exception:
            logger.warning("Failed to stream event %s", event_type, exc_info=True)

    emit_cb = _make_emit_callback(emit)

    from kt_db.repositories.conversations import ConversationRepository
    from kt_worker_ingest.ingest.pipeline import (
        build_chunk_list,
        decompose_all_sources,
        process_ingest_sources,
    )
    from kt_worker_ingest.agents.ingest_worker import IngestWorker

    ctx.log(f"Ingest confirm starting: conv={input.conversation_id}")

    msg_uuid = uuid.UUID(input.message_id)
    conv_uuid = uuid.UUID(input.conversation_id)

    # Mark as running
    async with worker_state.session_factory() as session:
        repo = ConversationRepository(session)
        await repo.update_message(msg_uuid, status="running")
        await session.commit()

    await emit("phase_change", {"phase": "running"})

    try:
        # ── Phase 1: Process sources ──────────────────────────────
        await emit(
            "pipeline_scope_start",
            {
                "scope_id": "ingest-processing",
                "scope_name": "Processing Sources",
            },
        )
        await emit(
            "pipeline_phase",
            {
                "scope_id": "ingest-processing",
                "phase": "processing",
                "status": "started",
            },
        )
        await emit(
            "activity_log",
            {
                "action": "Processing uploaded sources...",
                "tool": "ingest",
            },
        )

        async with _open_sessions(worker_state) as (session, write_session):
            agent_ctx = await _build_agent_context(
                worker_state,
                session,
                emit_event=emit_cb,
                write_session=write_session,
                api_key=input.api_key,
            )

            processed = await process_ingest_sources(
                conv_uuid,
                agent_ctx.session,
                agent_ctx.file_data_store,
                emit=emit_cb,
                write_session=write_session,
            )
            await write_session.commit()
            await agent_ctx.session.commit()

        if not processed:
            await emit(
                "pipeline_phase",
                {
                    "scope_id": "ingest-processing",
                    "phase": "processing",
                    "status": "completed",
                },
            )
            await emit(
                "pipeline_scope_end",
                {
                    "scope_id": "ingest-processing",
                    "status": "failed",
                    "error": "No sources",
                },
            )
            raise ValueError("No sources could be processed")

        await emit(
            "pipeline_phase",
            {
                "scope_id": "ingest-processing",
                "phase": "processing",
                "status": "completed",
                "detail": f"Processed {len(processed)} source(s)",
            },
        )
        await emit(
            "pipeline_scope_end",
            {
                "scope_id": "ingest-processing",
                "node_count": 0,
            },
        )

        ctx.log(f"Phase 1 complete: {len(processed)} sources processed")

        # ── Convert flat selected_chunks to per-source selection ──
        chunk_selection = None
        if input.selected_chunks is not None:
            selected_set = set(input.selected_chunks)
            chunk_list = build_chunk_list(processed)
            chunk_selection_dict: dict[str, set[int]] = {}
            source_local_idx: dict[str, int] = {}
            for c in chunk_list:
                sid = c.source_id
                local = source_local_idx.get(sid, 0)
                source_local_idx[sid] = local + 1
                if c.chunk_index in selected_set:
                    chunk_selection_dict.setdefault(sid, set()).add(local)
            for ps in processed:
                if ps.source_id not in chunk_selection_dict:
                    chunk_selection_dict[ps.source_id] = set()
            chunk_selection = chunk_selection_dict

        # Keep gRPC stream alive between phases
        ctx.refresh_timeout("4h")

        # ── Phase 2: Decompose selected sources ───────────────────
        await emit(
            "pipeline_scope_start",
            {
                "scope_id": "ingest-decomposition",
                "scope_name": "Decomposing Facts",
            },
        )
        await emit(
            "pipeline_phase",
            {
                "scope_id": "ingest-decomposition",
                "phase": "decomposition",
                "status": "started",
            },
        )
        await emit("phase_change", {"phase": "decomposing"})

        selected_label = "selected" if chunk_selection is not None else "all"
        await emit(
            "activity_log",
            {
                "action": f"Decomposing {selected_label} chunks...",
                "tool": "ingest",
            },
        )

        async with _open_sessions(worker_state) as (session, write_session):
            agent_ctx = await _build_agent_context(
                worker_state,
                session,
                emit_event=emit_cb,
                write_session=write_session,
                api_key=input.api_key,
            )

            decomp_summary = await decompose_all_sources(
                processed,
                agent_ctx,
                emit=emit_cb,
                chunk_selection=chunk_selection,
            )
            await write_session.commit()
            await agent_ctx.session.commit()

        await emit(
            "activity_log",
            {
                "action": (
                    f"Decomposition complete: {decomp_summary.total_facts} facts "
                    f"from {decomp_summary.total_chunks_processed} chunks"
                ),
                "tool": "ingest",
            },
        )
        await emit(
            "pipeline_phase",
            {
                "scope_id": "ingest-decomposition",
                "phase": "decomposition",
                "status": "completed",
                "fact_count": decomp_summary.total_facts,
                "detail": (f"{decomp_summary.total_facts} facts from {decomp_summary.total_chunks_processed} chunks"),
            },
        )
        await emit(
            "pipeline_scope_end",
            {
                "scope_id": "ingest-decomposition",
            },
        )

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
            async with worker_state.session_factory() as session:
                agent_ctx = await _build_agent_context(
                    worker_state, session, emit_event=emit_cb,
                    api_key=input.api_key,
                )
                content_index = await build_content_index(
                    processed,
                    agent_ctx.model_gateway,
                    agent_ctx.file_data_store,
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
        await emit("phase_change", {"phase": "building"})

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
            child_meta = TriggerWorkflowOptions(additional_metadata={
                "message_id": input.message_id,
                "conversation_id": input.conversation_id,
            })

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

            await emit("activity_log", {
                "action": f"Splitting across {len(partitions)} parallel agents",
                "tool": "ingest",
            })

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
                task_data = raw_result.get("run_ingest_partition", raw_result) if isinstance(raw_result, dict) else raw_result
                out = IngestPartitionOutput.model_validate(task_data)
                all_created_nodes.extend(out.created_node_ids)
                all_created_edges.extend(out.created_edge_ids)
                total_nav_used += out.nav_used
                if out.summary:
                    partition_summaries.append(out.summary)

            # Build subgraph from merged results
            from kt_agents_core.results import build_ingest_subgraph

            async with worker_state.session_factory() as session:
                merge_ctx = await _build_agent_context(worker_state, session, emit_event=emit_cb, api_key=input.api_key)
                subgraph = await build_ingest_subgraph(all_created_nodes, all_created_edges, merge_ctx)

            # Persist merged result
            merged_answer = "\n\n---\n\n".join(partition_summaries) if partition_summaries else ""
            async with worker_state.session_factory() as session:
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
            async with _open_sessions(worker_state) as (session, write_session):
                agent_ctx = await _build_agent_context(
                    worker_state,
                    session,
                    emit_event=emit_cb,
                    write_session=write_session,
                    api_key=input.api_key,
                )

                result = await IngestWorker(agent_ctx).run(
                    input.conversation_id,
                    processed,
                    input.nav_budget,
                    decomp_summary,
                    content_index=content_index,
                )

                await write_session.commit()
                await session.commit()

            # Persist result
            async with worker_state.session_factory() as session:
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

        # ── Flush usage to DB (self-reporting) ─────────────────────
        await flush_usage_to_db(
            worker_state.write_session_factory,
            input.conversation_id,
            input.message_id,
            "ingest",
        )

        # ── Persist ingest research report ─────────────────────────
        try:
            from kt_db.repositories.research_reports import ResearchReportRepository

            async with worker_state.session_factory() as session:
                await ResearchReportRepository(session).create(
                    message_id=msg_uuid,
                    conversation_id=conv_uuid,
                    nodes_created=ingest_nodes_created,
                    edges_created=ingest_edges_created,
                    waves_completed=1,
                    nav_budget=input.nav_budget,
                    nav_used=ingest_nav_used,
                    scope_summaries=[
                        f"Ingestion: {ingest_nodes_created} nodes, "
                        f"{ingest_edges_created} edges"
                    ],
                    total_prompt_tokens=0,
                    total_completion_tokens=0,
                    total_cost_usd=0.0,
                    usage_by_model=None,
                    usage_by_task=None,
                    report_type="ingestion",
                )
                await session.commit()
        except Exception:
            logger.warning(
                "Failed to persist ingest research report for message %s",
                input.message_id, exc_info=True,
            )

    except Exception as e:
        logger.exception("Ingest failed: conv=%s", input.conversation_id)
        async with worker_state.session_factory() as session:
            repo = ConversationRepository(session)
            await repo.update_message(msg_uuid, status="failed", error=str(e))
            await session.commit()
        await emit("phase_change", {"phase": "completed"})
        await emit("done", {})
        raise

    await emit("phase_change", {"phase": "completed"})
    await emit("done", {})

    ctx.log(f"Ingest confirm complete: conv={input.conversation_id}")
    return {}


# ══════════════════════════════════════════════════════════════
# Ingest partition workflow — one parallel agent per partition
# ══════════════════════════════════════════════════════════════

ingest_partition_wf = hatchet.workflow(
    name="ingest_partition",
    input_validator=IngestPartitionInput,
)


@ingest_partition_wf.durable_task(execution_timeout=timedelta(hours=4), schedule_timeout=_schedule_timeout)
async def run_ingest_partition(input: IngestPartitionInput, ctx: DurableContext) -> dict:
    """Run ingest agent on a partition of the content index.

    Each partition agent gets the full content index (for TOC context) but
    only its assigned range is accessible via get_summary/browse_facts.
    """
    worker_state = cast(WorkerState, ctx.lifespan)

    async def emit(event_type: str, payload: dict) -> None:
        try:
            await ctx.aio_put_stream(json.dumps({"type": event_type, **payload}))
        except Exception:
            logger.warning("Failed to stream event %s", event_type, exc_info=True)

    emit_cb = _make_emit_callback(emit)

    from kt_db.repositories.conversations import ConversationRepository
    from kt_worker_ingest.ingest.content_index import ContentIndex, IndexEntry, backfill_fact_counts
    from kt_worker_ingest.ingest.pipeline import (
        DecompositionSummary,
        reconstruct_decomp_summary,
        reconstruct_processed_sources,
    )
    from kt_worker_ingest.agents.ingest_worker import IngestWorker

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
        entries.append(IndexEntry(
            idx=i,
            title=title,
            summary="",  # Summaries loaded on-demand via get_summary
            char_count=0,
            source_name="",
        ))

    content_index = ContentIndex(entries=entries)
    backfill_fact_counts(
        content_index,
        total_facts=input.total_facts,
        fact_type_counts=input.fact_type_counts,
    )

    partition_range = (input.index_range_start, input.index_range_end)

    # Reconstruct decomp summary and processed sources
    async with worker_state.session_factory() as session:
        decomp_summary = await reconstruct_decomp_summary(conv_uuid, session)
        processed_sources = await reconstruct_processed_sources(conv_uuid, session)

    # Run the ingest agent scoped to this partition
    async with _open_sessions(worker_state) as (session, write_session):
        agent_ctx = await _build_agent_context(
            worker_state, session, emit_event=emit_cb,
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
        await session.commit()

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

@ingest_decompose_wf.durable_task(execution_timeout=timedelta(hours=6), schedule_timeout=_schedule_timeout)
async def handle_decompose(input: IngestDecomposeInput, ctx: DurableContext) -> dict:
    """Phase 1: Process sources, decompose facts, extract nodes, filter, prioritize.

    Stores proposed nodes in assistant message metadata_json.
    """
    worker_state = cast(WorkerState, ctx.lifespan)

    async def emit(event_type: str, payload: dict) -> None:
        try:
            await ctx.aio_put_stream(json.dumps({"type": event_type, **payload}))
        except Exception:
            logger.warning("Failed to stream event %s", event_type, exc_info=True)

    emit_cb = _make_emit_callback(emit)

    from kt_db.repositories.conversations import ConversationRepository
    from kt_worker_ingest.ingest.pipeline import (
        build_chunk_list,
        decompose_all_sources,
        process_ingest_sources,
    )

    ctx.log(f"Ingest decompose starting: conv={input.conversation_id}")

    msg_uuid = uuid.UUID(input.message_id)
    conv_uuid = uuid.UUID(input.conversation_id)

    # Mark as running
    async with worker_state.session_factory() as session:
        repo = ConversationRepository(session)
        await repo.update_message(msg_uuid, status="running")
        await session.commit()

    await emit("phase_change", {"phase": "running"})

    try:
        # ── Phase 1: Process sources (idempotent) ────────────────
        await emit("pipeline_scope_start", {
            "scope_id": "ingest-processing",
            "scope_name": "Processing Sources",
        })

        async with _open_sessions(worker_state) as (session, write_session):
            agent_ctx = await _build_agent_context(
                worker_state, session, emit_event=emit_cb,
                write_session=write_session,
                api_key=input.api_key,
            )
            processed = await process_ingest_sources(
                conv_uuid, agent_ctx.session, agent_ctx.file_data_store,
                emit=emit_cb, write_session=write_session,
            )
            await write_session.commit()
            await agent_ctx.session.commit()

        if not processed:
            raise ValueError("No sources could be processed")

        await emit("pipeline_scope_end", {"scope_id": "ingest-processing"})
        ctx.log(f"Processing complete: {len(processed)} sources")

        # ── Convert selected_chunks to per-source selection ──────
        chunk_selection = None
        if input.selected_chunks is not None:
            selected_set = set(input.selected_chunks)
            chunk_list = build_chunk_list(processed)
            chunk_selection_dict: dict[str, set[int]] = {}
            source_local_idx: dict[str, int] = {}
            for c in chunk_list:
                sid = c.source_id
                local = source_local_idx.get(sid, 0)
                source_local_idx[sid] = local + 1
                if c.chunk_index in selected_set:
                    chunk_selection_dict.setdefault(sid, set()).add(local)
            for ps in processed:
                if ps.source_id not in chunk_selection_dict:
                    chunk_selection_dict[ps.source_id] = set()
            chunk_selection = chunk_selection_dict

        ctx.refresh_timeout("4h")

        # ── Phase 2: Decompose into facts ────────────────────────
        await emit("pipeline_scope_start", {
            "scope_id": "ingest-decomposition",
            "scope_name": "Decomposing Facts",
        })
        await emit("phase_change", {"phase": "decomposing"})

        async with _open_sessions(worker_state) as (session, write_session):
            agent_ctx = await _build_agent_context(
                worker_state, session, emit_event=emit_cb, write_session=write_session,
                api_key=input.api_key,
            )
            decomp_summary = await decompose_all_sources(
                processed, agent_ctx, emit=emit_cb, chunk_selection=chunk_selection,
            )
            await write_session.commit()
            await agent_ctx.session.commit()

        await emit("pipeline_scope_end", {"scope_id": "ingest-decomposition"})
        ctx.log(
            f"Decomposition complete: {decomp_summary.total_facts} facts "
            f"from {decomp_summary.total_chunks_processed} chunks"
        )

        ctx.refresh_timeout("2h")

        # ── Phase 3: Extract nodes from facts ────────────────────
        await emit("pipeline_scope_start", {
            "scope_id": "ingest-extraction",
            "scope_name": "Extracting Nodes",
        })
        await emit("phase_change", {"phase": "extracting"})

        # Load all facts for this conversation from graph-db
        from kt_worker_ingest.ingest.pipeline import reconstruct_decomp_summary
        from kt_facts.processing.entity_extraction import extract_entities_from_facts

        async with worker_state.session_factory() as session:
            # Get facts via the same pattern as reconstruct_decomp_summary
            from kt_db.models import Fact, FactSource, IngestSource
            from sqlalchemy import select as sa_select

            src_result = await session.execute(
                sa_select(IngestSource.raw_source_id).where(
                    IngestSource.conversation_id == conv_uuid,
                    IngestSource.raw_source_id.isnot(None),
                )
            )
            raw_source_ids = [row[0] for row in src_result.all()]

            facts: list[Fact] = []
            if raw_source_ids:
                fact_result = await session.execute(
                    sa_select(Fact)
                    .join(FactSource, Fact.id == FactSource.fact_id)
                    .where(FactSource.raw_source_id.in_(raw_source_ids))
                    .distinct()
                )
                facts = list(fact_result.scalars().all())

        ctx.log(f"Loaded {len(facts)} facts for extraction")

        if not facts:
            # No facts — return empty proposals
            async with worker_state.session_factory() as session:
                repo = ConversationRepository(session)
                output = IngestDecomposeOutput(
                    fact_count=0,
                    source_count=len(processed),
                    content_summary="No facts could be extracted.",
                )
                await repo.update_message(
                    msg_uuid,
                    status="completed",
                    content="Decomposition complete but no facts extracted.",
                    metadata_json=output.model_dump(),
                )
                await session.commit()
            await emit("done", {})
            return {}

        # Extract nodes from facts
        from kt_models.gateway import ModelGateway

        gateway = ModelGateway()
        extracted_nodes = await extract_entities_from_facts(facts, gateway) or []

        ctx.log(f"Extracted {len(extracted_nodes)} raw nodes from {len(facts)} facts")
        await emit("pipeline_scope_end", {"scope_id": "ingest-extraction"})

        ctx.refresh_timeout("1h")

        # ── Phase 4: Filter generic nodes ────────────────────────
        await emit("pipeline_scope_start", {
            "scope_id": "ingest-filtering",
            "scope_name": "Filtering & Prioritizing",
        })
        await emit("phase_change", {"phase": "filtering"})

        if extracted_nodes:
            from kt_worker_orchestrator.bottom_up.scope import _filter_nodes

            async with worker_state.session_factory() as session:
                agent_ctx = await _build_agent_context(worker_state, session, emit_event=emit_cb, api_key=input.api_key)
                scope_desc = (
                    decomp_summary.key_topics[0]
                    if decomp_summary.key_topics
                    else "document content"
                )
                filtered_nodes = await _filter_nodes(agent_ctx, extracted_nodes, scope_desc)
        else:
            filtered_nodes = []

        ctx.log(f"Filtered: {len(extracted_nodes)} → {len(filtered_nodes)} nodes")

        # ── Phase 5: Batched prioritization ──────────────────────
        await emit("phase_change", {"phase": "prioritizing"})

        if filtered_nodes:
            from kt_worker_orchestrator.bottom_up.scope import batched_prioritize_nodes

            # Build summary context for the LLM
            summary_parts: list[str] = []
            if decomp_summary.source_summaries:
                summary_parts.append(decomp_summary.source_summaries[0].get("name", ""))
            if decomp_summary.key_topics:
                summary_parts.append(f"Key topics: {', '.join(decomp_summary.key_topics[:15])}")
            if decomp_summary.fact_type_counts:
                type_str = ", ".join(f"{k}: {v}" for k, v in decomp_summary.fact_type_counts.items())
                summary_parts.append(f"Fact types: {type_str}")
            content_summary = "\n".join(summary_parts)

            generic_query = (
                "Extract and prioritize the main entities, concepts, events, "
                "and facts from the source material"
            )

            async with worker_state.session_factory() as session:
                agent_ctx = await _build_agent_context(worker_state, session, emit_event=emit_cb, api_key=input.api_key)
                prioritized = await batched_prioritize_nodes(
                    agent_ctx,
                    filtered_nodes,
                    query=generic_query,
                    content_summary=content_summary,
                )
        else:
            prioritized = []

        ctx.log(f"Prioritized {len(prioritized)} nodes")

        # ── Build proposals from deduplicated seeds ─────────────
        from kt_db.keys import make_seed_key, key_to_uuid
        from kt_db.repositories.write_seeds import WriteSeedRepository

        # Map prioritized node → seed key, preserving priority/perspectives
        priority_map: dict[str, dict[str, Any]] = {}
        for n in prioritized:
            sk = make_seed_key(n.get("node_type", "concept"), n.get("name", ""))
            priority_map[sk] = n

        # Fetch actual seed objects (post-dedup)
        seed_status: dict[str, Any] = {}
        try:
            async with _open_sessions(worker_state) as (session, write_session):
                if write_session is not None:
                    seed_repo = WriteSeedRepository(write_session)
                    seed_status = await seed_repo.get_seeds_by_keys_batch(
                        list(priority_map.keys()),
                    )
        except Exception:
            logger.debug("Seed status lookup failed during ingest decompose", exc_info=True)

        proposed_nodes: list[ProposedNode] = []
        seen_keys: set[str] = set()
        for sk, pri in priority_map.items():
            seed = seed_status.get(sk)

            # Follow merge chain
            if seed and seed.status == "merged" and seed.merged_into_key:
                winner = seed_status.get(seed.merged_into_key)
                if winner:
                    sk = seed.merged_into_key
                    seed = winner
                else:
                    continue

            if sk in seen_keys:
                continue
            seen_keys.add(sk)

            existing_id = None
            if seed and seed.status == "promoted" and seed.promoted_node_key:
                existing_id = str(key_to_uuid(seed.promoted_node_key))

            name = seed.name if seed else pri.get("name", "")
            node_type = seed.node_type if seed else pri.get("node_type", "concept")
            entity_subtype = seed.entity_subtype if seed else pri.get("entity_subtype")
            fact_count = seed.fact_count if seed else 0
            aliases = (seed.metadata_ or {}).get("aliases", []) if seed else []

            proposed_nodes.append(ProposedNode(
                name=name,
                node_type=node_type,
                entity_subtype=entity_subtype,
                priority=pri.get("priority", 5),
                selected=pri.get("selected", True),
                seed_key=sk,
                existing_node_id=existing_id,
                fact_count=fact_count,
                aliases=aliases,
                perspectives=[
                    ProposedPerspective(claim=p["claim"], antithesis=p["antithesis"])
                    for p in pri.get("perspectives", [])
                    if isinstance(p, dict) and p.get("claim") and p.get("antithesis")
                ],
            ))

        output = IngestDecomposeOutput(
            fact_count=decomp_summary.total_facts,
            source_count=len(processed),
            proposed_nodes=proposed_nodes,
            content_summary=decomp_summary.source_summaries[0].get("name", "") if decomp_summary.source_summaries else "",
            key_topics=decomp_summary.key_topics[:20],
            fact_type_counts=decomp_summary.fact_type_counts,
        )

        async with worker_state.session_factory() as session:
            repo = ConversationRepository(session)
            await repo.update_message(
                msg_uuid,
                status="completed",
                content=f"Extracted {len(proposed_nodes)} proposed nodes from {decomp_summary.total_facts} facts.",
                metadata_json=output.model_dump(),
            )
            await session.commit()

        await emit("pipeline_scope_end", {"scope_id": "ingest-filtering"})

    except Exception as e:
        logger.exception("Ingest decompose failed: conv=%s", input.conversation_id)
        async with worker_state.session_factory() as session:
            repo = ConversationRepository(session)
            await repo.update_message(msg_uuid, status="failed", error=str(e))
            await session.commit()
        await emit("phase_change", {"phase": "completed"})
        await emit("done", {})
        raise

    await emit("phase_change", {"phase": "completed"})
    await emit("done", {})

    ctx.log(f"Ingest decompose complete: {len(proposed_nodes)} proposals")
    return {}


# ══════════════════════════════════════════════════════════════
# Phased document ingest — build workflow (Phase 2)
# ══════════════════════════════════════════════════════════════

ingest_build_wf = hatchet.workflow(
    name="ingest_build",
    input_validator=IngestBuildInput,
)


@ingest_build_wf.durable_task(execution_timeout=timedelta(hours=6), schedule_timeout=_schedule_timeout)
async def handle_build(input: IngestBuildInput, ctx: DurableContext) -> dict:
    """Phase 2: Build user-confirmed nodes from document ingest.

    Follows the same pattern as bottom_up_build_wf.
    """
    from kt_models.usage import start_usage_tracking
    from kt_hatchet.usage_helpers import flush_usage_to_db
    from kt_hatchet.models import BuildNodeInput
    from kt_hatchet.scope_planner import resolve_perspective_source_ids

    state = cast(WorkerState, ctx.lifespan)
    start_usage_tracking()

    async def emit(event_type: str, payload: dict) -> None:
        try:
            await ctx.aio_put_stream(json.dumps({"type": event_type, **payload}))
        except Exception:
            logger.warning("Failed to stream event %s", event_type, exc_info=True)

    msg_uuid = uuid.UUID(input.message_id)
    conv_uuid = uuid.UUID(input.conversation_id)

    # Mark as running
    async with state.session_factory() as session:
        from kt_db.repositories.conversations import ConversationRepository
        repo = ConversationRepository(session)
        await repo.update_message(msg_uuid, status="running")
        await session.commit()

    ctx.log(f"Starting ingest build (Phase 2): {len(input.selected_nodes)} nodes")

    await emit("pipeline_scope_start", {
        "scope_id": "build",
        "scope_name": f"Building {len(input.selected_nodes)} nodes",
        "task_run_id": ctx.step_run_id,
        "mode": "ingest_build",
    })

    try:
        # ── Phase: Create nodes via node_pipeline_wf ─────────────
        from hatchet_sdk import TriggerWorkflowOptions
        from kt_worker_nodes.workflows.node_pipeline import node_pipeline_wf

        await emit("pipeline_phase", {
            "scope_id": "build",
            "phase": "creating",
            "event": "start",
        })

        node_meta = TriggerWorkflowOptions(additional_metadata={
            "message_id": input.message_id,
            "conversation_id": input.conversation_id,
        })

        from kt_db.keys import make_seed_key as _make_seed_key

        bulk_items = []
        for node in input.selected_nodes:
            sk = node.seed_key or _make_seed_key(node.node_type, node.name)
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
                built_nodes.append({
                    "node_id": node_id,
                    "concept": create_data.get("concept", ""),
                    "node_type": create_data.get("node_type", "concept"),
                })
            created_edge_ids.extend(dim_data.get("edge_ids", []))

        await emit("pipeline_phase", {
            "scope_id": "build",
            "phase": "creating",
            "event": "end",
        })

        ctx.log(f"Created {len(created_node_ids)} nodes, {len(created_edge_ids)} edges")

        if created_node_ids:
            await emit("graph_update", {
                "node_ids": created_node_ids,
                "edge_ids": created_edge_ids,
                "wave": 0,
            })

        # ── Phase: Build perspectives ────────────────────────────
        perspective_plans: list[dict[str, Any]] = []
        for node in input.selected_nodes:
            if node.perspectives:
                for persp in node.perspectives:
                    perspective_plans.append({
                        "claim": persp.claim,
                        "antithesis": persp.antithesis,
                        "source_concept_id": node.name,
                    })

        perspective_node_count = 0
        if perspective_plans and built_nodes:
            perspective_plans = resolve_perspective_source_ids(perspective_plans, built_nodes)

            await emit("pipeline_phase", {
                "scope_id": "build",
                "phase": "perspectives",
                "event": "start",
            })

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

            await emit("pipeline_phase", {
                "scope_id": "build",
                "phase": "perspectives",
                "event": "end",
            })

            ctx.log(f"Built {perspective_node_count} perspective nodes")

        # ── Flush usage to DB (self-reporting) ─────────────────────
        await flush_usage_to_db(
            state.write_session_factory,
            input.conversation_id,
            input.message_id,
            "ingest_build",
        )

        # ── Persist research report ─────────────────────────────────
        try:
            from kt_db.repositories.research_reports import ResearchReportRepository

            async with state.session_factory() as session:
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
                )
                await session.commit()
        except Exception:
            logger.warning("Failed to persist research report", exc_info=True)

        # ── Update message with results ──────────────────────────
        async with state.session_factory() as session:
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

        await emit("pipeline_scope_end", {
            "scope_id": "build",
            "node_count": len(created_node_ids),
        })

    except Exception as e:
        logger.exception("Ingest build failed: conv=%s", input.conversation_id)
        async with state.session_factory() as session:
            from kt_db.repositories.conversations import ConversationRepository
            repo = ConversationRepository(session)
            await repo.update_message(msg_uuid, status="failed", error=str(e))
            await session.commit()
        await emit("phase_change", {"phase": "completed"})
        await emit("done", {})
        raise

    await emit("done", {
        "created_node_ids": created_node_ids,
        "created_edge_ids": created_edge_ids,
        "phase": "build",
    })

    ctx.log(
        f"Ingest build complete: {len(created_node_ids)} nodes, "
        f"{len(created_edge_ids)} edges"
    )

    return {}
