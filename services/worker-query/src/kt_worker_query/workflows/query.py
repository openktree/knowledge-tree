"""Lightweight query workflow — graph navigation + synthesis without exploration.

Uses the QueryWorker (read-only graph navigation via QueryAgent) to answer
questions from existing knowledge graph data.  No new nodes are created,
no external API calls are made — just DB reads and LLM synthesis.

Dispatched when a conversation is created in ``query`` mode, providing a
fast, lightweight alternative to the full ``exploration_wf``.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import timedelta
from typing import cast

from hatchet_sdk import DurableContext

from kt_config.settings import get_settings
from kt_hatchet.client import get_hatchet
from kt_hatchet.lifespan import WorkerState
from kt_hatchet.models import QueryInput

logger = logging.getLogger(__name__)

hatchet = get_hatchet()
_schedule_timeout = timedelta(minutes=get_settings().hatchet_schedule_timeout_minutes)

query_wf = hatchet.workflow(
    name="query",
    input_validator=QueryInput,
)


@query_wf.durable_task(execution_timeout=timedelta(minutes=10), schedule_timeout=_schedule_timeout)
async def handle_query(input: QueryInput, ctx: DurableContext) -> dict:
    """Run lightweight query agent: navigate graph + synthesize answer."""
    worker_state = cast(WorkerState, ctx.lifespan)

    async def emit(event_type: str, payload: dict) -> None:
        try:
            await ctx.aio_put_stream(json.dumps({"type": event_type, **payload}))
        except Exception:
            logger.warning("Failed to stream event %s", event_type, exc_info=True)

    from kt_db.repositories.conversations import ConversationRepository
    from kt_worker_conv.workflows.conversations import (
        _build_agent_context,
        _make_emit_callback,
    )
    from kt_worker_query.agents.query_worker import QueryWorker

    ctx.log(f"Query starting: conv={input.conversation_id}")

    msg_uuid = uuid.UUID(input.message_id)

    # Mark as running
    async with worker_state.session_factory() as session:
        repo = ConversationRepository(session)
        await repo.update_message(msg_uuid, status="running")
        await session.commit()

    await emit("phase_change", {"phase": "running"})

    try:
        async with worker_state.session_factory() as session:
            emit_cb = _make_emit_callback(emit)
            agent_ctx = await _build_agent_context(
                worker_state,
                session,
                emit_event=emit_cb,
                api_key=input.api_key,
            )

            result = await QueryWorker(agent_ctx).run(
                input.query,
                input.nav_budget,
            )

            await session.commit()

        # Persist result
        async with worker_state.session_factory() as session:
            repo = ConversationRepository(session)
            await repo.update_message(
                msg_uuid,
                status="completed",
                content=result.answer or "",
                nav_used=result.nav_used,
                explore_used=0,
                visited_nodes=result.visited_nodes,
                created_nodes=[],
                created_edges=[],
                subgraph=result.subgraph,
            )
            await session.commit()

    except Exception as e:
        logger.exception("Query failed: conv=%s", input.conversation_id)
        async with worker_state.session_factory() as session:
            repo = ConversationRepository(session)
            await repo.update_message(msg_uuid, status="failed", error=str(e))
            await session.commit()
        await emit("phase_change", {"phase": "completed"})
        await emit("done", {})
        raise

    await emit("phase_change", {"phase": "completed"})
    await emit("done", {})

    ctx.log(f"Query complete: conv={input.conversation_id}")
    return {}
