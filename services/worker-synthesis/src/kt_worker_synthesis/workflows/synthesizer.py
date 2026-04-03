"""Hatchet workflow for the Synthesizer Agent.

Dispatched via API to create a new synthesis document:
1. Creates a synthesis node
2. Runs the SynthesizerAgent to navigate the graph and produce text
3. Runs the document processing pipeline (split, embed, link)
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
from kt_hatchet.models import SynthesizerInput, SynthesizerOutput

logger = logging.getLogger(__name__)

hatchet = get_hatchet()

synthesizer_wf = hatchet.workflow(name="synthesizer_wf", input_validator=SynthesizerInput)


@synthesizer_wf.task(execution_timeout=timedelta(minutes=30))
async def run_synthesizer(input: SynthesizerInput, ctx: Context) -> dict[str, Any]:
    """Run the full synthesis pipeline."""
    worker_state = cast(WorkerState, ctx.lifespan)

    from langchain_core.messages import HumanMessage, SystemMessage

    from kt_agents_core.state import AgentContext
    from kt_graph.read_engine import ReadGraphEngine
    from kt_graph.worker_engine import WorkerGraphEngine
    from kt_worker_synthesis.agents.synthesizer_agent import SynthesizerAgent
    from kt_worker_synthesis.agents.synthesizer_state import SynthesizerState
    from kt_worker_synthesis.pipelines.document_processing import process_synthesis_document
    from kt_worker_synthesis.prompts.synthesizer import build_synthesizer_system_message

    write_session = None
    if worker_state.write_session_factory is not None:
        write_session = worker_state.write_session_factory()

    try:
        # ReadGraphEngine for graph reads during agent navigation (short-lived sessions)
        read_engine = ReadGraphEngine(
            session_factory=worker_state.session_factory,
            qdrant_client=worker_state.qdrant_client,
        )
        # WorkerGraphEngine for writes (create synthesis node, link facts)
        worker_engine = WorkerGraphEngine(
            write_session,
            worker_state.embedding_service,
            qdrant_client=worker_state.qdrant_client,
        )

        async def emit_event(event_type: str, **data: Any) -> None:
            try:
                await ctx.aio_put_stream(json.dumps({"type": event_type, **data}))
            except Exception:
                pass

        # Agent tools use read_engine for navigation
        agent_ctx = AgentContext(
            graph_engine=read_engine,
            provider_registry=worker_state.provider_registry,
            model_gateway=worker_state.model_gateway,
            embedding_service=worker_state.embedding_service,
            session=None,
            session_factory=worker_state.session_factory,
            emit_event=emit_event,
            write_session_factory=worker_state.write_session_factory,
            qdrant_client=worker_state.qdrant_client,
        )

        # Build system message
        system_content = build_synthesizer_system_message(
            topic=input.topic,
            starting_node_ids=input.starting_node_ids,
            budget=input.exploration_budget,
        )

        initial_state = SynthesizerState(
            topic=input.topic,
            starting_node_ids=input.starting_node_ids,
            exploration_budget=input.exploration_budget,
            messages=[
                SystemMessage(content=system_content),
                HumanMessage(
                    content=(
                        "Investigate the topic using the tools available. When done, "
                        "call finish_synthesis(text) with your complete markdown document. "
                        "The text argument must contain the COMPLETE document — anything "
                        "written outside finish_synthesis() is discarded."
                    )
                ),
            ],
        )

        # Run the agent
        await emit_event("synthesis_agent_started", topic=input.topic)

        agent = SynthesizerAgent(agent_ctx, model_id_override=input.model_id)
        graph, _ = agent.build_graph()
        compiled = graph.compile()

        recursion_limit = max(input.exploration_budget * 30, 500)
        final = await compiled.ainvoke(initial_state, config={"recursion_limit": recursion_limit})

        if isinstance(final, dict):
            synthesis_text = final.get("synthesis_text", "")
            nodes_visited = final.get("nodes_visited", [])
        else:
            synthesis_text = final.synthesis_text
            nodes_visited = final.nodes_visited

        if not synthesis_text:
            logger.warning("Synthesizer agent ended without producing text")
            synthesis_text = "Synthesis completed but no document was produced."

        # Create synthesis node — append timestamp for unique key
        from datetime import UTC, datetime

        ts = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        concept = f"{input.topic or 'Synthesis'} [{ts}]"
        node = await worker_engine.create_node(
            concept=concept,
            node_type="synthesis",
        )
        synthesis_node_id = node.id
        from kt_models.link_normalizer import normalize_ai_links

        synthesis_text = normalize_ai_links(synthesis_text)
        await worker_engine.set_node_definition(synthesis_node_id, synthesis_text)

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

        # Build node name/alias lookup for text matching
        node_names: dict[str, list[str]] = {}
        for nid in nodes_visited:
            try:
                n = await read_engine.get_node(uuid.UUID(nid))
                if n:
                    names = [n.concept]
                    node_names[nid] = names
            except Exception:
                pass

        # Run document processing pipeline (returns JSON doc)
        doc = await process_synthesis_document(
            synthesis_text=synthesis_text,
            embedding_service=worker_state.embedding_service,
            qdrant_client=worker_state.qdrant_client,
            node_names_and_aliases=node_names,
        )

        # Store document JSON in node metadata via write-db
        if write_session:
            from sqlalchemy import update

            from kt_db.write_models import WriteNode

            existing_meta = {}
            from sqlalchemy import select as sa_select

            row = (
                await write_session.execute(
                    sa_select(WriteNode.metadata_).where(WriteNode.node_uuid == synthesis_node_id)
                )
            ).scalar_one_or_none()
            if row and isinstance(row, dict):
                existing_meta = row

            existing_meta["synthesis_document"] = doc
            existing_meta["model_id"] = input.model_id or agent.get_model_id()
            await write_session.execute(
                update(WriteNode).where(WriteNode.node_uuid == synthesis_node_id).values(metadata_=existing_meta)
            )
            await write_session.commit()

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
