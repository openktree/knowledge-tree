"""Unified rebuild_node task — replaces enrich_node and recalculate_task.

Modes:
  - incremental (default): sync seed facts -> NEW dimension batches (respecting
    existing definitive dims) -> definition -> resolve NEW edges from candidates
    -> ancestry. Used for first-time enrichment and periodic auto-build updates.
  - full: sync seed facts -> delete ALL dimensions -> regenerate ALL ->
    definition -> full edge resolution + refresh -> ancestry + crystallization
    + dialectic pair. Used for manual "rebuild" from the UI.

Scope:
  - all (default): dimensions + definition + edges + ancestry
  - dimensions: dimensions + definition only
  - edges: edge resolution + refresh only
"""

from __future__ import annotations

import json
import logging
import uuid as _uuid
from datetime import timedelta
from typing import cast

from hatchet_sdk import ConcurrencyExpression, ConcurrencyLimitStrategy, Context

from kt_config.settings import get_settings
from kt_hatchet.client import get_hatchet
from kt_hatchet.lifespan import WorkerState
from kt_hatchet.models import RebuildNodeInput, RebuildNodeOutput, UpdateEdgesInput

logger = logging.getLogger(__name__)

hatchet = get_hatchet()
_schedule_timeout = timedelta(minutes=get_settings().hatchet_schedule_timeout_minutes)

rebuild_node_task = hatchet.workflow(
    name="rebuild_node",
    input_validator=RebuildNodeInput,
    concurrency=ConcurrencyExpression(
        expression="input.node_id",
        max_runs=1,
        limit_strategy=ConcurrencyLimitStrategy.GROUP_ROUND_ROBIN,
    ),
)


@rebuild_node_task.task(
    execution_timeout=timedelta(hours=1),
    schedule_timeout=_schedule_timeout,
)
async def rebuild_node(input: RebuildNodeInput, ctx: Context) -> dict:
    """Unified node rebuild: incremental or full, scoped to all/dimensions/edges."""
    from kt_db.keys import key_to_uuid
    from kt_db.repositories.write_nodes import WriteNodeRepository
    from kt_db.repositories.write_seeds import WriteSeedRepository

    state = cast(WorkerState, ctx.lifespan)
    settings = get_settings()

    if state.write_session_factory is None:
        raise RuntimeError("rebuild_node: write_session_factory is required")

    pipeline = _make_pipeline(state, input.api_key)
    nid = input.node_id
    mode = input.mode
    scope = input.scope

    ctx.log(f"rebuild_node: start node_id={nid} mode={mode} scope={scope}")

    # ── Load node from write-db ───────────────────────────────────
    async with state.write_session_factory() as ws:
        wn = await WriteNodeRepository(ws).get_by_uuid(_uuid.UUID(nid))
    if wn is None:
        ctx.log(f"rebuild_node: node {nid} not found in write-db")
        return RebuildNodeOutput(node_id=nid, status="error").model_dump()

    node_key = wn.key
    node_concept = wn.concept
    node_type = wn.node_type

    # ── Incremental: skip stub/partial with too few facts ─────────
    if mode == "incremental":
        async with state.write_session_factory() as ws:
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
                fact_count = len(wn.fact_ids or [])

        min_facts = settings.enrichment_min_facts_for_dimensions
        if fact_count < min_facts and wn.enrichment_status in ("stub", "partial"):
            # Not enough facts yet — mark as partial
            async with state.write_session_factory() as ws:
                from sqlalchemy import text

                await ws.execute(
                    text("UPDATE write_nodes SET enrichment_status = 'partial', updated_at = NOW() WHERE key = :key"),
                    {"key": node_key},
                )
                await ws.commit()
            ctx.log(f"rebuild_node: node '{node_concept}' has {fact_count} facts (need {min_facts}) — marked partial")
            return RebuildNodeOutput(
                node_id=nid,
                status="skipped",
                mode=mode,
                scope=scope,
                fact_count=fact_count,
            ).model_dump()

    # ── Phase 2.5: sync seed facts ────────────────────────────────
    enrich_result = await pipeline.enrich(nid)
    new_facts = enrich_result.get("new_facts_linked", 0)
    ctx.log(f"rebuild_node: pool enrichment done, {new_facts} new facts linked")

    dims_created = 0
    total_facts = 0
    edges_resolved = 0

    # ── Dimensions ────────────────────────────────────────────────
    if scope in ("all", "dimensions"):
        if mode == "full":
            dim_result = await pipeline.full_dimensions(nid)
        else:
            dim_result = await pipeline.dimensions(nid)
        dims_created = dim_result.get("dimensions_created", 0)
        total_facts = dim_result.get("fact_count", 0)
        node_type = dim_result.get("node_type", node_type)
        ctx.log(f"rebuild_node: dimensions done — {dims_created} dims from {total_facts} facts")

    # ── Definition ────────────────────────────────────────────────
    if scope in ("all", "dimensions"):
        await pipeline.definition(nid)
        ctx.log("rebuild_node: definition done")

    # ── Edges ─────────────────────────────────────────────────────
    if scope in ("all", "edges"):
        from kt_worker_nodes.workflows.node_pipeline import edge_task

        scope_id = f"rebuild-{nid[:8]}"
        await edge_task.aio_run_many(
            [
                edge_task.create_bulk_run_item(
                    input=UpdateEdgesInput(
                        node_id=nid,
                        edge_mode="candidates",
                        concept=node_concept,
                        node_type=node_type,
                        scope_id=scope_id,
                        message_id=scope_id,
                        conversation_id=scope_id,
                        api_key=input.api_key,
                    ),
                )
            ]
        )
        edges_resolved = 1  # At least one edge resolution pass ran
        ctx.log("rebuild_node: edge resolution done")

        # Full mode: also refresh justifications on existing edges
        if mode == "full":
            try:
                refresh_result = await pipeline.refresh_edge_justifications(
                    nid,
                    node_concept,
                    node_type,
                )
                edges_refreshed = refresh_result.get("edges_refreshed", 0)
                if edges_refreshed:
                    ctx.log(f"rebuild_node: refreshed {edges_refreshed} existing edge justifications")
            except Exception:
                logger.warning("rebuild_node: edge justification refresh failed for %s", nid, exc_info=True)

    # ── Ancestry ──────────────────────────────────────────────────
    if scope == "all":
        node_skip_ontology = bool((wn.metadata_ or {}).get("skip_ontology"))
        if node_skip_ontology:
            ctx.log("rebuild_node: skipping ancestry — node has skip_ontology metadata")
        else:
            await pipeline.ancestry(
                node_id=nid,
                node_name=node_concept,
                node_type=node_type,
            )
            ctx.log("rebuild_node: ancestry done")

        # Crystallization check on parent (full mode only)
        if mode == "full" and wn.parent_key:
            parent_id = key_to_uuid(wn.parent_key)
            await pipeline.crystallize(str(parent_id))
            ctx.log(f"rebuild_node: crystallization checked for parent {parent_id}")

    # ── Finalize: enrichment_status + update_count ────────────────
    async with state.write_session_factory() as ws:
        from sqlalchemy import text

        await ws.execute(
            text("UPDATE write_nodes SET enrichment_status = 'enriched', updated_at = NOW() WHERE key = :key"),
            {"key": node_key},
        )
        write_node_repo = WriteNodeRepository(ws)
        await write_node_repo.increment_update_count(node_key)
        await ws.commit()
    ctx.log("rebuild_node: finalized — enrichment_status='enriched'")

    # ── Dialectic pair (full + all only) ──────────────────────────
    if mode == "full" and scope == "all" and input.recalculate_pair:
        pair_id_str = (wn.metadata_ or {}).get("dialectic_pair_id")
        if pair_id_str:
            ctx.log(f"rebuild_node: triggering pair rebuild for {pair_id_str}")
            try:
                await rebuild_node_task.aio_run(
                    RebuildNodeInput(
                        node_id=str(pair_id_str),
                        mode="full",
                        scope="all",
                        recalculate_pair=False,
                        api_key=input.api_key,
                    ),
                )
            except Exception:
                logger.warning("Failed to rebuild dialectic pair %s", pair_id_str, exc_info=True)

    ctx.log(f"rebuild_node: completed node_id={nid}")

    try:
        await ctx.aio_put_stream(
            json.dumps(
                {
                    "type": "rebuild_complete",
                    "node_id": nid,
                    "mode": mode,
                    "scope": scope,
                    "dimensions_created": dims_created,
                    "fact_count": total_facts,
                }
            )
        )
    except Exception:
        pass

    return RebuildNodeOutput(
        node_id=nid,
        status="completed",
        mode=mode,
        scope=scope,
        dimensions_created=dims_created,
        edges_resolved=edges_resolved,
        fact_count=total_facts,
    ).model_dump()


def _make_pipeline(state: WorkerState, api_key: str | None):  # type: ignore[no-untyped-def]
    from kt_worker_nodes.hatchet_pipeline import HatchetPipeline

    return HatchetPipeline(state, api_key=api_key)
