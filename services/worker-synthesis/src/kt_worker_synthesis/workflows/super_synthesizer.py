"""Hatchet workflow for the SuperSynthesizer.

3-task pipeline:
1. reconnaissance — LLM plans scopes from graph search results
2. run_sub_syntheses — dispatches N synthesizer_wf in parallel, waits for all
3. combine — runs SuperSynthesizerAgent to produce the meta-synthesis
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
    SuperSynthesizerInput,
    SuperSynthesizerOutput,
    SynthesizerInput,
)

logger = logging.getLogger(__name__)

hatchet = get_hatchet()

super_synthesizer_wf = hatchet.workflow(
    name="super_synthesizer_wf",
    input_validator=SuperSynthesizerInput,
)


@super_synthesizer_wf.task(name="reconnaissance", execution_timeout=timedelta(minutes=10))
async def reconnaissance(input: SuperSynthesizerInput, ctx: Context) -> dict[str, Any]:
    """Plan scopes by searching the graph and designing sub-synthesis configs.

    If sub_configs are already provided, skip reconnaissance and use them directly.
    """
    if input.sub_configs:
        return {"sub_configs": [c.model_dump() for c in input.sub_configs]}

    worker_state = cast(WorkerState, ctx.lifespan)

    from kt_agents_core.state import AgentContext
    from kt_graph.engine import GraphEngine

    async with worker_state.session_factory() as session:
        graph_engine = GraphEngine(
            session,
            worker_state.embedding_service,
            qdrant_client=worker_state.qdrant_client,
        )
        agent_ctx = AgentContext(
            graph_engine=graph_engine,
            provider_registry=worker_state.provider_registry,
            model_gateway=worker_state.model_gateway,
            embedding_service=worker_state.embedding_service,
            session=session,
        )

        # Search broadly to map the landscape
        all_nodes: list[dict[str, Any]] = []
        search_terms = [input.topic]
        words = input.topic.split()
        if len(words) > 2:
            search_terms.append(" ".join(words[: len(words) // 2]))
            search_terms.append(" ".join(words[len(words) // 2 :]))
        search_terms.append(f"{input.topic} overview")

        seen_ids: set[str] = set()
        for term in search_terms:
            nodes = await graph_engine.search_nodes(term, limit=30)
            for n in nodes:
                nid = str(n.id)
                if nid not in seen_ids:
                    seen_ids.add(nid)
                    facts = await graph_engine.get_node_facts(n.id)
                    all_nodes.append(
                        {
                            "node_id": nid,
                            "concept": n.concept,
                            "node_type": n.node_type,
                            "fact_count": len(facts),
                        }
                    )

        # Read existing syntheses the user wants to include
        existing_summaries = ""
        if input.existing_synthesis_ids:
            import uuid as _uuid

            summaries = []
            for sid in input.existing_synthesis_ids:
                try:
                    node = await graph_engine.get_node(_uuid.UUID(sid))
                    if node and node.definition:
                        # Show title + first 500 chars of definition
                        preview = node.definition[:500]
                        if len(node.definition) > 500:
                            preview += "..."
                        summaries.append(f"- **{node.concept}**: {preview}")
                except Exception:
                    pass
            if summaries:
                existing_summaries = (
                    "\n\n## Existing Research (already covered — DO NOT overlap)\n"
                    "The user has selected the following existing synthesis documents to include. "
                    "Your new scopes should COMPLEMENT these, not duplicate their coverage.\n\n"
                    + "\n\n".join(summaries)
                )

        # Use LLM to plan scopes
        node_list = "\n".join(
            f"- {n['concept']} [{n['node_type']}] ({n['fact_count']} facts) id={n['node_id']}"
            for n in sorted(all_nodes, key=lambda x: x["fact_count"], reverse=True)[:50]
        )

        plan_prompt = f"""You are planning a multi-scope synthesis investigation on: "{input.topic}"

Available nodes in the knowledge graph:
{node_list}
{existing_summaries}

Design exactly {input.scope_count if input.scope_count > 0 else "3-7"} non-overlapping thematic scopes that COMPLEMENT any existing research listed above.
Each scope should:
- Have a clear thematic focus
- NOT duplicate topics already covered by existing syntheses
- Include 3-8 specific starting node IDs from the list above
- Have an exploration budget of 10-20 nodes

Output a JSON array of scope objects:
[
  {{"topic": "<scope description>", "starting_node_ids": ["id1", "id2"], "exploration_budget": 15}},
  ...
]

Output ONLY the JSON array."""

        try:
            response = await agent_ctx.model_gateway.generate(
                messages=[{"role": "user", "content": plan_prompt}],
                model_id=agent_ctx.model_gateway.orchestrator_model,
            )
            raw = response if isinstance(response, str) else str(response)
            # Extract JSON array
            start = raw.find("[")
            end = raw.rfind("]")
            if start >= 0 and end > start:
                scopes = json.loads(raw[start : end + 1])
            else:
                scopes = []
        except Exception:
            logger.warning("Scope planning failed, creating single scope", exc_info=True)
            scopes = [{"topic": input.topic, "starting_node_ids": [], "exploration_budget": 20}]

        if not scopes:
            scopes = [{"topic": input.topic, "starting_node_ids": [], "exploration_budget": 20}]

        sub_configs = []
        for scope in scopes:
            sub_configs.append(
                SynthesizerInput(
                    topic=scope.get("topic", input.topic),
                    starting_node_ids=scope.get("starting_node_ids", []),
                    exploration_budget=scope.get("exploration_budget", 15),
                    visibility=input.visibility,
                    creator_id=input.creator_id,
                ).model_dump()
            )

        return {"sub_configs": sub_configs}


@super_synthesizer_wf.task(
    name="run_sub_syntheses",
    parents=[reconnaissance],
    execution_timeout=timedelta(minutes=60),
)
async def run_sub_syntheses(input: SuperSynthesizerInput, ctx: Context) -> dict[str, Any]:
    """Dispatch synthesizer_wf for each scope and collect results."""
    recon = ctx.task_output(reconnaissance)
    sub_configs = recon.get("sub_configs", [])

    if not sub_configs:
        return {"synthesis_node_ids": []}

    from kt_worker_synthesis.workflows.synthesizer import synthesizer_wf

    # Dispatch all sub-syntheses in parallel
    refs = []
    for config in sub_configs:
        ref = await synthesizer_wf.aio_run_no_wait(SynthesizerInput(**config))
        refs.append(ref)

    # Wait for all to complete
    synthesis_node_ids = []
    for ref in refs:
        try:
            result = await ref.aio_result()
            output = result.get("run_synthesizer", {})
            node_id = output.get("synthesis_node_id", "")
            if node_id:
                synthesis_node_ids.append(node_id)
        except Exception:
            logger.warning("Sub-synthesis failed", exc_info=True)

    # Include existing synthesis IDs provided by the user
    if input.existing_synthesis_ids:
        synthesis_node_ids.extend(input.existing_synthesis_ids)

    return {"synthesis_node_ids": synthesis_node_ids}


@super_synthesizer_wf.task(
    name="combine",
    parents=[run_sub_syntheses],
    execution_timeout=timedelta(minutes=30),
)
async def combine(input: SuperSynthesizerInput, ctx: Context) -> dict[str, Any]:
    """Run SuperSynthesizerAgent to combine sub-syntheses into a meta-synthesis."""
    sub_result = ctx.task_output(run_sub_syntheses)
    synthesis_node_ids = sub_result.get("synthesis_node_ids", [])

    if not synthesis_node_ids:
        return SuperSynthesizerOutput(supersynthesis_node_id="").model_dump()

    worker_state = cast(WorkerState, ctx.lifespan)

    from langchain_core.messages import HumanMessage, SystemMessage

    from kt_agents_core.state import AgentContext
    from kt_graph.engine import GraphEngine
    from kt_worker_synthesis.agents.super_synthesizer_agent import SuperSynthesizerAgent
    from kt_worker_synthesis.agents.super_synthesizer_state import SuperSynthesizerState
    from kt_worker_synthesis.pipelines.document_processing import process_synthesis_document
    from kt_worker_synthesis.prompts.super_synthesizer import build_super_synthesizer_system_message

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

            agent_ctx = AgentContext(
                graph_engine=graph_engine,
                provider_registry=worker_state.provider_registry,
                model_gateway=worker_state.model_gateway,
                embedding_service=worker_state.embedding_service,
                session=session,
                session_factory=worker_state.session_factory,
                write_session_factory=worker_state.write_session_factory,
                qdrant_client=worker_state.qdrant_client,
            )

            system_content = build_super_synthesizer_system_message(
                topic=input.topic,
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
                super_text = final.get("super_synthesis_text", "")
            else:
                super_text = final.super_synthesis_text

            if not super_text:
                super_text = "Super-synthesis completed but no document was produced."

            # Create supersynthesis node — append timestamp for unique key
            from datetime import UTC, datetime

            ts = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
            concept = f"{input.topic or 'Super-Synthesis'} [{ts}]"
            node = await graph_engine.create_node(
                concept=concept,
                node_type="supersynthesis",
            )
            supersynthesis_node_id = node.id
            from kt_models.link_normalizer import normalize_ai_links

            super_text = normalize_ai_links(super_text)
            await graph_engine.set_node_definition(supersynthesis_node_id, super_text)

            # Set visibility
            if write_session:
                from sqlalchemy import update

                from kt_db.write_models import WriteNode

                await write_session.execute(
                    update(WriteNode)
                    .where(WriteNode.node_uuid == supersynthesis_node_id)
                    .values(visibility=input.visibility, creator_id=input.creator_id)
                )
                await write_session.flush()

            # Collect node names from all sub-syntheses for text matching
            # Read from sub-synthesis nodes' metadata (which has synthesis_document)
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

            # Run document processing (returns JSON doc)
            doc = await process_synthesis_document(
                synthesis_text=super_text,
                embedding_service=worker_state.embedding_service,
                qdrant_client=worker_state.qdrant_client,
                node_names_and_aliases=node_names,
            )

            # Add sub_synthesis_ids to the document
            doc["sub_synthesis_ids"] = synthesis_node_ids

            # Store document JSON in node metadata via write-db
            if write_session:
                from sqlalchemy import select as sa_select

                row = (
                    await write_session.execute(
                        sa_select(WriteNode.metadata_).where(WriteNode.node_uuid == supersynthesis_node_id)
                    )
                ).scalar_one_or_none()
                existing_meta = row if isinstance(row, dict) else {}
                existing_meta["synthesis_document"] = doc
                await write_session.execute(
                    update(WriteNode)
                    .where(WriteNode.node_uuid == supersynthesis_node_id)
                    .values(metadata_=existing_meta)
                )
                await write_session.commit()

            stats = doc.get("stats", {})

            output = SuperSynthesizerOutput(
                supersynthesis_node_id=str(supersynthesis_node_id),
                sub_synthesis_node_ids=synthesis_node_ids,
                total_sentences=stats.get("sentences_count", 0),
                total_facts_linked=stats.get("facts_linked", 0),
            )
            return output.model_dump()

        finally:
            if write_session is not None:
                await write_session.close()
