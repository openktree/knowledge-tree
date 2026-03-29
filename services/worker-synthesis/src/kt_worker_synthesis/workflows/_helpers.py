"""Shared helpers for synthesis workflows.

Extracted from synthesizer.py and super_synthesizer.py so that the
regeneration workflows can reuse the same agent-running logic.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field
from typing import Any

from kt_hatchet.models import SynthesizerInput

logger = logging.getLogger(__name__)


@dataclass
class SynthesisResult:
    """Result of running the synthesizer agent."""

    synthesis_text: str = ""
    nodes_visited: list[str] = field(default_factory=list)


async def run_synthesis_agent(
    input: SynthesizerInput,
    model_gateway: Any,
    graph_engine: Any,
    provider_registry: Any,
    embedding_service: Any,
    session: Any,
    session_factory: Any,
    write_session_factory: Any | None,
    qdrant_client: Any | None,
    emit_event: Callable[..., Coroutine[Any, Any, None]],
) -> SynthesisResult:
    """Run the synthesizer agent and return the result.

    This is the core agent logic extracted from the synthesizer workflow
    so it can be reused by the regeneration workflow.
    """
    from langchain_core.messages import HumanMessage, SystemMessage

    from kt_agents_core.state import AgentContext
    from kt_worker_synthesis.agents.synthesizer_agent import SynthesizerAgent
    from kt_worker_synthesis.agents.synthesizer_state import SynthesizerState
    from kt_worker_synthesis.prompts.synthesizer import build_synthesizer_system_message

    agent_ctx = AgentContext(
        graph_engine=graph_engine,
        provider_registry=provider_registry,
        model_gateway=model_gateway,
        embedding_service=embedding_service,
        session=session,
        session_factory=session_factory,
        emit_event=emit_event,
        write_session_factory=write_session_factory,
        qdrant_client=qdrant_client,
    )

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

    await emit_event("synthesis_agent_started", topic=input.topic)

    agent = SynthesizerAgent(agent_ctx)
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

    return SynthesisResult(synthesis_text=synthesis_text, nodes_visited=nodes_visited)


async def process_and_store_synthesis(
    *,
    synthesis_text: str,
    synthesis_node_id: uuid.UUID,
    nodes_visited: list[str],
    graph_engine: Any,
    embedding_service: Any,
    qdrant_client: Any | None,
    write_session: Any | None,
    synthesis_input_data: dict[str, Any],
    parent_supersynthesis_id: str | None = None,
) -> dict[str, Any]:
    """Process synthesis text and store the document in node metadata.

    Sets the node definition, runs the document processing pipeline,
    and stores the result in the node's metadata. Returns the document dict.
    """
    from sqlalchemy import select as sa_select
    from sqlalchemy import update

    from kt_db.write_models import WriteNode
    from kt_worker_synthesis.pipelines.document_processing import process_synthesis_document

    await graph_engine.set_node_definition(synthesis_node_id, synthesis_text)

    # Build node name/alias lookup for text matching
    node_names: dict[str, list[str]] = {}
    for nid in nodes_visited:
        try:
            n = await graph_engine.get_node(uuid.UUID(nid))
            if n:
                node_names[nid] = [n.concept]
        except Exception:
            pass

    doc = await process_synthesis_document(
        synthesis_text=synthesis_text,
        embedding_service=embedding_service,
        qdrant_client=qdrant_client,
        node_names_and_aliases=node_names,
    )

    if write_session:
        row = (
            await write_session.execute(sa_select(WriteNode.metadata_).where(WriteNode.node_uuid == synthesis_node_id))
        ).scalar_one_or_none()
        existing_meta = row if isinstance(row, dict) else {}

        existing_meta["synthesis_document"] = doc
        existing_meta["synthesis_input"] = synthesis_input_data
        # Clear any previous error
        existing_meta.pop("synthesis_error", None)
        if parent_supersynthesis_id:
            existing_meta["parent_supersynthesis_id"] = parent_supersynthesis_id

        await write_session.execute(
            update(WriteNode).where(WriteNode.node_uuid == synthesis_node_id).values(metadata_=existing_meta)
        )
        await write_session.commit()

    return doc


async def store_synthesis_error(
    *,
    synthesis_node_id: uuid.UUID,
    write_session: Any,
    synthesis_input_data: dict[str, Any],
    error_message: str = "Synthesis agent ended without producing text",
    parent_supersynthesis_id: str | None = None,
    visibility: str = "public",
    creator_id: str | None = None,
) -> None:
    """Store error metadata on a failed synthesis node.

    Sets no definition, stores error info and the original input for
    regeneration in the node's metadata.
    """
    from datetime import UTC, datetime

    from sqlalchemy import select as sa_select
    from sqlalchemy import update

    from kt_db.write_models import WriteNode

    row = (
        await write_session.execute(sa_select(WriteNode.metadata_).where(WriteNode.node_uuid == synthesis_node_id))
    ).scalar_one_or_none()
    existing_meta = row if isinstance(row, dict) else {}

    existing_meta["synthesis_error"] = {
        "message": error_message,
        "timestamp": datetime.now(UTC).isoformat(),
    }
    existing_meta["synthesis_input"] = synthesis_input_data
    # Remove stale document if any
    existing_meta.pop("synthesis_document", None)
    if parent_supersynthesis_id:
        existing_meta["parent_supersynthesis_id"] = parent_supersynthesis_id

    await write_session.execute(
        update(WriteNode)
        .where(WriteNode.node_uuid == synthesis_node_id)
        .values(
            metadata_=existing_meta,
            visibility=visibility,
            creator_id=creator_id,
        )
    )
    await write_session.commit()


async def run_super_synthesis_combine(
    *,
    topic: str,
    synthesis_node_ids: list[str],
    graph_engine: Any,
    model_gateway: Any,
    embedding_service: Any,
    session: Any,
    session_factory: Any,
    write_session_factory: Any | None,
    qdrant_client: Any | None,
    provider_registry: Any,
) -> str:
    """Run the SuperSynthesizerAgent to combine sub-syntheses.

    Returns the super-synthesis text (may be empty on failure).
    """
    from langchain_core.messages import HumanMessage, SystemMessage

    from kt_agents_core.state import AgentContext
    from kt_worker_synthesis.agents.super_synthesizer_agent import SuperSynthesizerAgent
    from kt_worker_synthesis.agents.super_synthesizer_state import SuperSynthesizerState
    from kt_worker_synthesis.prompts.super_synthesizer import build_super_synthesizer_system_message

    agent_ctx = AgentContext(
        graph_engine=graph_engine,
        provider_registry=provider_registry,
        model_gateway=model_gateway,
        embedding_service=embedding_service,
        session=session,
        session_factory=session_factory,
        write_session_factory=write_session_factory,
        qdrant_client=qdrant_client,
    )

    system_content = build_super_synthesizer_system_message(
        topic=topic,
        synthesis_node_ids=synthesis_node_ids,
    )

    initial_state = SuperSynthesizerState(
        synthesis_node_ids=synthesis_node_ids,
        messages=[
            SystemMessage(content=system_content),
            HumanMessage(
                content=(
                    "Read all sub-syntheses, then produce a comprehensive super-synthesis "
                    "using finish_super_synthesis(text)."
                )
            ),
        ],
    )

    agent = SuperSynthesizerAgent(agent_ctx)
    graph, _ = agent.build_graph()
    compiled = graph.compile()

    recursion_limit = max(len(synthesis_node_ids) * 30, 500)
    final = await compiled.ainvoke(initial_state, config={"recursion_limit": recursion_limit})

    if isinstance(final, dict):
        return final.get("super_synthesis_text", "")
    return final.super_synthesis_text
