"""Node pipeline DAG workflow and standalone edge resolution task.

DAG structure::

    create_node
    ├── generate_dimensions (parent: create_node)
    │   └── update_ancestry (parent: generate_dimensions)
    └── generate_definition (parent: create_node, parallel with dimensions)

Each task is a thin wrapper around the corresponding ``HatchetPipeline``
phase method, which opens its own DB session and delegates to the original
battle-tested sub-pipeline classes.

``generate_dimensions`` fans out edge resolution as standalone child tasks
via ``edge_task.aio_run_many()``, collects the results, then proceeds to
``update_ancestry``.

Fact gathering (external search) is NOT done here — it is the responsibility
of ``ScopePlannerAgent`` in ``exploration.py``.  Each ``create_node`` task
reads from the existing fact pool only (``explore_budget=0``).
"""

from __future__ import annotations

import json
import logging
from datetime import timedelta
from typing import cast

from hatchet_sdk import Context, DurableContext

from kt_config.settings import get_settings
from kt_hatchet.client import get_hatchet
from kt_hatchet.lifespan import WorkerState
from kt_hatchet.models import (
    AncestryOutput,
    BuildNodeInput,
    CrystallizeInput,
    DimensionsOutput,
    EdgeOutput,
    RecalculateInput,
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
    input_validator=BuildNodeInput,
)


@node_pipeline_wf.task(execution_timeout=timedelta(minutes=15), schedule_timeout=_schedule_timeout)
async def create_node(input: BuildNodeInput, ctx: Context) -> dict:
    """Phase 1+1.5+2: classify, gather from pool, create/enrich node.

    Delegates to ``HatchetPipeline.create`` which calls the original
    NodeCreationPipeline phases (classify_and_gather_batch → enrich_batch
    or create_batch) within a single session.

    External search is disabled — facts must have been gathered by the
    ScopePlannerAgent before this task runs.

    Returns ``{node_id, action, concept, node_type, explore_charged}``.
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

    ctx.log(f"create_node: concept={input.concept!r}, type={input.node_type}")

    await emit(
        "pipeline_phase",
        {
            "scope_id": input.scope_id,
            "phase": "building",
            "status": "started",
        },
    )

    result = await HatchetPipeline(state, api_key=input.api_key).create(
        concept=input.concept,
        node_type=input.node_type,
        seed_key=input.seed_key,
        query=input.concept,
        entity_subtype=input.entity_subtype,
        existing_node_id=input.existing_node_id,
    )

    await emit(
        "pipeline_phase",
        {
            "scope_id": input.scope_id,
            "phase": "building",
            "status": "completed",
        },
    )

    node_id = result.get("node_id")
    action = result.get("action", "unknown")
    ctx.log(f"create_node: completed node_id={node_id}, action={action}")

    await flush_usage_to_db(state.write_session_factory, input.conversation_id, input.message_id, "node_creation")
    return result


@node_pipeline_wf.durable_task(
    parents=[create_node],
    execution_timeout=timedelta(minutes=30),
    schedule_timeout=_schedule_timeout,
)
async def generate_dimensions(input: BuildNodeInput, ctx: DurableContext) -> dict:
    """Phase 3: generate dimensions, then fan out edge resolution.

    Spawns ``edge_task`` children (1 same_type + N cross_type) via
    ``aio_run_many`` and collects the results.

    Returns ``DimensionsOutput`` with node_id and aggregated edge_ids.
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

    create_result: dict = ctx.task_output(create_node)
    node_id_str: str | None = create_result.get("node_id")

    if not node_id_str:
        ctx.log("generate_dimensions: skipping — create_node returned no node_id")
        return DimensionsOutput(node_id="", edge_ids=[]).model_dump()

    ctx.log(f"generate_dimensions: node_id={node_id_str}")

    # ── Generate dimensions ──────────────────────────────────────
    await emit(
        "pipeline_phase",
        {
            "scope_id": input.scope_id,
            "phase": "dimensions",
            "status": "started",
        },
    )

    max_retries = 2
    dim_result: dict = {}
    for attempt in range(1, max_retries + 1):
        try:
            dim_result = await HatchetPipeline(state, api_key=input.api_key).dimensions(node_id_str)
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
                ctx.log(f"generate_dimensions: ERROR — all {max_retries} attempts failed for node {node_id_str}")
                logger.exception(
                    "generate_dimensions: all %d attempts failed for node %s",
                    max_retries,
                    node_id_str,
                )

    node_type: str = dim_result.get("node_type", "concept")
    dims_created: int = dim_result.get("dimensions_created", 0)
    fact_count: int = dim_result.get("fact_count", 0)

    ctx.log(f"generate_dimensions: dimensions_created={dims_created}, fact_count={fact_count}, node_type={node_type}")

    if fact_count > 0 and dims_created == 0:
        ctx.log(
            f"generate_dimensions: WARNING — node {node_id_str} has {fact_count} facts "
            f"but 0 dimensions were generated (LLM error or empty response)"
        )

    await emit(
        "pipeline_phase",
        {
            "scope_id": input.scope_id,
            "phase": "dimensions",
            "status": "completed",
        },
    )

    # ── Fan out edge resolution tasks ────────────────────────────
    all_edge_ids: list[str] = []

    if input.skip_edges:
        ctx.log("generate_dimensions: skipping edges (skip_edges=True)")
    else:
        from kt_hatchet.models import UpdateEdgesInput

        ctx.log("generate_dimensions: spawning edge resolution task")

        await emit(
            "pipeline_phase",
            {
                "scope_id": input.scope_id,
                "phase": "edges",
                "status": "started",
            },
        )

        concept = create_result.get("concept", input.concept)

        # Single edge task — the candidate-based resolver handles both
        # same-type and cross-type edges in one pass.
        edge_results = await edge_task.aio_run_many(
            [
                edge_task.create_bulk_run_item(
                    input=UpdateEdgesInput(
                        node_id=node_id_str,
                        edge_mode="candidates",
                        concept=concept,
                        node_type=node_type,
                        scope_id=input.scope_id,
                        message_id=input.message_id,
                        conversation_id=input.conversation_id,
                        api_key=input.api_key,
                    ),
                )
            ]
        )

        for result in edge_results:
            edge_out = EdgeOutput.model_validate(result)
            all_edge_ids.extend(edge_out.edge_ids)

        await emit(
            "pipeline_phase",
            {
                "scope_id": input.scope_id,
                "phase": "edges",
                "status": "completed",
            },
        )

    ctx.log(f"generate_dimensions: done — {dims_created} dimensions, {len(all_edge_ids)} edges")

    await flush_usage_to_db(state.write_session_factory, input.conversation_id, input.message_id, "dimensions")

    return DimensionsOutput(
        node_id=node_id_str,
        dimensions_created=dims_created,
        fact_count=fact_count,
        edge_ids=all_edge_ids,
    ).model_dump()


@node_pipeline_wf.task(
    parents=[create_node],
    execution_timeout=timedelta(minutes=15),
    schedule_timeout=_schedule_timeout,
)
async def generate_definition(input: BuildNodeInput, ctx: Context) -> dict:
    """Phase 3.5 (parallel): generate a definition for the node.

    Runs in parallel with ``generate_dimensions``.  Terminal — no downstream
    tasks depend on the definition.
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

    create_result: dict = ctx.task_output(create_node)
    node_id_str: str | None = create_result.get("node_id")

    if not node_id_str:
        ctx.log("generate_definition: skipping — create_node returned no node_id")
        return {"node_id": None, "has_definition": False}

    ctx.log(f"generate_definition: node_id={node_id_str}")

    await emit(
        "pipeline_phase",
        {
            "scope_id": input.scope_id,
            "phase": "definitions",
            "status": "started",
        },
    )

    result = await HatchetPipeline(state, api_key=input.api_key).definition(node_id_str)

    await emit(
        "pipeline_phase",
        {
            "scope_id": input.scope_id,
            "phase": "definitions",
            "status": "completed",
        },
    )

    await flush_usage_to_db(state.write_session_factory, input.conversation_id, input.message_id, "definition")

    ctx.log(f"generate_definition: has_definition={result.get('has_definition')}")
    return result


@node_pipeline_wf.task(
    parents=[generate_dimensions],
    execution_timeout=timedelta(minutes=15),
    schedule_timeout=_schedule_timeout,
)
async def update_ancestry(input: BuildNodeInput, ctx: Context) -> dict:
    """Phase 5: determine ontological ancestry for the newly built node.

    Runs after ``generate_dimensions`` (and therefore after all edges are
    resolved) to ensure the graph is fully populated before ancestry resolution.

    Uses AncestryPipeline to propose AI+base ontology chains, merge them,
    and resolve against the existing system graph.

    For entity nodes, assigns the default parent only (no ontological ancestry).
    """
    state = cast(WorkerState, ctx.lifespan)

    async def emit(event_type: str, payload: dict) -> None:
        try:
            await ctx.aio_put_stream(json.dumps({"type": event_type, **payload}))
        except Exception:
            logger.warning("Failed to stream event %s", event_type, exc_info=True)

    create_result: dict = ctx.task_output(create_node)
    node_id_str: str | None = create_result.get("node_id")
    node_type: str = create_result.get("node_type", input.node_type)

    # Composite nodes have no ancestry/parents
    from kt_config.types import COMPOSITE_NODE_TYPES

    if node_type in COMPOSITE_NODE_TYPES:
        ctx.log(f"update_ancestry: skipping — {node_type} is composite, no ancestry")
        return AncestryOutput(node_id=node_id_str or "").model_dump()

    if not node_id_str:
        ctx.log("update_ancestry: skipping — create_node returned no node_id")
        return AncestryOutput(node_id="").model_dump()

    # Skip ontology for stub nodes created by the ancestry pipeline itself
    # to prevent recursive ancestry generation that mutates established chains.
    if input.skip_ontology:
        ctx.log(f"update_ancestry: skipping — skip_ontology=True for {node_id_str}")
        return AncestryOutput(node_id=node_id_str).model_dump()

    ctx.log(f"update_ancestry: node_id={node_id_str}, type={node_type}")

    await emit(
        "pipeline_phase",
        {
            "scope_id": input.scope_id,
            "phase": "ancestry",
            "status": "started",
        },
    )

    result = await HatchetPipeline(state, api_key=input.api_key).ancestry(
        node_id=node_id_str,
        node_name=input.concept,
        node_type=node_type,
    )

    await emit(
        "pipeline_phase",
        {
            "scope_id": input.scope_id,
            "phase": "ancestry",
            "status": "completed",
        },
    )

    # Stub nodes are already created and wired by the ancestry pipeline.
    # Log what was created.
    nodes_created = result.get("nodes_created", [])
    if nodes_created:
        ctx.log(f"update_ancestry: created {len(nodes_created)} stub nodes")

    # Trigger crystallization check for the parent (fire-and-forget).
    # Use asyncio.create_task so we don't block the ancestry step waiting
    # for crystallization to complete — it can take minutes.
    parent_id_str = result.get("parent_id")
    if parent_id_str:
        import asyncio

        async def _fire_and_forget_crystallize() -> None:
            try:
                await crystallize_task.aio_run(
                    CrystallizeInput(
                        parent_node_id=parent_id_str,
                        scope_id=input.scope_id,
                        message_id=input.message_id,
                        conversation_id=input.conversation_id,
                        api_key=input.api_key,
                    ),
                )
            except Exception:
                logger.debug(
                    "update_ancestry: crystallization trigger failed for parent %s",
                    parent_id_str,
                    exc_info=True,
                )

        asyncio.create_task(_fire_and_forget_crystallize())

    ctx.log(
        f"update_ancestry: completed for node_id={node_id_str}, "
        f"parent={result.get('parent_id')}, stubs={len(nodes_created)}"
    )
    return AncestryOutput(
        node_id=node_id_str,
        parent_id=result.get("parent_id", ""),
        nodes_created=nodes_created,
    ).model_dump()


# ══════════════════════════════════════════════════════════════
# Standalone crystallization task
# ══════════════════════════════════════════════════════════════


@hatchet.task(
    name="crystallize_node",
    input_validator=CrystallizeInput,
    execution_timeout=timedelta(minutes=15),
    schedule_timeout=_schedule_timeout,
)
async def crystallize_task(input: CrystallizeInput, ctx: Context) -> dict:
    """Check and crystallize a parent node if it meets the threshold.

    Spawned by ``update_ancestry`` after setting a node's parent.
    Fire-and-forget — failure does not affect the node pipeline.
    """
    state = cast(WorkerState, ctx.lifespan)

    ctx.log(f"crystallize_task: parent_node_id={input.parent_node_id}")

    result = await HatchetPipeline(state, api_key=input.api_key).crystallize(input.parent_node_id)

    ctx.log(f"crystallize_task: done — crystallized={result.get('crystallized', False)}")
    return result


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

    ctx.log(
        f"edge_task: concept={input.concept}, node_id={input.node_id}, mode={input.edge_mode}, cross_type_pair={input.cross_type_pair}"
    )

    result = await HatchetPipeline(state, api_key=input.api_key).edges(
        node_id=input.node_id,
        mode=input.edge_mode,
        concept=input.concept,
        node_type=input.node_type,
        cross_type_pair=input.cross_type_pair,
    )

    ctx.log(
        f"edge_task: done — concept={input.concept}, mode={input.edge_mode}, "
        f"edges_created={result.get('edges_created', 0)}, edge_ids={result.get('edge_ids', [])}"
    )

    await flush_usage_to_db(state.write_session_factory, input.conversation_id, input.message_id, "edge_classification")

    return EdgeOutput(
        edge_ids=result.get("edge_ids", []),
        edges_created=result.get("edges_created", 0),
    ).model_dump()


# ══════════════════════════════════════════════════════════════
# Recalculate workflow — full refresh for an existing node
# ══════════════════════════════════════════════════════════════


@hatchet.task(
    name="recalculate_node",
    input_validator=RecalculateInput,
    execution_timeout=timedelta(hours=1),
    schedule_timeout=_schedule_timeout,
)
async def recalculate_task(input: RecalculateInput, ctx: Context) -> dict:
    """Full recalculation of an existing node.

    Runs the same pipeline phases as the node DAG but for an existing
    node: dimensions → definition → edges → ancestry → crystallization.
    Also recalculates the dialectic pair partner if requested.
    """
    import uuid as _uuid

    from kt_db.keys import key_to_uuid
    from kt_db.repositories.write_nodes import WriteNodeRepository

    state = cast(WorkerState, ctx.lifespan)
    if state.write_session_factory is None:
        raise RuntimeError("recalculate_task: write_session_factory is required")

    pipeline = HatchetPipeline(state, api_key=input.api_key)
    nid = input.node_id

    ctx.log(f"recalculate: starting node_id={nid}")

    # Phase 2.5: enrich from fact pool
    enrich_result = await pipeline.enrich(nid)
    new_facts = enrich_result.get("new_facts_linked", 0)
    ctx.log(f"recalculate: pool enrichment done, {new_facts} new facts linked")

    # Phase 3: dimensions
    dim_result = await pipeline.dimensions(nid)
    node_type: str = dim_result.get("node_type", "concept")
    recalc_dims = dim_result.get("dimensions_created", 0)
    recalc_facts = dim_result.get("fact_count", 0)
    ctx.log(f"recalculate: dimensions done, node_type={node_type}, dims={recalc_dims}, facts={recalc_facts}")
    if recalc_facts > 0 and recalc_dims == 0:
        ctx.log(f"recalculate: WARNING — node {nid} has {recalc_facts} facts but 0 dimensions")

    # Phase 3.5: definition
    await pipeline.definition(nid)
    ctx.log("recalculate: definition done")

    # Load node concept for edge dispatch + ancestry — from write-db
    write_session = state.write_session_factory()
    try:
        write_node_repo = WriteNodeRepository(write_session)
        wn = await write_node_repo.get_by_uuid(_uuid.UUID(nid))
        if wn is None:
            raise RuntimeError(f"recalculate_task: node {nid} not found in write-db")
        node_concept = wn.concept
        node_type_resolved = wn.node_type
        node_metadata: dict | None = wn.metadata_
        node_skip_ontology = bool((wn.metadata_ or {}).get("skip_ontology"))
        wn_parent_key: str | None = wn.parent_key
    finally:
        await write_session.close()

    # Phase 4: edges — single candidate-based resolution pass
    scope_id = f"recalculate-{nid[:8]}"
    await edge_task.aio_run_many(
        [
            edge_task.create_bulk_run_item(
                input=UpdateEdgesInput(
                    node_id=nid,
                    edge_mode="candidates",
                    concept=node_concept,
                    node_type=node_type_resolved,
                    scope_id=scope_id,
                    message_id=scope_id,
                    conversation_id=scope_id,
                    api_key=input.api_key,
                ),
            )
        ]
    )
    ctx.log("recalculate: edge classification done, DB writes dispatched async")

    # Phase 4.5: refresh justifications on existing edges touching this node
    try:
        refresh_result = await pipeline.refresh_edge_justifications(
            nid,
            node_concept,
            node_type_resolved,
        )
        edges_refreshed = refresh_result.get("edges_refreshed", 0)
        if edges_refreshed:
            ctx.log(f"recalculate: refreshed {edges_refreshed} existing edge justifications")
    except Exception:
        logger.warning(
            "recalculate: edge justification refresh failed for %s",
            nid,
            exc_info=True,
        )

    # Phase 5: ancestry (skip for stub nodes created by the ancestry pipeline)
    if node_skip_ontology:
        ctx.log("recalculate: skipping ancestry — node has skip_ontology metadata")
    else:
        await pipeline.ancestry(
            node_id=nid,
            node_name=node_concept,
            node_type=node_type_resolved,
        )
        ctx.log("recalculate: ancestry done")

    # Crystallization check on parent — resolve from write-db
    parent_id = key_to_uuid(wn_parent_key) if wn_parent_key else None

    if parent_id:
        await pipeline.crystallize(str(parent_id))
        ctx.log(f"recalculate: crystallization checked for parent {parent_id}")

    # Increment update count via write-db
    write_session = state.write_session_factory()
    try:
        write_node_repo = WriteNodeRepository(write_session)
        await write_node_repo.increment_update_count(write_node_repo.node_key(node_type_resolved, node_concept))
        await write_session.commit()
    finally:
        await write_session.close()

    ctx.log(f"recalculate: completed node_id={nid}")

    # Dialectic pair recalculation
    if input.recalculate_pair:
        pair_id_str = (node_metadata or {}).get("dialectic_pair_id")

        if pair_id_str:
            ctx.log(f"recalculate: triggering pair recalculation for {pair_id_str}")
            try:
                await recalculate_task.aio_run(
                    RecalculateInput(
                        node_id=str(pair_id_str),
                        recalculate_pair=False,  # Prevent infinite loop
                    ),
                )
            except Exception:
                logger.warning(
                    "Failed to recalculate dialectic pair %s",
                    pair_id_str,
                    exc_info=True,
                )

    return {"node_id": nid, "status": "completed"}
