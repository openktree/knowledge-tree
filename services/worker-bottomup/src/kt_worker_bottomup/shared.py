"""Shared helpers used by orchestrator workflows (bottom-up, etc.).

Extracted from exploration.py to decouple from the top-down orchestrator.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any

from kt_hatchet.lifespan import WorkerState

logger = logging.getLogger(__name__)


@asynccontextmanager
async def _open_sessions(state: WorkerState) -> AsyncGenerator[tuple[Any, Any], None]:
    """Open write-db session for worker pipelines.

    Yields ``(session, write_session)`` where ``session`` is **None**.
    Workers operate in write-db-only mode — all reads route through
    write-db or Qdrant, and the sync worker propagates to graph-db.

    Previously this opened a graph-db session that was held for the
    entire pipeline (hours during bottom-up research), leaking
    connections from the limited graph-db pool (max_connections=100)
    and starving the API / wiki-frontend.
    """
    write_session = None
    if state.write_session_factory is not None:
        write_session = state.write_session_factory()
    try:
        yield None, write_session
    finally:
        if write_session is not None:
            await write_session.close()


async def _build_agent_context(
    state: WorkerState,
    session: Any | None = None,
    emit_event: Any | None = None,
    write_session: Any | None = None,
    api_key: str | None = None,
) -> Any:
    """Build an AgentContext from WorkerState.

    ``session`` (graph-db) defaults to None — workers operate in
    write-db-only mode.  GraphEngine methods that have write-db
    fallbacks will use write-db; methods that require graph-db
    (e.g. search_nodes) will raise RuntimeError, which callers
    handle gracefully (e.g. scout wraps graph reads in try/except).

    Pass ``emit_event`` to wire AgentContext.emit() calls (e.g. from
    PerspectiveBuilder) to the Hatchet stream.  The callback must have
    signature ``async def(event_type: str, **kwargs) -> None``.

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
        session_factory=state.session_factory,
        content_fetcher=state.content_fetcher,
        emit_event=emit_event,
        write_session_factory=state.write_session_factory,
        qdrant_client=state.qdrant_client,
    )


_WAVE_PLAN_MAX_RETRIES = 3
_WAVE_PLAN_RETRY_DELAY = 2.0  # seconds, doubles each attempt


async def _plan_wave(
    query: str,
    wave: int,
    total_waves: int,
    briefings: list[Any],
    wave_explore: int,
    wave_nav: int,
    scout_results: dict[str, Any],
    agent_ctx: Any,
) -> list[Any]:
    """Plan scopes for a wave using an LLM call.

    Returns a list of ``ScopePlan`` dataclasses.  Retries on empty/invalid
    responses up to ``_WAVE_PLAN_MAX_RETRIES`` times, then raises so the
    workflow fails cleanly rather than proceeding with a degraded single-scope
    fallback.
    """
    from kt_worker_bottomup.bottom_up.wave_planner import (
        MIN_UTILIZATION,
        SUBDIVISION_THRESHOLD,
        WAVE_PLANNER_PROMPT,
        WavePlanParseError,
        build_wave_planner_user_msg,
        parse_scope_plans,
        subdivide_large_scopes_with_llm,
        subdivide_scopes,
    )

    base_user_msg = build_wave_planner_user_msg(
        query,
        wave,
        total_waves,
        wave_explore,
        wave_nav,
        briefings,
        scout_results,
    )

    last_exc: Exception | None = None
    user_msg = base_user_msg
    for attempt in range(_WAVE_PLAN_MAX_RETRIES):
        try:
            response = await agent_ctx.model_gateway.generate(
                messages=[
                    {"role": "system", "content": WAVE_PLANNER_PROMPT},
                    {"role": "user", "content": user_msg},
                ],
                model_id=agent_ctx.model_gateway.orchestrator_model,
                reasoning_effort=agent_ctx.model_gateway.orchestrator_thinking_level or None,
            )
            raw_text = response if isinstance(response, str) else str(response)

            if not raw_text.strip():
                logger.warning(
                    "Wave planner returned empty response (attempt %d/%d)",
                    attempt + 1,
                    _WAVE_PLAN_MAX_RETRIES,
                )
                last_exc = WavePlanParseError("Empty response from wave planner LLM")
                delay = _WAVE_PLAN_RETRY_DELAY * (2**attempt)
                await asyncio.sleep(delay)
                continue

            scopes = parse_scope_plans(raw_text, wave_explore, wave_nav)

            # Check budget utilization — retry with hint if too low
            total_planned = sum(s.explore_budget for s in scopes)
            utilization = total_planned / wave_explore if wave_explore > 0 else 1.0

            if utilization < MIN_UTILIZATION and attempt < _WAVE_PLAN_MAX_RETRIES - 1:
                logger.info(
                    "Wave planner used only %d/%d explore (%.0f%%), retrying with hint",
                    total_planned,
                    wave_explore,
                    utilization * 100,
                )
                hint = (
                    f"\n\nIMPORTANT: Your previous plan only used {total_planned} "
                    f"out of {wave_explore} explore budget ({utilization:.0%}). "
                    f"You MUST plan more scopes to use at least "
                    f"{int(wave_explore * MIN_UTILIZATION)} explore budget. "
                    f"Plan approximately {wave_explore // 4} scopes with 3-5 "
                    f"explore each."
                )
                user_msg = base_user_msg + hint
                delay = _WAVE_PLAN_RETRY_DELAY * (2**attempt)
                await asyncio.sleep(delay)
                continue

            # Apply subdivision: LLM-based for large scopes, mechanical for the rest
            if any(s.explore_budget > SUBDIVISION_THRESHOLD for s in scopes):
                scopes = await subdivide_large_scopes_with_llm(
                    scopes,
                    wave_explore,
                    wave_nav,
                    agent_ctx,
                )
            else:
                scopes = subdivide_scopes(scopes, wave_explore, wave_nav)

            return scopes

        except WavePlanParseError as exc:
            last_exc = exc
            logger.warning(
                "Wave planner parse failed (attempt %d/%d): %s",
                attempt + 1,
                _WAVE_PLAN_MAX_RETRIES,
                exc,
            )
            delay = _WAVE_PLAN_RETRY_DELAY * (2**attempt)
            await asyncio.sleep(delay)
        except Exception as exc:
            last_exc = exc
            logger.warning(
                "Wave planner LLM call failed (attempt %d/%d): %s",
                attempt + 1,
                _WAVE_PLAN_MAX_RETRIES,
                exc,
            )
            delay = _WAVE_PLAN_RETRY_DELAY * (2**attempt)
            await asyncio.sleep(delay)

    # All retries exhausted — fail the workflow
    raise RuntimeError(f"Wave planner failed after {_WAVE_PLAN_MAX_RETRIES} attempts: {last_exc}") from last_exc
