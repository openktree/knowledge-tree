"""Node pipeline DAG workflow and standalone edge resolution task.

Unified workflow handling both node creation (from seeds) and rebuilds.

DAG structure::

    prepare_node
    ├── generate_dimensions (parent: prepare_node)
    │   └── finalize_node (parent: generate_dimensions)
    └── generate_definition (parent: prepare_node, parallel with dimensions)

Modes:
  - ``create`` -- promote a seed to a full node (default, used by bottom-up/ingest)
  - ``rebuild_incremental`` -- enrich existing node with new facts
  - ``rebuild_full`` -- delete all dims, regenerate everything

Each task is a thin wrapper around the corresponding ``HatchetPipeline``
phase method, which opens its own DB session and delegates to the original
battle-tested sub-pipeline classes.

``generate_dimensions`` fans out edge resolution as standalone child tasks
via ``edge_task.aio_run_many()`` and collects the results.

Fact gathering (external search) is NOT done here -- all facts come from
seeds.  ``prepare_node`` in create mode reads from the existing fact pool
only (``explore_budget=0``).
"""

from __future__ import annotations

import logging
import uuid as _uuid
from datetime import timedelta
from typing import cast

from hatchet_sdk import (
    ConcurrencyExpression,
    ConcurrencyLimitStrategy,
    Context,
    DurableContext,
)

from kt_config.settings import get_settings
from kt_hatchet.client import get_hatchet
from kt_hatchet.lifespan import WorkerState
from kt_hatchet.models import (
    DimensionsOutput,
    EdgeOutput,
    NodePipelineInput,
    UpdateEdgesInput,
)
from kt_worker_nodes.hatchet_pipeline import HatchetPipeline

logger = logging.getLogger(__name__)

hatchet = get_hatchet()
_schedule_timeout = timedelta(minutes=get_settings().hatchet_schedule_timeout_minutes)


# ══════════════════════════════════════════════════════════════
# Node pipeline DAG workflow
# ══════════════════════════════════════════════════════════════

node_pipeline_wf = hatchet.workflow(
    name="node_pipeline",
    input_validator=NodePipelineInput,
    concurrency=ConcurrencyExpression(
        expression="has(input.node_id) && input.node_id != '' && input.node_id != null ? input.node_id : input.seed_key",
        max_runs=1,
        limit_strategy=ConcurrencyLimitStrategy.GROUP_ROUND_ROBIN,
    ),
)


@node_pipeline_wf.task(execution_timeout=timedelta(minutes=15), schedule_timeout=_schedule_timeout)
async def prepare_node(input: NodePipelineInput, ctx: Context) -> dict:
    """Prepare a node for the pipeline -- create from seed or load for rebuild.

    Create mode:
      Delegates to ``HatchetPipeline.create`` which calls the original
      NodeCreationPipeline phases (classify_and_gather_batch -> enrich_batch
      or create_batch) within a single session.  External search is disabled --
      facts must have been gathered before this task runs.

    Rebuild mode:
      Loads the existing node from write-db and runs ``HatchetPipeline.enrich``
      to sync seed facts.  Incremental mode skips nodes with too few facts.

    Returns ``{node_id, concept, node_type, status}`` in all modes.
    """
    from kt_hatchet.usage_helpers import flush_usage_to_db
    from kt_models.usage import start_usage_tracking

    state = cast(WorkerState, ctx.lifespan)
    start_usage_tracking()

    is_rebuild = input.mode.startswith("rebuild")

    if is_rebuild:
        result = await _prepare_rebuild(input, state, ctx)
    else:
        result = await _prepare_create(input, state, ctx)

    await flush_usage_to_db(state.write_session_factory, input.conversation_id, input.message_id, "node_creation")
    return result


async def _prepare_create(
    input: NodePipelineInput,
    state: WorkerState,
    ctx: Context,
) -> dict:
    """Create mode: promote seed to node via NodeCreationPipeline."""
    ctx.log(f"prepare_node[create]: concept={input.concept!r}, type={input.node_type}")

    result = await HatchetPipeline(state, user_id=input.user_id, graph_id=input.graph_id).create(
        concept=input.concept,
        node_type=input.node_type,
        seed_key=input.seed_key,
        query=input.concept,
        entity_subtype=input.entity_subtype,
    )

    node_id = result.get("node_id")
    action = result.get("action", "unknown")
    ctx.log(f"prepare_node[create]: completed node_id={node_id}, action={action}")
    return result


async def _prepare_rebuild(
    input: NodePipelineInput,
    state: WorkerState,
    ctx: Context,
) -> dict:
    """Rebuild mode: load existing node, enrich from seed pool."""
    from kt_config.types import COMPOSITE_NODE_TYPES
    from kt_db.repositories.write_nodes import WriteNodeRepository
    from kt_db.repositories.write_seeds import WriteSeedRepository

    nid = input.node_id
    if not nid:
        ctx.log("prepare_node[rebuild]: ERROR -- no node_id provided")
        return {"node_id": None, "status": "error"}

    ctx.log(f"prepare_node[rebuild]: node_id={nid}, mode={input.mode}")

    if state.write_session_factory is None:
        raise RuntimeError("prepare_node[rebuild]: write_session_factory is required")

    # Load node + check fact count in a single session
    async with state.write_session_factory() as ws:
        wn = await WriteNodeRepository(ws).get_by_uuid(_uuid.UUID(nid))
        if wn is None:
            ctx.log(f"prepare_node[rebuild]: node {nid} not found in write-db")
            return {"node_id": nid, "status": "error"}

        node_concept = wn.concept
        node_type = wn.node_type
        node_key = wn.key
        node_fact_ids = wn.fact_ids

        if node_type in COMPOSITE_NODE_TYPES:
            ctx.log(f"prepare_node[rebuild]: skipping -- {node_type} is composite")
            return {
                "node_id": nid,
                "concept": node_concept,
                "node_type": node_type,
                "status": "skipped",
            }

        # Incremental mode: skip stub/partial nodes with too few facts
        if input.mode == "rebuild_incremental":
            settings = get_settings()
            seed_repo = WriteSeedRepository(ws)
            seeds = await seed_repo.get_seeds_by_promoted_node_key(node_key)
            fact_count = 0
            if seeds:
                all_fact_ids: set[_uuid.UUID] = set()
                for seed in seeds:
                    descendant_facts = await seed_repo.get_all_descendant_facts(seed.key)
                    all_fact_ids.update(descendant_facts)
                fact_count = len(all_fact_ids)
            else:
                fact_count = len(node_fact_ids or [])

            min_facts = settings.enrichment_min_facts_for_dimensions
            if fact_count < min_facts:
                await WriteNodeRepository(ws).set_enrichment_status(node_key, "partial")
                await ws.commit()
                ctx.log(
                    f"prepare_node[rebuild]: node '{node_concept}' has {fact_count} facts "
                    f"(need {min_facts}) -- marked partial"
                )
                return {
                    "node_id": nid,
                    "concept": node_concept,
                    "node_type": node_type,
                    "status": "skipped",
                    "fact_count": fact_count,
                }

    # Enrich: sync seed facts to node
    pipeline = HatchetPipeline(state, user_id=input.user_id, graph_id=input.graph_id)
    enrich_result = await pipeline.enrich(nid)
    new_facts = enrich_result.get("new_facts_linked", 0)
    ctx.log(f"prepare_node[rebuild]: pool enrichment done, {new_facts} new facts linked")

    return {
        "node_id": nid,
        "concept": node_concept,
        "node_type": node_type,
        "action": "rebuild",
        "status": "ok",
    }


@node_pipeline_wf.durable_task(
    parents=[prepare_node],
    execution_timeout=timedelta(minutes=30),
    schedule_timeout=_schedule_timeout,
)
async def generate_dimensions(input: NodePipelineInput, ctx: DurableContext) -> dict:
    """Generate dimensions, then fan out edge resolution.

    Respects ``input.mode`` (incremental vs full) and ``input.scope``.
    Spawns ``edge_task`` via ``aio_run_many`` and collects results.

    Returns ``DimensionsOutput`` with node_id and aggregated edge_ids.
    """
    from kt_hatchet.usage_helpers import flush_usage_to_db
    from kt_models.usage import start_usage_tracking

    state = cast(WorkerState, ctx.lifespan)
    start_usage_tracking()

    prepare_result: dict = ctx.task_output(prepare_node)
    node_id_str: str | None = prepare_result.get("node_id")
    status = prepare_result.get("status", "ok")

    if not node_id_str or status in ("error", "skipped"):
        ctx.log(f"generate_dimensions: skipping -- prepare_node status={status}")
        return DimensionsOutput(node_id=node_id_str or "", edge_ids=[]).model_dump()

    node_type: str = prepare_result.get("node_type", input.node_type)
    concept: str = prepare_result.get("concept", input.concept)
    pipeline = HatchetPipeline(state, user_id=input.user_id, graph_id=input.graph_id)

    dims_created = 0
    fact_count = 0

    # -- Generate dimensions -----------------------------------------------
    if input.scope in ("all", "dimensions"):
        ctx.log(f"generate_dimensions: node_id={node_id_str}, mode={input.mode}")

        max_retries = 2
        dim_result: dict = {}
        for attempt in range(1, max_retries + 1):
            try:
                if input.mode == "rebuild_full":
                    dim_result = await pipeline.full_dimensions(node_id_str)
                else:
                    dim_result = await pipeline.dimensions(node_id_str)
                break
            except Exception:
                if attempt < max_retries:
                    ctx.log(f"generate_dimensions: attempt {attempt} failed, retrying...")
                    logger.warning(
                        "generate_dimensions: attempt %d failed for node %s, retrying",
                        attempt,
                        node_id_str,
                        exc_info=True,
                    )
                else:
                    ctx.log(f"generate_dimensions: ERROR -- all {max_retries} attempts failed for node {node_id_str}")
                    logger.exception(
                        "generate_dimensions: all %d attempts failed for node %s",
                        max_retries,
                        node_id_str,
                    )

        node_type = dim_result.get("node_type", node_type)
        dims_created = dim_result.get("dimensions_created", 0)
        fact_count = dim_result.get("fact_count", 0)

        ctx.log(f"generate_dimensions: dimensions_created={dims_created}, fact_count={fact_count}")

        if fact_count > 0 and dims_created == 0:
            ctx.log(
                f"generate_dimensions: WARNING -- node {node_id_str} has {fact_count} facts "
                f"but 0 dimensions were generated (LLM error or empty response)"
            )

    # -- Fan out edge resolution tasks -------------------------------------
    all_edge_ids: list[str] = []

    if input.scope in ("all", "edges"):
        ctx.log("generate_dimensions: spawning edge resolution task")

        edge_results = await edge_task.aio_run_many(
            [
                edge_task.create_bulk_run_item(
                    input=UpdateEdgesInput(
                        node_id=node_id_str,
                        concept=concept,
                        node_type=node_type,
                        scope_id=input.scope_id,
                        message_id=input.message_id,
                        conversation_id=input.conversation_id,
                        user_id=input.user_id,
                    ),
                )
            ]
        )

        for result in edge_results:
            edge_out = EdgeOutput.model_validate(result)
            all_edge_ids.extend(edge_out.edge_ids)

        # Full rebuild: also refresh justifications on existing edges
        if input.mode == "rebuild_full":
            try:
                refresh_result = await pipeline.refresh_edge_justifications(
                    node_id_str,
                    concept,
                    node_type,
                )
                edges_refreshed = refresh_result.get("edges_refreshed", 0)
                if edges_refreshed:
                    ctx.log(f"generate_dimensions: refreshed {edges_refreshed} existing edge justifications")
            except Exception:
                logger.warning(
                    "generate_dimensions: edge justification refresh failed for %s",
                    node_id_str,
                    exc_info=True,
                )

    ctx.log(f"generate_dimensions: done -- {dims_created} dimensions, {len(all_edge_ids)} edges")

    await flush_usage_to_db(state.write_session_factory, input.conversation_id, input.message_id, "dimensions")

    return DimensionsOutput(
        node_id=node_id_str,
        dimensions_created=dims_created,
        fact_count=fact_count,
        edge_ids=all_edge_ids,
    ).model_dump()


@node_pipeline_wf.task(
    parents=[prepare_node],
    execution_timeout=timedelta(minutes=15),
    schedule_timeout=_schedule_timeout,
)
async def generate_definition(input: NodePipelineInput, ctx: Context) -> dict:
    """Generate a definition for the node (parallel with dimensions).

    Respects ``input.scope`` -- skips if scope is "edges" only.
    """
    from kt_hatchet.usage_helpers import flush_usage_to_db
    from kt_models.usage import start_usage_tracking

    state = cast(WorkerState, ctx.lifespan)
    start_usage_tracking()

    prepare_result: dict = ctx.task_output(prepare_node)
    node_id_str: str | None = prepare_result.get("node_id")
    status = prepare_result.get("status", "ok")

    if not node_id_str or status in ("error", "skipped"):
        ctx.log(f"generate_definition: skipping -- prepare_node status={status}")
        return {"node_id": None, "has_definition": False}

    if input.scope == "edges":
        ctx.log("generate_definition: skipping -- scope is edges-only")
        return {"node_id": node_id_str, "has_definition": False}

    ctx.log(f"generate_definition: node_id={node_id_str}")

    result = await HatchetPipeline(state, user_id=input.user_id, graph_id=input.graph_id).definition(node_id_str)

    await flush_usage_to_db(state.write_session_factory, input.conversation_id, input.message_id, "definition")

    ctx.log(f"generate_definition: has_definition={result.get('has_definition')}")
    return result


@node_pipeline_wf.task(
    parents=[generate_dimensions],
    execution_timeout=timedelta(minutes=5),
    schedule_timeout=_schedule_timeout,
)
async def finalize_node(input: NodePipelineInput, ctx: Context) -> dict:
    """Finalize the node after dimensions and edges are complete.

    For rebuild modes: updates enrichment_status, increments update_count,
    and optionally dispatches dialectic pair rebuild.

    For create mode: lightweight -- no additional work needed.
    """
    state = cast(WorkerState, ctx.lifespan)

    prepare_result: dict = ctx.task_output(prepare_node)
    node_id_str: str | None = prepare_result.get("node_id")
    status = prepare_result.get("status", "ok")

    if not node_id_str or status in ("error", "skipped"):
        ctx.log(f"finalize_node: skipping -- prepare_node status={status}")
        return {"node_id": node_id_str, "status": status}

    is_rebuild = input.mode.startswith("rebuild")

    if is_rebuild and state.write_session_factory:
        from kt_db.repositories.write_nodes import WriteNodeRepository

        # Update enrichment status + check for dialectic pair in one session
        pair_id_str: str | None = None
        async with state.write_session_factory() as ws:
            node_repo = WriteNodeRepository(ws)
            wn = await node_repo.get_by_uuid(_uuid.UUID(node_id_str))
            if wn:
                await node_repo.set_enrichment_status(wn.key, "enriched")
                await node_repo.increment_update_count(wn.key)
                await ws.commit()
                ctx.log("finalize_node: enrichment_status='enriched'")

                if input.mode == "rebuild_full" and input.scope == "all" and input.recalculate_pair:
                    pair_id_str = (wn.metadata_ or {}).get("dialectic_pair_id")

        # Dispatch pair rebuild outside the session (fire-and-forget:
        # pair failure doesn't affect the primary node's result).
        if pair_id_str:
            ctx.log(f"finalize_node: triggering pair rebuild for {pair_id_str}")
            try:
                await node_pipeline_wf.aio_run_no_wait(
                    NodePipelineInput(
                        mode="rebuild_full",
                        scope="all",
                        node_id=str(pair_id_str),
                        recalculate_pair=False,
                        user_id=input.user_id,
                    ),
                )
            except Exception:
                logger.warning("Failed to rebuild dialectic pair %s", pair_id_str, exc_info=True)

    ctx.log(f"finalize_node: completed node_id={node_id_str}")
    return {"node_id": node_id_str, "status": "completed"}


# ══════════════════════════════════════════════════════════════
# Standalone edge resolution task
# ══════════════════════════════════════════════════════════════


@hatchet.task(
    name="edge_resolution",
    input_validator=UpdateEdgesInput,
    execution_timeout=timedelta(minutes=30),
    schedule_timeout=_schedule_timeout,
)
async def edge_task(input: UpdateEdgesInput, ctx: Context) -> dict:
    """Resolve edges from pending candidates for a single node.

    Spawned by ``generate_dimensions`` as a child task.
    Reads write_edge_candidates for this node's seed, generates justifications
    via LLM, and creates edges in the graph.

    Returns ``EdgeOutput`` with the created edge IDs.
    """
    from kt_hatchet.usage_helpers import flush_usage_to_db
    from kt_models.usage import start_usage_tracking

    state = cast(WorkerState, ctx.lifespan)
    start_usage_tracking()

    ctx.log(f"edge_task: concept={input.concept}, node_id={input.node_id}")

    result = await HatchetPipeline(state, user_id=input.user_id, graph_id=input.graph_id).edges(
        node_id=input.node_id,
        concept=input.concept,
        node_type=input.node_type,
    )

    ctx.log(
        f"edge_task: done -- concept={input.concept}, "
        f"edges_created={result.get('edges_created', 0)}, "
        f"edge_ids={result.get('edge_ids', [])}"
    )

    await flush_usage_to_db(
        state.write_session_factory,
        input.conversation_id,
        input.message_id,
        "edge_classification",
    )

    return EdgeOutput(
        edge_ids=result.get("edge_ids", []),
        edges_created=result.get("edges_created", 0),
    ).model_dump()
