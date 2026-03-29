"""Hatchet workflow for the Synthesizer Agent.

Dispatched via API to create a new synthesis document:
1. Creates a synthesis node
2. Runs the SynthesizerAgent to navigate the graph and produce text
3. Runs the document processing pipeline (split, embed, link)
"""

from __future__ import annotations

import json
import logging
from datetime import timedelta
from typing import Any, cast

from hatchet_sdk import Context

from kt_hatchet.client import get_hatchet
from kt_hatchet.lifespan import WorkerState
from kt_hatchet.models import SynthesizerInput, SynthesizerOutput

logger = logging.getLogger(__name__)

hatchet = get_hatchet()

synthesizer_wf = hatchet.workflow(name="synthesizer_wf", input_validator=SynthesizerInput)


@synthesizer_wf.task(execution_timeout=timedelta(minutes=30))
async def run_synthesizer(input: SynthesizerInput, ctx: Context) -> dict[str, Any]:
    """Run the full synthesis pipeline."""
    worker_state = cast(WorkerState, ctx.lifespan)

    from kt_graph.engine import GraphEngine
    from kt_worker_synthesis.workflows._helpers import (
        process_and_store_synthesis,
        run_synthesis_agent,
        store_synthesis_error,
    )

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

            async def emit_event(event_type: str, **data: Any) -> None:
                try:
                    await ctx.aio_put_stream(json.dumps({"type": event_type, **data}))
                except Exception:
                    pass

            result = await run_synthesis_agent(
                input=input,
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

            # Create synthesis node — append timestamp for unique key
            from datetime import UTC, datetime

            ts = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
            concept = f"{input.topic or 'Synthesis'} [{ts}]"
            node = await graph_engine.create_node(
                concept=concept,
                node_type="synthesis",
            )
            synthesis_node_id = node.id

            # Set visibility and creator via write-db
            if write_session:
                from sqlalchemy import update

                from kt_db.write_models import WriteNode

                await write_session.execute(
                    update(WriteNode)
                    .where(WriteNode.node_uuid == synthesis_node_id)
                    .values(
                        visibility=input.visibility,
                        creator_id=input.creator_id,
                    )
                )
                await write_session.flush()

            synthesis_input_data = input.model_dump()

            if not result.synthesis_text:
                logger.warning("Synthesizer agent ended without producing text")

                if write_session:
                    await store_synthesis_error(
                        synthesis_node_id=synthesis_node_id,
                        write_session=write_session,
                        synthesis_input_data=synthesis_input_data,
                        visibility=input.visibility,
                        creator_id=input.creator_id,
                    )

                await emit_event(
                    "synthesis_failed",
                    synthesis_node_id=str(synthesis_node_id),
                    error="Synthesis agent ended without producing text",
                )

                return SynthesizerOutput(
                    synthesis_node_id=str(synthesis_node_id),
                ).model_dump()

            # Success path: process document and store
            doc = await process_and_store_synthesis(
                synthesis_text=result.synthesis_text,
                synthesis_node_id=synthesis_node_id,
                nodes_visited=result.nodes_visited,
                graph_engine=graph_engine,
                embedding_service=worker_state.embedding_service,
                qdrant_client=worker_state.qdrant_client,
                write_session=write_session,
                synthesis_input_data=synthesis_input_data,
            )

            stats = doc.get("stats", {})

            await emit_event(
                "synthesis_completed",
                synthesis_node_id=str(synthesis_node_id),
                **stats,
            )

            output = SynthesizerOutput(
                synthesis_node_id=str(synthesis_node_id),
                sentences_count=stats.get("sentences_count", 0),
                facts_linked=stats.get("facts_linked", 0),
                nodes_referenced=stats.get("nodes_referenced", 0),
            )
            return output.model_dump()

        finally:
            if write_session is not None:
                await write_session.close()
