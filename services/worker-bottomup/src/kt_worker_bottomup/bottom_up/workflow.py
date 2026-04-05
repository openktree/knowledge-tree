"""Bottom-up exploration workflows: orchestrator and scope.

The **bottom_up_wf** is the top-level durable orchestrator. When explore_budget > 5,
it uses waves with sub-explorer scopes (reusing the wave planning infrastructure).
When <= 5, it runs a single scope.

The **bottom_up_scope_wf** handles a single scope:
1. Gather facts with extraction + filter noise nodes (single LLM call)
2. Fan out node builds for all filtered nodes
3. Plan perspectives via single LLM call (after nodes exist)
4. Build perspective pairs via PerspectiveBuilder
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import timedelta
from typing import Any, cast

from hatchet_sdk import (
    ConcurrencyExpression,
    ConcurrencyLimitStrategy,
    DurableContext,
    TriggerWorkflowOptions,
)

from kt_config.settings import get_settings
from kt_hatchet.client import get_hatchet
from kt_hatchet.lifespan import WorkerState
from kt_hatchet.models import (
    AgentSelectInput,
    AgentSelectOutput,
    BottomUpInput,
    BottomUpPrepareInput,
    BottomUpPrepareScopeInput,
    BottomUpPrepareScopeOutput,
    BottomUpScopeInput,
    BottomUpScopeOutput,
    BuildNodeInput,
)
from kt_worker_bottomup.shared import _build_agent_context, _open_sessions

logger = logging.getLogger(__name__)

hatchet = get_hatchet()
_schedule_timeout = timedelta(minutes=get_settings().hatchet_schedule_timeout_minutes)


# ══════════════════════════════════════════════════════════════
# Bottom-up scope workflow — one per scope
# ══════════════════════════════════════════════════════════════

bottom_up_scope_wf = hatchet.workflow(
    name="bottom_up_scope",
    input_validator=BottomUpScopeInput,
)


@bottom_up_scope_wf.durable_task(execution_timeout=timedelta(hours=2), schedule_timeout=_schedule_timeout)
async def bottom_up_scope(input: BottomUpScopeInput, ctx: DurableContext) -> dict:
    """Run a bottom-up scoped exploration: gather → filter → build → perspectives.

    Phase 1: Gather facts with extraction, filter noise via single LLM call.
    Phase 2: Fan out node_pipeline_wf for each filtered node (parallel builds).
    Phase 3: Plan perspectives via single LLM call, then build them.

    Returns a BottomUpScopeOutput dict with created node/edge IDs and budget usage.
    """
    from kt_hatchet.usage_helpers import flush_usage_to_db
    from kt_models.usage import start_usage_tracking
    from kt_worker_bottomup.bottom_up.scope import (
        plan_and_store_perspective_seeds,
        run_bottom_up_scope_pipeline,
    )

    state = cast(WorkerState, ctx.lifespan)
    start_usage_tracking()

    async def emit(event_type: str, payload: dict) -> None:
        try:
            await ctx.aio_put_stream(json.dumps({"type": event_type, **payload}))
        except Exception:
            logger.warning("Failed to stream event %s", event_type, exc_info=True)

    ctx.log(f"Bottom-up scope starting: '{input.scope_description}'")

    await emit(
        "pipeline_scope_start",
        {
            "scope_id": input.scope_id,
            "scope_name": input.scope_description,
            "wave_number": input.wave_number,
            "task_run_id": ctx.step_run_id,
            "mode": "bottom_up",
        },
    )

    # ── Phase 1: Gather facts + extract + filter ─────────────────────────

    await emit(
        "pipeline_phase",
        {
            "scope_id": input.scope_id,
            "phase": "gathering",
            "event": "start",
        },
    )

    async with _open_sessions(state) as (session, write_session):
        agent_ctx = await _build_agent_context(state, session, write_session=write_session, user_id=input.user_id)
        plan = await run_bottom_up_scope_pipeline(
            agent_ctx,
            scope_description=input.scope_description,
            explore_slice=input.explore_slice,
            message_id=input.message_id,
            conversation_id=input.conversation_id,
        )
        if write_session is not None:
            await write_session.commit()

    await emit(
        "pipeline_phase",
        {
            "scope_id": input.scope_id,
            "phase": "gathering",
            "event": "end",
        },
    )

    ctx.refresh_timeout("30m")

    if not plan.node_plans:
        ctx.log(f"Bottom-up scope {input.scope_id}: no nodes after filtering")
        await emit("pipeline_scope_end", {"scope_id": input.scope_id, "node_count": 0})
        await flush_usage_to_db(
            state.write_session_factory,
            input.conversation_id,
            input.message_id,
            "scope_exploration",
        )
        return BottomUpScopeOutput(
            briefing=f"Scope '{input.scope_description}': no nodes after filtering.",
        ).model_dump()

    # Fail if fact gathering produced nothing
    if plan.gathered_fact_count == 0 and plan.explore_used == 0:
        msg = (
            f"Scope '{input.scope_description}': fact gathering failed — "
            f"0 facts stored (possible model errors). Budget refunded."
        )
        ctx.log(msg)
        await emit(
            "pipeline_scope_end",
            {
                "scope_id": input.scope_id,
                "node_count": 0,
                "error": msg,
            },
        )
        raise RuntimeError(msg)

    ctx.log(
        f"Bottom-up scope {input.scope_id}: "
        f"building {len(plan.node_plans)} nodes "
        f"(extracted={plan.extracted_count}, explore_used={plan.explore_used}/{input.explore_slice}): "
        f"{[e['name'] for e in plan.node_plans[:10]]}"
    )

    # ── Phase 2: Fan out node pipeline workflows ──────────────────────────

    from kt_worker_nodes.workflows.node_pipeline import node_pipeline_wf

    node_meta = TriggerWorkflowOptions(
        additional_metadata={
            "message_id": input.message_id,
            "conversation_id": input.conversation_id,
        }
    )
    from kt_db.keys import make_seed_key

    bulk_items = []
    for entry in plan.node_plans:
        sk = entry.get("seed_key") or make_seed_key(entry.get("node_type", "concept"), entry["name"])
        bulk_items.append(
            node_pipeline_wf.create_bulk_run_item(
                input=BuildNodeInput(
                    scope_id=input.scope_id,
                    concept=entry["name"],
                    node_type=entry.get("node_type", "concept"),
                    seed_key=sk,
                    message_id=input.message_id,
                    conversation_id=input.conversation_id,
                    user_id=input.user_id,
                ),
                options=node_meta,
            )
        )

    results = await node_pipeline_wf.aio_run_many(bulk_items)

    ctx.refresh_timeout("30m")

    # ── Collect results from node pipeline ────────────────────────────────

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

    explore_used = plan.explore_used
    nav_used = len(created_node_ids)

    # ── Phase 3: Save perspective seeds (lightweight) ──────────────────────

    perspective_seed_count = 0

    # Plan perspectives via single LLM call, save as seeds instead of building
    max_perspectives = max(1, len(built_nodes) // 10)

    if built_nodes:
        async with _open_sessions(state) as (session, write_session):
            persp_ctx = await _build_agent_context(
                state,
                session,
                write_session=write_session,
                user_id=input.user_id,
            )
            thesis_keys = await plan_and_store_perspective_seeds(
                persp_ctx,
                scope_description=input.scope_description,
                built_nodes=built_nodes,
                content_summary=plan.content_summary,
                max_perspectives=max_perspectives,
            )
            perspective_seed_count = len(thesis_keys)
            if thesis_keys:
                await write_session.commit()

    if perspective_seed_count:
        await emit(
            "perspective_seeds_created",
            {
                "scope_id": input.scope_id,
                "count": perspective_seed_count,
            },
        )

        ctx.log(f"Bottom-up scope {input.scope_id}: saved {perspective_seed_count} perspective seed pairs")

    nav_used = len(created_node_ids)

    # ── Build briefing ────────────────────────────────────────────────────

    node_names = [e["name"] for e in plan.node_plans[:5]]
    briefing = (
        f"Scope '{input.scope_description}': "
        f"extracted {plan.extracted_count} nodes, "
        f"filtered to {len(plan.node_plans)} for building, "
        f"built {len(created_node_ids)} nodes, "
        f"{len(created_edge_ids)} edges"
        f"{f', {perspective_seed_count} perspective seeds' if perspective_seed_count else ''}. "
        f"Nodes: {', '.join(node_names)}{'...' if len(plan.node_plans) > 5 else ''}. "
        f"Budget: {explore_used}/{input.explore_slice} explore, "
        f"{nav_used}/{input.nav_slice} nav used."
    )

    if created_node_ids:
        await emit(
            "graph_update",
            {
                "node_ids": created_node_ids,
                "edge_ids": created_edge_ids,
                "wave": input.wave_number,
            },
        )

    await emit(
        "pipeline_scope_end",
        {
            "scope_id": input.scope_id,
            "node_count": len(created_node_ids),
            "mode": "bottom_up",
        },
    )

    ctx.log(
        f"Bottom-up scope {input.scope_id} complete: "
        f"{len(created_node_ids)} nodes, "
        f"{len(created_edge_ids)} edges, "
        f"{perspective_seed_count} perspective seeds"
    )

    await flush_usage_to_db(
        state.write_session_factory,
        input.conversation_id,
        input.message_id,
        "scope_exploration",
    )
    return BottomUpScopeOutput(
        created_node_ids=created_node_ids,
        created_edge_ids=created_edge_ids,
        explore_used=explore_used,
        nav_used=nav_used,
        briefing=briefing,
        node_count=len(created_node_ids),
        extracted_count=plan.extracted_count,
        super_sources=plan.super_sources,
    ).model_dump()


# ══════════════════════════════════════════════════════════════
# Bottom-up orchestrator — top-level workflow
# ══════════════════════════════════════════════════════════════

# Threshold: above this, use waves; at or below, single scope
_WAVE_THRESHOLD = 5

bottom_up_wf = hatchet.workflow(
    name="bottom_up",
    input_validator=BottomUpInput,
    concurrency=ConcurrencyExpression(
        expression="input.conversation_id",
        max_runs=get_settings().bottom_up_max_runs,
        limit_strategy=ConcurrencyLimitStrategy.GROUP_ROUND_ROBIN,
    ),
)


@bottom_up_wf.durable_task(execution_timeout=timedelta(hours=12), schedule_timeout=_schedule_timeout)
async def bottom_up_orchestrate(input: BottomUpInput, ctx: DurableContext) -> dict:
    """Run bottom-up exploration: wave-based when explore > 5, single scope otherwise.

    Uses the same wave planning infrastructure as the standard exploration_wf,
    but dispatches bottom_up_scope_wf instead of sub_explore_wf.
    """
    from kt_hatchet.usage_helpers import flush_usage_to_db
    from kt_models.usage import start_usage_tracking

    state = cast(WorkerState, ctx.lifespan)
    start_usage_tracking()

    async def emit(event_type: str, payload: dict) -> None:
        try:
            await ctx.aio_put_stream(json.dumps({"type": event_type, **payload}))
        except Exception:
            logger.warning("Failed to stream event %s", event_type, exc_info=True)

    from kt_worker_bottomup.bottom_up.scout import scout_impl
    from kt_worker_bottomup.bottom_up.state import (
        WaveAccumulator,
    )

    ctx.log("Starting bottom-up exploration workflow")

    await emit(
        "pipeline_scope_start",
        {
            "scope_id": "orchestrator",
            "scope_name": f"Bottom-up: {input.query}",
            "task_run_id": ctx.step_run_id,
        },
    )

    # ── Scout phase ───────────────────────────────────────────────────

    await emit(
        "pipeline_phase",
        {
            "scope_id": "orchestrator",
            "phase": "scout",
            "event": "start",
        },
    )

    scout_queries = [input.query, f"overview of {input.query}"]
    scout_results: dict[str, Any] = {}
    async with _open_sessions(state) as (session, write_session):
        agent_ctx = await _build_agent_context(state, session, write_session=write_session, user_id=input.user_id)
        try:
            scout_results = await scout_impl(scout_queries, agent_ctx)
        except Exception:
            logger.exception("Scout phase failed")

    await emit(
        "pipeline_phase",
        {
            "scope_id": "orchestrator",
            "phase": "scout",
            "event": "end",
        },
    )

    ctx.log(f"Scout complete: {sum(len(v.get('graph_matches', [])) for v in scout_results.values())} graph matches")

    # ── Decide: waves or single scope ─────────────────────────────────

    accumulator = WaveAccumulator(
        query=input.query,
        nav_budget=input.nav_budget,
        explore_budget=input.explore_budget,
    )

    if input.explore_budget > _WAVE_THRESHOLD:
        # Wave-based exploration
        waves_completed = await _run_waves(
            input,
            ctx,
            state,
            accumulator,
            scout_results,
            emit,
        )
    else:
        # Single scope
        waves_completed = await _run_single_scope(
            input,
            ctx,
            state,
            accumulator,
            scout_results,
            emit,
        )

    await emit(
        "pipeline_scope_end",
        {
            "scope_id": "orchestrator",
            "node_count": len(accumulator.created_nodes),
        },
    )

    # ── Persist research report ───────────────────────────────────────

    scope_summaries = [b.summary for b in accumulator.briefings if b.summary]

    await flush_usage_to_db(state.write_session_factory, input.conversation_id, input.message_id, "orchestrator")

    try:
        from kt_db.repositories.research_reports import ResearchReportRepository

        async with state.session_factory() as session:
            await ResearchReportRepository(session).create(
                message_id=uuid.UUID(input.message_id),
                conversation_id=uuid.UUID(input.conversation_id),
                nodes_created=len(accumulator.created_nodes),
                edges_created=len(accumulator.created_edges),
                waves_completed=waves_completed,
                explore_budget=input.explore_budget,
                explore_used=accumulator.explore_used,
                nav_budget=input.nav_budget,
                nav_used=accumulator.nav_used,
                scope_summaries=scope_summaries if scope_summaries else None,
                total_prompt_tokens=0,
                total_completion_tokens=0,
                total_cost_usd=0.0,
                usage_by_model=None,
                usage_by_task=None,
                super_sources=accumulator.super_sources if accumulator.super_sources else None,
                workflow_run_id=ctx.workflow_run_id,
                report_type="graph_builder",
            )

    except Exception:
        logger.warning(
            "Failed to persist research report for message %s",
            input.message_id,
            exc_info=True,
        )

    # ── Fire-and-forget: promote seeds to stub nodes ─────────────────
    try:
        from kt_hatchet.models import AutoBuildInput
        from kt_worker_nodes.workflows.auto_build import auto_build_task

        await auto_build_task.aio_run_no_wait(AutoBuildInput())
        ctx.log("Dispatched auto_build_graph to promote accumulated seeds")
    except Exception:
        logger.warning("Failed to dispatch auto_build_graph", exc_info=True)

    await emit(
        "done",
        {
            "created_node_ids": accumulator.created_nodes,
            "created_edge_ids": accumulator.created_edges,
            "explore_used": accumulator.explore_used,
            "nav_used": accumulator.nav_used,
        },
    )

    ctx.log(
        f"Bottom-up exploration complete: "
        f"{len(accumulator.created_nodes)} nodes, "
        f"{len(accumulator.created_edges)} edges"
    )

    return {}


# ── Internal helpers ──────────────────────────────────────────────────


async def _run_waves(
    input: BottomUpInput,
    ctx: DurableContext,
    state: WorkerState,
    accumulator: Any,
    scout_results: dict[str, Any],
    emit: Any,
) -> int:
    """Run wave-based exploration using the wave planner."""
    from kt_worker_bottomup.bottom_up.state import (
        ScopeBriefing,
        wave_budget_ratios,
    )
    from kt_worker_bottomup.shared import _plan_wave

    wave_count = state.settings.default_wave_count
    ratios = wave_budget_ratios(wave_count)
    waves_completed = 0

    for wave_idx, ratio in enumerate(ratios):
        ctx.refresh_timeout("30m")

        wave_num = wave_idx + 1
        wave_explore = max(1, int(input.explore_budget * ratio))
        wave_nav = max(1, int(input.nav_budget * ratio))
        wave_explore = min(wave_explore, accumulator.explore_remaining)
        wave_nav = min(wave_nav, accumulator.nav_remaining)

        if wave_explore <= 0 and wave_nav <= 0:
            ctx.log(f"Wave {wave_num}: budget exhausted, stopping")
            break

        ctx.log(f"Wave {wave_num}/{wave_count}: explore={wave_explore}, nav={wave_nav}")

        # Plan wave scopes via LLM
        await emit(
            "pipeline_phase",
            {
                "scope_id": "orchestrator",
                "phase": "planning",
                "event": "start",
                "wave": wave_num,
            },
        )

        async with _open_sessions(state) as (session, write_session):
            agent_ctx = await _build_agent_context(state, session, write_session=write_session, user_id=input.user_id)
            scopes = await _plan_wave(
                input.query,
                wave_num,
                wave_count,
                accumulator.briefings,
                wave_explore,
                wave_nav,
                scout_results,
                agent_ctx,
            )

        await emit(
            "pipeline_phase",
            {
                "scope_id": "orchestrator",
                "phase": "planning",
                "event": "end",
                "wave": wave_num,
            },
        )

        if not scopes:
            ctx.log(f"Wave {wave_num}: planner returned no scopes, skipping")
            continue

        ctx.log(f"Wave {wave_num}: planned {len(scopes)} scopes: {[s.scope for s in scopes]}")

        # Fan out bottom_up_scope_wf children
        child_meta = TriggerWorkflowOptions(
            additional_metadata={
                "message_id": input.message_id,
                "conversation_id": input.conversation_id,
            }
        )
        bulk_items = []
        for scope in scopes:
            scope_id = str(uuid.uuid4())
            bulk_items.append(
                bottom_up_scope_wf.create_bulk_run_item(
                    input=BottomUpScopeInput(
                        scope_id=scope_id,
                        scope_description=scope.scope,
                        explore_slice=scope.explore_budget,
                        nav_slice=scope.nav_budget,
                        wave_number=wave_num,
                        message_id=input.message_id,
                        conversation_id=input.conversation_id,
                        user_id=input.user_id,
                    ),
                    options=child_meta,
                )
            )

        try:
            results = await bottom_up_scope_wf.aio_run_many(bulk_items)
        except Exception:
            logger.exception(
                "Wave %d: aio_run_many failed (some bottom-up scopes may have errored)",
                wave_num,
            )
            results = []

        for result in results:
            task_data = result.get("bottom_up_scope", result) if isinstance(result, dict) else result
            sub_out = BottomUpScopeOutput.model_validate(task_data)

            briefing = ScopeBriefing(
                scope=sub_out.briefing or "completed",
                wave=wave_num,
                summary=sub_out.briefing,
                visited_nodes=sub_out.created_node_ids,
                created_nodes=sub_out.created_node_ids,
                created_edges=sub_out.created_edge_ids,
                nav_used=sub_out.nav_used,
                explore_used=sub_out.explore_used,
                super_sources=sub_out.super_sources,
            )
            accumulator.merge(briefing)

        await emit(
            "graph_update",
            {
                "node_ids": accumulator.created_nodes,
                "edge_ids": accumulator.created_edges,
                "wave": wave_num,
            },
        )

        await emit(
            "budget_update",
            {
                "explore_used": accumulator.explore_used,
                "explore_budget": input.explore_budget,
                "nav_used": accumulator.nav_used,
                "nav_budget": input.nav_budget,
            },
        )

        waves_completed += 1
        ctx.log(
            f"Wave {wave_num} complete: "
            f"nodes={len(accumulator.created_nodes)}, "
            f"edges={len(accumulator.created_edges)}, "
            f"explore_used={accumulator.explore_used}/{input.explore_budget}"
        )

    return waves_completed


async def _run_single_scope(
    input: BottomUpInput,
    ctx: DurableContext,
    state: WorkerState,
    accumulator: Any,
    scout_results: dict[str, Any],
    emit: Any,
) -> int:
    """Run a single bottom-up scope (no waves)."""
    from kt_worker_bottomup.bottom_up.state import ScopeBriefing

    scope_id = str(uuid.uuid4())

    scope_result = await bottom_up_scope_wf.aio_run(
        input=BottomUpScopeInput(
            scope_id=scope_id,
            scope_description=input.query,
            explore_slice=input.explore_budget,
            nav_slice=input.nav_budget,
            wave_number=0,
            message_id=input.message_id,
            conversation_id=input.conversation_id,
            user_id=input.user_id,
        ),
        options=TriggerWorkflowOptions(
            additional_metadata={
                "message_id": input.message_id,
                "conversation_id": input.conversation_id,
            },
        ),
    )

    ctx.refresh_timeout("30m")

    scope_output = scope_result.get("bottom_up_scope", {}) if isinstance(scope_result, dict) else {}
    sub_out = BottomUpScopeOutput.model_validate(scope_output)

    briefing = ScopeBriefing(
        scope=sub_out.briefing or "completed",
        wave=1,
        summary=sub_out.briefing,
        visited_nodes=sub_out.created_node_ids,
        created_nodes=sub_out.created_node_ids,
        created_edges=sub_out.created_edge_ids,
        nav_used=sub_out.nav_used,
        explore_used=sub_out.explore_used,
        super_sources=sub_out.super_sources,
    )
    accumulator.merge(briefing)

    return 1


# ══════════════════════════════════════════════════════════════
# Bottom-up ingest — Prepare scope (fact gathering only, isolated)
# ══════════════════════════════════════════════════════════════

bottom_up_prepare_scope_wf = hatchet.workflow(
    name="bottom_up_prepare_scope",
    input_validator=BottomUpPrepareScopeInput,
)


@bottom_up_prepare_scope_wf.durable_task(
    execution_timeout=timedelta(hours=2),
    schedule_timeout=_schedule_timeout,
)
async def bottom_up_prepare_scope(
    input: BottomUpPrepareScopeInput,
    ctx: DurableContext,
) -> dict:
    """Gather facts for a single scope — NO node building, NO query context.

    This workflow is deliberately isolated from the orchestrator's query.
    It only receives its scope_description, ensuring scope exploration
    is neutral and unbiased by the user's original query. Each scope
    runs its own scout to discover relevant terms.
    """
    from kt_hatchet.usage_helpers import flush_usage_to_db
    from kt_models.usage import start_usage_tracking
    from kt_worker_bottomup.bottom_up.scope import run_bottom_up_scope_pipeline

    state = cast(WorkerState, ctx.lifespan)
    start_usage_tracking()

    async def emit(event_type: str, payload: dict) -> None:
        try:
            await ctx.aio_put_stream(json.dumps({"type": event_type, **payload}))
        except Exception:
            logger.warning("Failed to stream event %s", event_type, exc_info=True)

    ctx.log(f"Prepare-scope starting: '{input.scope_description}'")

    await emit(
        "pipeline_scope_start",
        {
            "scope_id": input.scope_id,
            "scope_name": input.scope_description,
            "task_run_id": ctx.step_run_id,
            "mode": "bottom_up_ingest",
        },
    )

    await emit(
        "pipeline_phase",
        {
            "scope_id": input.scope_id,
            "phase": "gathering",
            "event": "start",
        },
    )

    async with _open_sessions(state) as (session, write_session):
        agent_ctx = await _build_agent_context(state, session, write_session=write_session, user_id=input.user_id)
        plan = await run_bottom_up_scope_pipeline(
            agent_ctx,
            scope_description=input.scope_description,
            explore_slice=input.explore_slice,
            message_id=input.message_id,
            conversation_id=input.conversation_id,
        )
        if write_session is not None:
            await write_session.commit()

    await emit(
        "pipeline_phase",
        {
            "scope_id": input.scope_id,
            "phase": "gathering",
            "event": "end",
        },
    )

    await emit(
        "pipeline_scope_end",
        {
            "scope_id": input.scope_id,
            "node_count": 0,
            "fact_count": plan.gathered_fact_count,
        },
    )

    ctx.log(
        f"Prepare-scope '{input.scope_description}': "
        f"{plan.gathered_fact_count} facts, {len(plan.node_plans)} extracted nodes"
    )

    await flush_usage_to_db(state.write_session_factory, input.conversation_id, input.message_id, "scope_prepare")

    return BottomUpPrepareScopeOutput(
        node_plans=plan.node_plans,
        explore_used=plan.explore_used,
        gathered_fact_count=plan.gathered_fact_count,
        extracted_count=plan.extracted_count,
        content_summary=plan.content_summary,
        source_urls=plan.source_urls,
    ).model_dump()


# ══════════════════════════════════════════════════════════════
# Bottom-up ingest — Phase 1: Prepare (orchestrator)
# ══════════════════════════════════════════════════════════════

bottom_up_prepare_wf = hatchet.workflow(
    name="bottom_up_prepare",
    input_validator=BottomUpPrepareInput,
    concurrency=ConcurrencyExpression(
        expression="input.conversation_id",
        max_runs=get_settings().bottom_up_prepare_max_runs,
        limit_strategy=ConcurrencyLimitStrategy.GROUP_ROUND_ROBIN,
    ),
)


@bottom_up_prepare_wf.durable_task(execution_timeout=timedelta(hours=4), schedule_timeout=_schedule_timeout)
async def bottom_up_prepare(input: BottomUpPrepareInput, ctx: DurableContext) -> dict:
    """Phase 1: Scout → plan scopes → fan-out scope gathering → prioritize.

    The orchestrator knows the user query and plans scopes, but scope
    exploration is isolated in separate bottom_up_prepare_scope_wf
    workflows that only receive their scope description — not the
    original query.  This prevents scope bias.

    Creates 0 new nodes. Returns proposed nodes with priorities for user selection.
    The output is stored on the ConversationMessage.metadata_json field.
    """
    from kt_hatchet.usage_helpers import flush_usage_to_db
    from kt_models.usage import start_usage_tracking
    from kt_worker_bottomup.bottom_up.scout import scout_impl

    state = cast(WorkerState, ctx.lifespan)
    start_usage_tracking()

    async def emit(event_type: str, payload: dict) -> None:
        try:
            await ctx.aio_put_stream(json.dumps({"type": event_type, **payload}))
        except Exception:
            logger.warning("Failed to stream event %s", event_type, exc_info=True)

    ctx.log("Starting bottom-up prepare (Phase 1)")

    await emit(
        "pipeline_scope_start",
        {
            "scope_id": "prepare",
            "scope_name": f"Gathering: {input.query}",
            "task_run_id": ctx.step_run_id,
            "mode": "bottom_up_ingest",
        },
    )

    # ── Scout phase ──────────────────────────────────────────────────
    await emit(
        "pipeline_phase",
        {
            "scope_id": "prepare",
            "phase": "scout",
            "event": "start",
        },
    )

    scout_queries = [input.query, f"overview of {input.query}"]
    scout_results: dict[str, Any] = {}
    async with _open_sessions(state) as (session, write_session):
        agent_ctx = await _build_agent_context(state, session, write_session=write_session, user_id=input.user_id)
        try:
            scout_results = await scout_impl(scout_queries, agent_ctx)
        except Exception:
            logger.exception("Scout phase failed")

    await emit(
        "pipeline_phase",
        {
            "scope_id": "prepare",
            "phase": "scout",
            "event": "end",
        },
    )

    ctx.log(f"Scout complete: {sum(len(v.get('graph_matches', [])) for v in scout_results.values())} graph matches")

    # ── Plan scopes (single wave, thorough scouting) ─────────────────
    from kt_worker_bottomup.shared import _plan_wave

    await emit(
        "pipeline_phase",
        {
            "scope_id": "prepare",
            "phase": "planning",
            "event": "start",
        },
    )

    async with _open_sessions(state) as (session, write_session):
        agent_ctx = await _build_agent_context(state, session, write_session=write_session, user_id=input.user_id)
        scopes = await _plan_wave(
            input.query,
            1,
            1,  # wave 1 of 1
            [],
            input.explore_budget,
            0,
            scout_results,
            agent_ctx,
        )

    await emit(
        "pipeline_phase",
        {
            "scope_id": "prepare",
            "phase": "planning",
            "event": "end",
        },
    )

    if not scopes:
        # Fallback: use the query itself as a single scope
        from kt_worker_bottomup.bottom_up.state import ScopePlan

        scopes = [ScopePlan(scope=input.query, explore_budget=input.explore_budget, nav_budget=0)]

    ctx.log(f"Planned {len(scopes)} scopes: {[s.scope for s in scopes]}")

    # ── Fan out scope gathering (isolated — scopes don't see query) ──
    await emit(
        "pipeline_phase",
        {
            "scope_id": "prepare",
            "phase": "gathering",
            "event": "start",
        },
    )

    scope_meta = TriggerWorkflowOptions(
        additional_metadata={
            "message_id": input.message_id,
            "conversation_id": input.conversation_id,
        }
    )

    bulk_items = []
    for scope in scopes:
        scope_id = str(uuid.uuid4())
        bulk_items.append(
            bottom_up_prepare_scope_wf.create_bulk_run_item(
                input=BottomUpPrepareScopeInput(
                    scope_id=scope_id,
                    scope_description=scope.scope,
                    explore_slice=scope.explore_budget,
                    message_id=input.message_id,
                    conversation_id=input.conversation_id,
                    user_id=input.user_id,
                ),
                options=scope_meta,
            )
        )

    all_extracted: list[dict[str, Any]] = []
    total_fact_count = 0
    total_explore_used = 0
    content_summaries: list[str] = []
    source_count = 0
    all_source_urls: list[dict[str, str]] = []
    seen_urls: set[str] = set()

    try:
        results = await bottom_up_prepare_scope_wf.aio_run_many(bulk_items)
    except Exception:
        logger.exception("Prepare scope fan-out failed (some scopes may have errored)")
        results = []

    for result in results:
        task_data = result.get("bottom_up_prepare_scope", result) if isinstance(result, dict) else result
        scope_out = BottomUpPrepareScopeOutput.model_validate(task_data)
        all_extracted.extend(scope_out.node_plans)
        total_fact_count += scope_out.gathered_fact_count
        total_explore_used += scope_out.explore_used
        source_count += scope_out.explore_used
        if scope_out.content_summary:
            content_summaries.append(scope_out.content_summary)
        for src in scope_out.source_urls:
            url = src.get("url", "")
            if url and url not in seen_urls:
                seen_urls.add(url)
                all_source_urls.append(src)

    await emit(
        "pipeline_phase",
        {
            "scope_id": "prepare",
            "phase": "gathering",
            "event": "end",
        },
    )

    ctx.log(
        f"Scope gathering complete: {total_fact_count} facts, "
        f"{len(all_extracted)} extracted nodes from {len(results)} scopes"
    )

    ctx.refresh_timeout("30m")

    # ── Build research summary from seeds in write-db ─────────────────
    combined_summary = "\n\n".join(content_summaries) if content_summaries else ""

    from kt_db.repositories.write_seeds import WriteSeedRepository
    from kt_hatchet.models import ResearchSummaryOutput, SeedSummary

    seed_summaries: list[SeedSummary] = []
    try:
        async with _open_sessions(state) as (session, write_session):
            if write_session is not None:
                seed_repo = WriteSeedRepository(write_session)
                # Get all active seeds created during gathering.  The high limit
                # ensures the summary reflects the full scope; each SeedSummary
                # is lightweight (~200 bytes) so 10k is ~2 MB in memory.
                all_seeds = await seed_repo.list_seeds(
                    status="active",
                    limit=10_000,
                )
                for seed in all_seeds:
                    aliases = list((seed.metadata_ or {}).get("aliases", []))
                    seed_summaries.append(
                        SeedSummary(
                            key=seed.key,
                            name=seed.name,
                            node_type=seed.node_type,
                            fact_count=seed.fact_count,
                            aliases=aliases,
                            status=seed.status,
                            entity_subtype=seed.entity_subtype,
                        )
                    )
    except Exception:
        logger.debug("Seed summary lookup failed during prepare", exc_info=True)

    await flush_usage_to_db(
        state.write_session_factory,
        input.conversation_id,
        input.message_id,
        "orchestrator_prepare",
    )

    output = ResearchSummaryOutput(
        fact_count=total_fact_count,
        source_count=len(all_source_urls),
        source_urls=all_source_urls,
        seeds=seed_summaries,
        content_summary=combined_summary,
        explore_used=total_explore_used,
    )

    # ── Persist research report ──────────────────────────────────────
    try:
        from kt_db.repositories.research_reports import ResearchReportRepository

        async with state.session_factory() as session:
            await ResearchReportRepository(session).create(
                message_id=uuid.UUID(input.message_id),
                conversation_id=uuid.UUID(input.conversation_id),
                nodes_created=0,
                edges_created=0,
                waves_completed=len(results),
                explore_budget=input.explore_budget,
                explore_used=total_explore_used,
                scope_summaries=[f"Gathered {total_fact_count} facts, {len(seed_summaries)} seeds"],
                total_prompt_tokens=0,
                total_completion_tokens=0,
                total_cost_usd=0.0,
                usage_by_model=None,
                usage_by_task=None,
                workflow_run_id=ctx.workflow_run_id,
                summary_data=output.model_dump(),
            )

    except Exception:
        logger.warning("Failed to persist research report for prepare", exc_info=True)

    # ── Store output on message metadata ─────────────────────────────
    try:
        async with state.session_factory() as session:
            from kt_db.repositories.conversations import ConversationRepository

            repo = ConversationRepository(session)
            await repo.update_message(
                uuid.UUID(input.message_id),
                metadata_json=output.model_dump(),
                status="completed",
                content=f"Research complete: {total_fact_count} facts gathered, {len(seed_summaries)} seeds created.",
            )

    except Exception:
        logger.exception("Failed to store prepare output on message")

    await emit(
        "pipeline_scope_end",
        {
            "scope_id": "prepare",
            "node_count": 0,
            "fact_count": total_fact_count,
            "seed_count": len(seed_summaries),
        },
    )

    # ── Fire-and-forget: promote seeds to stub nodes ─────────────────
    try:
        from kt_hatchet.models import AutoBuildInput
        from kt_worker_nodes.workflows.auto_build import auto_build_task

        await auto_build_task.aio_run_no_wait(AutoBuildInput())
        ctx.log("Dispatched auto_build_graph to promote accumulated seeds")
    except Exception:
        logger.warning("Failed to dispatch auto_build_graph", exc_info=True)

    await emit(
        "done",
        {
            "phase": "prepare",
            "fact_count": total_fact_count,
            "seed_count": len(seed_summaries),
        },
    )

    ctx.log(f"Bottom-up prepare complete: {total_fact_count} facts, {len(seed_summaries)} seeds")

    return output.model_dump()


# ══════════════════════════════════════════════════════════════
# Agent-assisted node selection
# ══════════════════════════════════════════════════════════════

agent_select_wf = hatchet.workflow(
    name="agent_select",
    input_validator=AgentSelectInput,
    concurrency=ConcurrencyExpression(
        expression="input.conversation_id",
        max_runs=get_settings().agent_select_max_runs,
        limit_strategy=ConcurrencyLimitStrategy.GROUP_ROUND_ROBIN,
    ),
)


@agent_select_wf.task(execution_timeout=timedelta(minutes=10), schedule_timeout=_schedule_timeout)
async def agent_select(input: AgentSelectInput, ctx: DurableContext) -> dict:
    """Run agent-assisted node selection over proposed nodes.

    The agent processes nodes in batches of 100, using tools to select
    and optionally edit nodes. Results are stored on the conversation
    message metadata for frontend retrieval.
    """
    from kt_worker_bottomup.bottom_up.agent_select import agent_select_nodes

    state = cast(WorkerState, ctx.lifespan)

    ctx.log(f"Agent selecting up to {input.max_select} from {len(input.proposed_nodes)} nodes")

    async with _open_sessions(state) as (session, write_session):
        agent_ctx = await _build_agent_context(state, session, write_session=write_session, user_id=input.user_id)
        updated_nodes = await agent_select_nodes(
            agent_ctx,
            input.proposed_nodes,
            max_select=input.max_select,
            instructions=input.instructions,
        )

    # Store results on the conversation message metadata
    output = AgentSelectOutput(proposed_nodes=updated_nodes)

    try:
        from kt_db.repositories.conversations import ConversationRepository

        async with state.session_factory() as db_session:
            conv_repo = ConversationRepository(db_session)
            msg = await conv_repo.get_message(uuid.UUID(input.message_id))
            if msg is not None:
                existing_meta = dict(msg.metadata_json or {})
                existing_meta["proposed_nodes"] = [n.model_dump() for n in updated_nodes]
                existing_meta["agent_select_status"] = "completed"
                await conv_repo.update_message(
                    msg.id,
                    metadata_json=existing_meta,
                )
                await db_session.commit()
    except Exception:
        logger.warning("Failed to persist agent selection to message metadata", exc_info=True)

    selected_count = sum(1 for n in updated_nodes if n.selected)
    ctx.log(f"Agent selection complete: {selected_count}/{input.max_select} selected")

    return output.model_dump()
