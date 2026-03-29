"""Hatchet workflows for regenerating failed syntheses.

regenerate_synthesis_wf — Re-runs the synthesizer agent on an existing
    failed synthesis node, updating it in-place.

recombine_supersynthesis_wf — Re-runs only the combine step of the
    super-synthesizer on an existing super-synthesis node.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import timedelta
from typing import Any, cast

from hatchet_sdk import Context

from kt_hatchet.client import get_hatchet
from kt_hatchet.lifespan import WorkerState
from kt_hatchet.models import (
    RecombineSuperSynthesisInput,
    RegenerateSynthesisInput,
    SuperSynthesizerOutput,
    SynthesizerInput,
    SynthesizerOutput,
)

logger = logging.getLogger(__name__)

hatchet = get_hatchet()

# ── Regenerate a single synthesis ──────────────────────────────────

regenerate_synthesis_wf = hatchet.workflow(
    name="regenerate_synthesis_wf",
    input_validator=RegenerateSynthesisInput,
)


@regenerate_synthesis_wf.task(execution_timeout=timedelta(minutes=30))
async def run_regenerate_synthesis(input: RegenerateSynthesisInput, ctx: Context) -> dict[str, Any]:
    """Re-run the synthesizer agent on a failed synthesis node."""
    worker_state = cast(WorkerState, ctx.lifespan)

    from kt_graph.engine import GraphEngine
    from kt_worker_synthesis.workflows._helpers import (
        process_and_store_synthesis,
        run_synthesis_agent,
        store_synthesis_error,
    )

    node_id = uuid.UUID(input.node_id)

    async with worker_state.session_factory() as session:
        write_session = None
        if worker_state.write_session_factory is not None:
            write_session = worker_state.write_session_factory()

        try:
            graph_engine = GraphEngine(
                session,
                worker_state.embedding_service,
                write_session=write_session,
                qdrant_client=worker_state.qdrant_client,
            )

            # Read stored synthesis input from metadata
            node = await graph_engine.get_node(node_id)
            if not node:
                logger.error("Node %s not found for regeneration", input.node_id)
                return SynthesizerOutput(synthesis_node_id=input.node_id).model_dump()

            meta = node.metadata_ or {}
            synthesis_input_data = meta.get("synthesis_input")
            if not synthesis_input_data:
                logger.error("No synthesis_input in metadata for node %s", input.node_id)
                return SynthesizerOutput(synthesis_node_id=input.node_id).model_dump()

            synth_input = SynthesizerInput(**synthesis_input_data)
            parent_supersynthesis_id = meta.get("parent_supersynthesis_id")

            async def emit_event(event_type: str, **data: Any) -> None:
                try:
                    await ctx.aio_put_stream(json.dumps({"type": event_type, **data}))
                except Exception:
                    pass

            await emit_event("synthesis_regenerate_started", node_id=input.node_id)

            result = await run_synthesis_agent(
                input=synth_input,
                model_gateway=worker_state.model_gateway,
                graph_engine=graph_engine,
                provider_registry=worker_state.provider_registry,
                embedding_service=worker_state.embedding_service,
                session=session,
                session_factory=worker_state.session_factory,
                write_session_factory=worker_state.write_session_factory,
                qdrant_client=worker_state.qdrant_client,
                emit_event=emit_event,
            )

            if not result.synthesis_text:
                logger.warning("Regeneration failed — agent still produced no text for %s", input.node_id)

                if write_session:
                    await store_synthesis_error(
                        synthesis_node_id=node_id,
                        write_session=write_session,
                        synthesis_input_data=synthesis_input_data,
                        error_message="Regeneration failed — agent still produced no text",
                        parent_supersynthesis_id=parent_supersynthesis_id,
                        visibility=synth_input.visibility,
                        creator_id=synth_input.creator_id,
                    )

                await emit_event(
                    "synthesis_failed",
                    synthesis_node_id=input.node_id,
                    error="Regeneration failed — agent still produced no text",
                )
                return SynthesizerOutput(synthesis_node_id=input.node_id).model_dump()

            # Success: update the existing node in-place
            doc = await process_and_store_synthesis(
                synthesis_text=result.synthesis_text,
                synthesis_node_id=node_id,
                nodes_visited=result.nodes_visited,
                graph_engine=graph_engine,
                embedding_service=worker_state.embedding_service,
                qdrant_client=worker_state.qdrant_client,
                write_session=write_session,
                synthesis_input_data=synthesis_input_data,
                parent_supersynthesis_id=parent_supersynthesis_id,
            )

            stats = doc.get("stats", {})

            await emit_event(
                "synthesis_completed",
                synthesis_node_id=input.node_id,
                **stats,
            )

            # If this is a sub-synthesis with a parent, dispatch recombine
            if parent_supersynthesis_id:
                try:
                    from kt_hatchet.client import dispatch_workflow

                    await dispatch_workflow(
                        "recombine_supersynthesis_wf",
                        {"node_id": parent_supersynthesis_id},
                    )
                    logger.info(
                        "Dispatched recombine for parent super-synthesis %s",
                        parent_supersynthesis_id,
                    )
                except Exception:
                    logger.warning(
                        "Failed to dispatch recombine for parent %s",
                        parent_supersynthesis_id,
                        exc_info=True,
                    )

            return SynthesizerOutput(
                synthesis_node_id=input.node_id,
                sentences_count=stats.get("sentences_count", 0),
                facts_linked=stats.get("facts_linked", 0),
                nodes_referenced=stats.get("nodes_referenced", 0),
            ).model_dump()

        finally:
            if write_session is not None:
                await write_session.close()


# ── Recombine a super-synthesis ────────────────────────────────────

recombine_supersynthesis_wf = hatchet.workflow(
    name="recombine_supersynthesis_wf",
    input_validator=RecombineSuperSynthesisInput,
)


@recombine_supersynthesis_wf.task(execution_timeout=timedelta(minutes=30))
async def run_recombine(input: RecombineSuperSynthesisInput, ctx: Context) -> dict[str, Any]:
    """Re-run the combine step on an existing super-synthesis node."""
    worker_state = cast(WorkerState, ctx.lifespan)

    from sqlalchemy import select as sa_select
    from sqlalchemy import update

    from kt_db.write_models import WriteNode
    from kt_graph.engine import GraphEngine
    from kt_worker_synthesis.workflows._helpers import (
        run_super_synthesis_combine,
        store_synthesis_error,
    )

    node_id = uuid.UUID(input.node_id)

    async with worker_state.session_factory() as session:
        write_session = None
        if worker_state.write_session_factory is not None:
            write_session = worker_state.write_session_factory()

        try:
            graph_engine = GraphEngine(
                session,
                worker_state.embedding_service,
                write_session=write_session,
                qdrant_client=worker_state.qdrant_client,
            )

            # Read stored metadata
            node = await graph_engine.get_node(node_id)
            if not node:
                logger.error("Super-synthesis node %s not found", input.node_id)
                return SuperSynthesizerOutput(supersynthesis_node_id=input.node_id).model_dump()

            meta = node.metadata_ or {}
            doc = meta.get("synthesis_document", {})
            synthesis_node_ids = doc.get("sub_synthesis_ids", [])
            synthesis_input_data = meta.get("synthesis_input", {})
            topic = synthesis_input_data.get("topic", node.concept)

            if not synthesis_node_ids:
                logger.error("No sub_synthesis_ids found for super-synthesis %s", input.node_id)
                return SuperSynthesizerOutput(supersynthesis_node_id=input.node_id).model_dump()

            super_text = await run_super_synthesis_combine(
                topic=topic,
                synthesis_node_ids=synthesis_node_ids,
                graph_engine=graph_engine,
                model_gateway=worker_state.model_gateway,
                embedding_service=worker_state.embedding_service,
                session=session,
                session_factory=worker_state.session_factory,
                write_session_factory=worker_state.write_session_factory,
                qdrant_client=worker_state.qdrant_client,
                provider_registry=worker_state.provider_registry,
            )

            if not super_text:
                logger.warning("Recombine failed — agent produced no text for %s", input.node_id)

                if write_session:
                    await store_synthesis_error(
                        synthesis_node_id=node_id,
                        write_session=write_session,
                        synthesis_input_data=synthesis_input_data,
                        error_message="Recombine failed — agent produced no text",
                    )

                return SuperSynthesizerOutput(
                    supersynthesis_node_id=input.node_id,
                    sub_synthesis_node_ids=synthesis_node_ids,
                ).model_dump()

            # Success: update the existing super-synthesis node
            await graph_engine.set_node_definition(node_id, super_text)

            # Collect node names from sub-syntheses for text matching
            from kt_worker_synthesis.pipelines.document_processing import process_synthesis_document

            node_names: dict[str, list[str]] = {}
            for sid in synthesis_node_ids:
                try:
                    sub_node = await graph_engine.get_node(uuid.UUID(sid))
                    if sub_node and sub_node.metadata_:
                        sub_doc = sub_node.metadata_.get("synthesis_document", {})
                        for ref_node in sub_doc.get("referenced_nodes", []):
                            nid = ref_node.get("node_id", "")
                            concept = ref_node.get("concept", "unknown")
                            if nid:
                                node_names[nid] = [concept]
                except Exception:
                    pass

            new_doc = await process_synthesis_document(
                synthesis_text=super_text,
                embedding_service=worker_state.embedding_service,
                qdrant_client=worker_state.qdrant_client,
                node_names_and_aliases=node_names,
            )
            new_doc["sub_synthesis_ids"] = synthesis_node_ids

            if write_session:
                row = (
                    await write_session.execute(sa_select(WriteNode.metadata_).where(WriteNode.node_uuid == node_id))
                ).scalar_one_or_none()
                existing_meta = row if isinstance(row, dict) else {}
                existing_meta["synthesis_document"] = new_doc
                existing_meta.pop("synthesis_error", None)
                if synthesis_input_data:
                    existing_meta["synthesis_input"] = synthesis_input_data
                await write_session.execute(
                    update(WriteNode).where(WriteNode.node_uuid == node_id).values(metadata_=existing_meta)
                )
                await write_session.commit()

            stats = new_doc.get("stats", {})

            return SuperSynthesizerOutput(
                supersynthesis_node_id=input.node_id,
                sub_synthesis_node_ids=synthesis_node_ids,
                total_sentences=stats.get("sentences_count", 0),
                total_facts_linked=stats.get("facts_linked", 0),
            ).model_dump()

        finally:
            if write_session is not None:
                await write_session.close()
