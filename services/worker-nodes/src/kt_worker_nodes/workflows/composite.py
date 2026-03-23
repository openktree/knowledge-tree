"""Composite node workflows — build and regenerate synthesis/perspective nodes.

Standalone Hatchet tasks (not DAG workflows) for composite node lifecycle:

- **build_composite_task**: Check for merge candidate → create/merge node →
  run agent → set definition → create draws_from edges → save version.
- **regenerate_composite_task**: On-demand regeneration → run agent →
  save new version → update default version.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import timedelta
from typing import cast

from hatchet_sdk import Context

from kt_config.settings import get_settings
from kt_config.types import COMPOSITE_NODE_TYPES
from kt_hatchet.client import get_hatchet
from kt_hatchet.lifespan import WorkerState
from kt_hatchet.models import (
    BuildCompositeInput,
    BuildCompositeOutput,
    RegenerateCompositeInput,
    RegenerateCompositeOutput,
)

logger = logging.getLogger(__name__)

hatchet = get_hatchet()
_schedule_timeout = timedelta(minutes=get_settings().hatchet_schedule_timeout_minutes)


# ══════════════════════════════════════════════════════════════
# Build composite node
# ══════════════════════════════════════════════════════════════


@hatchet.task(
    name="build_composite",
    input_validator=BuildCompositeInput,
    execution_timeout=timedelta(minutes=15),
    schedule_timeout=_schedule_timeout,
)
async def build_composite_task(input: BuildCompositeInput, ctx: Context) -> dict:
    """Build a composite node (synthesis or perspective).

    1. Check for a mergeable existing composite.
    2. Create or merge the node.
    3. Run the appropriate agent (synthesis or perspective).
    4. Set the definition, create draws_from edges, save version.
    """
    state = cast(WorkerState, ctx.lifespan)

    async def emit(event_type: str, payload: dict) -> None:
        try:
            await ctx.aio_put_stream(json.dumps({"type": event_type, **payload}))
        except Exception:
            logger.warning("Failed to stream event %s", event_type, exc_info=True)

    if input.node_type not in COMPOSITE_NODE_TYPES:
        raise ValueError(f"build_composite: invalid node_type={input.node_type}")

    ctx.log(f"build_composite: type={input.node_type}, concept={input.concept!r}")

    await emit("pipeline_phase", {
        "scope_id": input.scope_id or "composite",
        "phase": "composite_build",
        "status": "started",
        "node_type": input.node_type,
        "concept": input.concept,
    })

    from kt_agents_core.state import AgentContext
    from kt_db.keys import make_node_key
    from kt_db.repositories.write_node_versions import WriteNodeVersionRepository
    from kt_graph.engine import GraphEngine
    from kt_worker_nodes.pipelines.composite.merge import find_mergeable_composite

    node_id: str | None = None
    merged_into: str | None = None
    is_new = True
    draws_from_edge_ids: list[str] = []
    version_number = 1

    async with state.session_factory() as session:
        write_session = state.write_session_factory() if state.write_session_factory else None
        try:
            graph_engine = GraphEngine(
                session,
                state.embedding_service,
                qdrant_client=state.qdrant_client,
                write_session=write_session,
            )
            agent_ctx = AgentContext(
                graph_engine=graph_engine,
                provider_registry=state.provider_registry,
                model_gateway=state.model_gateway,
                embedding_service=state.embedding_service,
                session=session,
                session_factory=state.session_factory,
                content_fetcher=state.content_fetcher,
                write_session_factory=state.write_session_factory,
                qdrant_client=state.qdrant_client,
            )

            source_ids = input.source_node_ids

            # ── Embed concept for merge check and node creation ───────
            embedding: list[float] | None = None
            if state.embedding_service:
                try:
                    embedding = await state.embedding_service.embed_text(input.concept)
                except Exception:
                    logger.warning("Failed to embed concept for composite", exc_info=True)

            # ── Check for merge candidate ─────────────────────────────
            merge_target = await find_mergeable_composite(
                graph_engine,
                node_type=input.node_type,
                source_node_ids=set(source_ids),
                concept=input.concept,
                embedding=embedding,
            )

            if merge_target:
                # Merge: expand source set and regenerate
                node_id = merge_target
                merged_into = merge_target
                is_new = False
                ctx.log(f"build_composite: merging into existing {merge_target}")

                # Get existing source nodes from draws_from edges
                existing_sources = await _get_source_node_ids(graph_engine, uuid.UUID(merge_target))
                source_ids = list(set(source_ids) | set(existing_sources))
            else:
                # Create new composite node
                node = await graph_engine.create_node(
                    concept=input.concept,
                    embedding=embedding,
                    node_type=input.node_type,
                    metadata_=input.metadata,
                )
                node_id = str(node.id)
                ctx.log(f"build_composite: created node {node_id}")

            # ── Run agent ─────────────────────────────────────────────
            definition = await _run_composite_agent(
                agent_ctx=agent_ctx,
                node_type=input.node_type,
                concept=input.concept,
                source_node_ids=source_ids,
                query_context=input.query_context,
                parent_concept=input.parent_concept,
            )

            # ── Set definition on node ────────────────────────────────
            if node_id and definition:
                await graph_engine.set_node_definition(
                    uuid.UUID(node_id),
                    definition,
                    source=state.model_gateway.orchestrator_model,
                )

            # ── Create draws_from edges ───────────────────────────────
            if node_id:
                draws_from_edge_ids = await _create_draws_from_edges(
                    graph_engine, uuid.UUID(node_id), source_ids,
                )

            # ── Save version ──────────────────────────────────────────
            if node_id and write_session:
                node_key = make_node_key(input.node_type, input.concept)
                version_repo = WriteNodeVersionRepository(write_session)
                version_number = await version_repo.next_version_number(node_key)
                await version_repo.create_version(
                    node_key=node_key,
                    version_number=version_number,
                    snapshot={
                        "definition": definition,
                        "source_node_ids": source_ids,
                        "source_node_count": len(source_ids),
                        "model_id": state.model_gateway.orchestrator_model,
                        "query_context": input.query_context,
                    },
                    source_node_count=len(source_ids),
                )
                default = await version_repo.update_default(node_key)
                # If the new default differs, update the node definition
                if default and default.snapshot and default.snapshot.get("definition") != definition:
                    await graph_engine.set_node_definition(
                        uuid.UUID(node_id),
                        default.snapshot["definition"],
                        source=default.snapshot.get("model_id", "synthesized"),
                    )

                await write_session.commit()
            await session.commit()

        finally:
            if write_session:
                await write_session.close()

    await emit("pipeline_phase", {
        "scope_id": input.scope_id or "composite",
        "phase": "composite_build",
        "status": "completed",
        "node_id": node_id,
    })

    ctx.log(
        f"build_composite: done node_id={node_id}, merged={merged_into}, "
        f"version={version_number}, edges={len(draws_from_edge_ids)}"
    )

    return BuildCompositeOutput(
        node_id=node_id,
        merged_into=merged_into,
        version_number=version_number,
        is_new=is_new,
        draws_from_edge_ids=draws_from_edge_ids,
    ).model_dump()


# ══════════════════════════════════════════════════════════════
# Regenerate composite node (on-demand)
# ══════════════════════════════════════════════════════════════


@hatchet.task(
    name="regenerate_composite",
    input_validator=RegenerateCompositeInput,
    execution_timeout=timedelta(minutes=15),
    schedule_timeout=_schedule_timeout,
)
async def regenerate_composite_task(input: RegenerateCompositeInput, ctx: Context) -> dict:
    """On-demand regeneration of a composite node.

    1. Load existing node and source nodes.
    2. Re-run the agent with current data.
    3. Save new version; update default if highest source_node_count.
    """
    state = cast(WorkerState, ctx.lifespan)
    nid = uuid.UUID(input.node_id)

    ctx.log(f"regenerate_composite: node_id={input.node_id}")

    from kt_agents_core.state import AgentContext
    from kt_db.keys import make_node_key
    from kt_db.repositories.write_node_versions import WriteNodeVersionRepository
    from kt_graph.engine import GraphEngine

    version_number = 1
    source_node_count = 0
    is_default = False

    async with state.session_factory() as session:
        write_session = state.write_session_factory() if state.write_session_factory else None
        try:
            graph_engine = GraphEngine(
                session,
                state.embedding_service,
                qdrant_client=state.qdrant_client,
                write_session=write_session,
            )

            # Load the node
            node = await graph_engine.get_node(nid)
            if node is None:
                raise ValueError(f"Node {input.node_id} not found")

            if node.node_type not in COMPOSITE_NODE_TYPES:
                raise ValueError(f"Node {input.node_id} is type {node.node_type}, not composite")

            # Get current source nodes
            source_ids = await _get_source_node_ids(graph_engine, nid)
            source_node_count = len(source_ids)

            agent_ctx = AgentContext(
                graph_engine=graph_engine,
                provider_registry=state.provider_registry,
                model_gateway=state.model_gateway,
                embedding_service=state.embedding_service,
                session=session,
                session_factory=state.session_factory,
                content_fetcher=state.content_fetcher,
                write_session_factory=state.write_session_factory,
                qdrant_client=state.qdrant_client,
            )

            # Run agent
            definition = await _run_composite_agent(
                agent_ctx=agent_ctx,
                node_type=node.node_type,
                concept=node.concept,
                source_node_ids=source_ids,
            )

            # Save version
            if write_session:
                node_key = make_node_key(node.node_type, node.concept)
                version_repo = WriteNodeVersionRepository(write_session)
                version_number = await version_repo.next_version_number(node_key)
                await version_repo.create_version(
                    node_key=node_key,
                    version_number=version_number,
                    snapshot={
                        "definition": definition,
                        "source_node_ids": source_ids,
                        "source_node_count": source_node_count,
                        "model_id": state.model_gateway.orchestrator_model,
                    },
                    source_node_count=source_node_count,
                )
                default = await version_repo.update_default(node_key)
                is_default = default is not None and default.version_number == version_number

                # Update node definition to default version
                if default and default.snapshot:
                    await graph_engine.set_node_definition(
                        nid,
                        default.snapshot["definition"],
                        source=default.snapshot.get("model_id", "synthesized"),
                    )

                await write_session.commit()
            await session.commit()

        finally:
            if write_session:
                await write_session.close()

    ctx.log(
        f"regenerate_composite: done version={version_number}, "
        f"source_count={source_node_count}, is_default={is_default}"
    )

    return RegenerateCompositeOutput(
        node_id=input.node_id,
        version_number=version_number,
        source_node_count=source_node_count,
        is_default=is_default,
    ).model_dump()


# ── Internal helpers ──────────────────────────────────────────────


async def _run_composite_agent(
    agent_ctx: "AgentContext",
    node_type: str,
    concept: str,
    source_node_ids: list[str],
    query_context: str = "",
    parent_concept: str = "",
) -> str:
    """Run the appropriate composite agent and return the definition text."""
    if node_type == "synthesis":
        from kt_worker_nodes.pipelines.composite.synthesis_agent import build_synthesis_impl
        result = await build_synthesis_impl(
            agent_ctx,
            source_node_ids=source_node_ids,
            concept=concept,
            query_context=query_context,
        )
    elif node_type == "perspective":
        from kt_worker_nodes.pipelines.composite.perspective_agent import build_perspective_impl
        result = await build_perspective_impl(
            agent_ctx,
            source_node_ids=source_node_ids,
            claim=concept,
            parent_concept=parent_concept,
        )
    else:
        raise ValueError(f"Unknown composite node_type: {node_type}")

    return result.get("definition", "")


async def _get_source_node_ids(
    graph_engine: "GraphEngine",
    node_id: uuid.UUID,
) -> list[str]:
    """Get source node IDs for a composite node from draws_from edges."""
    edges = await graph_engine.get_edges(node_id, direction="both")
    source_ids: list[str] = []
    for edge in edges:
        if edge.relationship_type == "draws_from":
            # draws_from is directed: source=composite, target=source_node
            if edge.source_node_id == node_id:
                source_ids.append(str(edge.target_node_id))
            else:
                source_ids.append(str(edge.source_node_id))
    return source_ids


async def _create_draws_from_edges(
    graph_engine: "GraphEngine",
    composite_node_id: uuid.UUID,
    source_node_ids: list[str],
) -> list[str]:
    """Create draws_from edges from composite node to each source node."""
    edge_ids: list[str] = []
    for src_id_str in source_node_ids:
        try:
            src_id = uuid.UUID(src_id_str)
            edge = await graph_engine.create_edge(
                source_id=composite_node_id,
                target_id=src_id,
                rel_type="draws_from",
                weight=1.0,
                justification=f"Composite node draws from source node",
            )
            if edge is not None:
                edge_ids.append(str(edge.id))
        except Exception:
            logger.warning(
                "Failed to create draws_from edge %s → %s",
                composite_node_id, src_id_str, exc_info=True,
            )
    return edge_ids
