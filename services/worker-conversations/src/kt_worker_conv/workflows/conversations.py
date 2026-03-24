"""Conversation workflows: follow-up and re-synthesis.

These workflows port the business logic from the Redis Streams workers:
- ``FollowUpStreamWorker``   -> ``follow_up_wf``
- ``ResynthesizeStreamWorker`` -> ``resynthesize_task``

Each task builds an AgentContext from WorkerState, delegates to the existing
worker classes (IngestWorker, QueryWorker), persists
results to ConversationMessage, and streams progress events via
``ctx.aio_put_stream`` so the frontend pipeline view stays intact.
"""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import timedelta
from typing import Any, cast

from hatchet_sdk import Context, DurableContext

from kt_config.settings import get_settings
from kt_hatchet.client import get_hatchet
from kt_hatchet.lifespan import WorkerState
from kt_hatchet.models import (
    FollowUpInput,
    ResynthesizeInput,
)

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
    (IngestWorker, QueryWorker, OrchestratorWorker) can emit progress events
    through ``ctx.emit()``.

    ``write_session`` is required for any task that writes facts, nodes,
    edges, or dimensions.

    When ``api_key`` is provided (BYOK), per-request ModelGateway and
    EmbeddingService are created instead of using the shared worker instances.
    """
    from kt_agents_core.state import AgentContext
    from kt_graph.engine import GraphEngine

    if api_key:
        from kt_models.embeddings import EmbeddingService
        from kt_models.gateway import ModelGateway

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


# ======================================================================
# Follow-up workflow — conversation turn routing
# ======================================================================

follow_up_wf = hatchet.workflow(
    name="follow_up",
    input_validator=FollowUpInput,
)


@follow_up_wf.durable_task(execution_timeout=timedelta(minutes=60), schedule_timeout=_schedule_timeout)
async def handle_follow_up(input: FollowUpInput, ctx: DurableContext) -> dict:
    """Handle a follow-up conversation turn.

    Routes by mode:
    - ``ingest`` -> IngestWorker.run_expansion()
    - everything else -> QueryWorker.run()
    """
    worker_state = cast(WorkerState, ctx.lifespan)

    async def emit(event_type: str, payload: dict) -> None:
        try:
            await ctx.aio_put_stream(json.dumps({"type": event_type, **payload}))
        except Exception:
            logger.warning("Failed to stream event %s", event_type, exc_info=True)

    from kt_db.repositories.conversations import ConversationRepository
    from kt_worker_ingest.agents.ingest_worker import IngestWorker
    from kt_worker_query.agents.query_worker import QueryWorker

    ctx.log(f"Follow-up starting: mode={input.mode}, conv={input.conversation_id}")

    msg_uuid = uuid.UUID(input.message_id)
    conv_uuid = uuid.UUID(input.conversation_id)

    async with worker_state.session_factory() as session:
        repo = ConversationRepository(session)

        # Load prior context from DB
        prior_answer = ""
        prior_visited: list[str] = []
        messages = await repo.get_messages(conv_uuid)
        for m in reversed(messages):
            if m.role == "assistant" and m.id != msg_uuid and m.status == "completed":
                prior_answer = m.content or ""
                prior_visited = list(m.visited_nodes or [])
                break

        # Mark as running
        await repo.update_message(msg_uuid, status="running")
        await session.commit()

    await emit("phase_change", {"phase": "running"})
    ctx.refresh_timeout("30m")

    try:
        emit_cb = _make_emit_callback(emit)
        async with _open_sessions(worker_state) as (session, write_session):
            agent_ctx = await _build_agent_context(
                worker_state,
                session,
                emit_event=emit_cb,
                write_session=write_session,
                api_key=input.api_key,
            )

            if input.mode == "ingest":
                result = await IngestWorker(agent_ctx).run_expansion(
                    input.conversation_id,
                    input.nav_budget,
                )
            else:
                result = await QueryWorker(agent_ctx).run(
                    input.follow_up_query,
                    input.nav_budget,
                    original_query=input.original_query,
                    prior_answer=prior_answer,
                    prior_visited_nodes=prior_visited,
                )

            await write_session.commit()
            await session.commit()

        # Persist result in a separate session
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

    except Exception as e:
        logger.exception("Follow-up turn failed: conv=%s", input.conversation_id)
        async with worker_state.session_factory() as session:
            repo = ConversationRepository(session)
            await repo.update_message(msg_uuid, status="failed", error=str(e))
            await session.commit()
        await emit("phase_change", {"phase": "completed"})
        await emit("done", {})
        raise

    await emit("phase_change", {"phase": "completed"})
    await emit("done", {})

    ctx.log(f"Follow-up complete: conv={input.conversation_id}, mode={input.mode}")
    return {}


# ======================================================================
# Resynthesize task — standalone (not durable, short-running)
# ======================================================================


@hatchet.task(
    name="resynthesize",
    input_validator=ResynthesizeInput,
    execution_timeout=timedelta(minutes=10),
    schedule_timeout=_schedule_timeout,
)
async def resynthesize_task(input: ResynthesizeInput, ctx: Context) -> dict:
    """Re-run synthesis on an existing message without re-exploring.

    Loads visited_nodes from the ConversationMessage, builds a minimal
    PipelineState with zero budgets, and calls synthesize_answer_impl.
    Only the message content and status are updated.
    """
    worker_state = cast(WorkerState, ctx.lifespan)

    async def emit(event_type: str, payload: dict) -> None:
        try:
            await ctx.aio_put_stream(json.dumps({"type": event_type, **payload}))
        except Exception:
            logger.warning("Failed to stream event %s", event_type, exc_info=True)

    from kt_agents_core.state import PipelineState
    from kt_agents_core.synthesis import synthesize_answer_impl
    from kt_db.repositories.conversations import ConversationRepository

    ctx.log(f"Resynthesize starting: conv={input.conversation_id}, msg={input.message_id}")

    msg_uuid = uuid.UUID(input.message_id)

    # Load visited_nodes from the message record
    async with worker_state.session_factory() as session:
        repo = ConversationRepository(session)
        msg = await repo.get_message(msg_uuid)
        if msg is None:
            raise ValueError(f"Message {input.message_id} not found")
        visited_nodes = list(msg.visited_nodes or [])

    await emit("phase_change", {"phase": "running"})

    try:
        async with _open_sessions(worker_state) as (session, write_session):
            agent_ctx = await _build_agent_context(
                worker_state, session, write_session=write_session, api_key=input.api_key
            )

            # Build minimal state with original visited nodes and zero budgets
            orch_state = PipelineState(
                query=input.query,
                nav_budget=0,
                explore_budget=0,
                visited_nodes=visited_nodes,
            )

            result = await synthesize_answer_impl(agent_ctx, orch_state)

        # Update only content and status — preserve all other fields
        async with worker_state.session_factory() as session:
            repo = ConversationRepository(session)
            await repo.update_message(
                msg_uuid,
                status="completed",
                content=result.get("answer", "") or "",
            )
            await session.commit()

    except Exception as e:
        logger.exception("Re-synthesis failed: conv=%s", input.conversation_id)
        async with worker_state.session_factory() as session:
            repo = ConversationRepository(session)
            await repo.update_message(msg_uuid, status="failed", error=str(e))
            await session.commit()
        await emit("phase_change", {"phase": "completed"})
        await emit("done", {})
        raise

    await emit("phase_change", {"phase": "completed"})
    await emit("done", {})

    ctx.log(f"Resynthesize complete: conv={input.conversation_id}")
    return {}
