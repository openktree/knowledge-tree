"""On-demand enrichment tasks for stub nodes and co-occurrence edges.

These tasks generate dimensions/definitions (for nodes) and justifications
(for edges) that were skipped during auto-build. Triggered by API access
or explicit user action.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import timedelta
from typing import cast

from hatchet_sdk import ConcurrencyExpression, ConcurrencyLimitStrategy, Context

from kt_config.settings import get_settings
from kt_hatchet.client import get_hatchet
from kt_hatchet.lifespan import WorkerState
from kt_hatchet.models import EnrichEdgeInput, EnrichEdgeOutput, EnrichNodeInput, EnrichNodeOutput
from kt_worker_nodes.hatchet_pipeline import HatchetPipeline

logger = logging.getLogger(__name__)

hatchet = get_hatchet()
_schedule_timeout = timedelta(minutes=get_settings().hatchet_schedule_timeout_minutes)


# ══════════════════════════════════════════════════════════════
# Enrich Node Task
# ══════════════════════════════════════════════════════════════

enrich_node_task = hatchet.workflow(
    name="enrich_node",
    input_validator=EnrichNodeInput,
    concurrency=ConcurrencyExpression(
        expression="input.node_id",
        max_runs=1,
        limit_strategy=ConcurrencyLimitStrategy.GROUP_ROUND_ROBIN,
    ),
)


@enrich_node_task.task(
    execution_timeout=timedelta(minutes=15),
    schedule_timeout=_schedule_timeout,
)
async def enrich_node(input: EnrichNodeInput, ctx: Context) -> dict:
    """Generate dimensions and definition for a stub node.

    1. Load node from write-db
    2. If enrichment_status not in ('stub', 'partial'), skip
    3. Check fact count — need enough facts for meaningful dimensions
    4. Generate dimensions via DimensionPipeline
    5. Generate definition via DefinitionPipeline
    6. Set enrichment_status = 'enriched'
    """
    import uuid

    state = cast(WorkerState, ctx.lifespan)
    settings = get_settings()
    pipeline = HatchetPipeline(state, api_key=input.api_key)

    node_id = uuid.UUID(input.node_id)

    async with pipeline._open_sessions() as (graph_session, write_session):
        from kt_agents_core.state import AgentContext
        from kt_db.repositories.write_nodes import WriteNodeRepository
        from kt_graph.engine import GraphEngine
        from kt_models.gateway import ModelGateway

        if write_session is None:
            return EnrichNodeOutput(enriched=False).model_dump()

        write_repo = WriteNodeRepository(write_session)
        wn = await write_repo.get_by_uuid(node_id)
        if wn is None:
            logger.warning("Node %s not found in write-db", input.node_id)
            return EnrichNodeOutput(enriched=False).model_dump()

        # Only enrich stub/partial nodes
        if wn.enrichment_status not in ("stub", "partial"):
            logger.info("Node %s already enriched (status=%s)", wn.concept, wn.enrichment_status)
            return EnrichNodeOutput(enriched=False).model_dump()

        # Look up the corresponding seed to get the authoritative fact count
        from kt_db.repositories.write_seeds import WriteSeedRepository

        seed_repo = WriteSeedRepository(write_session)
        seeds = await seed_repo.get_seeds_by_promoted_node_key(wn.key)

        if seeds:
            all_fact_ids: set[uuid.UUID] = set()
            for seed in seeds:
                descendant_facts = await seed_repo.get_all_descendant_facts(seed.key)
                all_fact_ids.update(descendant_facts)
            seed_fact_ids = list(all_fact_ids)
            fact_count = len(seed_fact_ids)
        else:
            seed_fact_ids = None
            fact_count = len(wn.fact_ids or [])

        min_facts = settings.enrichment_min_facts_for_dimensions

        if fact_count < min_facts:
            # Not enough facts yet — mark as partial
            from sqlalchemy import text

            await write_session.execute(
                text("UPDATE write_nodes SET enrichment_status = 'partial', updated_at = NOW() WHERE key = :key"),
                {"key": wn.key},
            )
            await write_session.commit()
            logger.info(
                "Node '%s' has %d facts (need %d) — marked partial",
                wn.concept,
                fact_count,
                min_facts,
            )
            return EnrichNodeOutput(enriched=False).model_dump()

        # Create engine + context for dimension pipeline
        engine = GraphEngine(
            graph_session,
            write_session=write_session,
            qdrant_client=state.qdrant_client,
        )
        gateway = ModelGateway()

        async def emit(event_type: str, **kwargs: object) -> None:
            try:
                await ctx.aio_put_stream(json.dumps({"type": event_type, **kwargs}))
            except Exception:
                pass

        agent_ctx = AgentContext(
            graph_engine=engine,
            provider_registry=state.provider_registry,
            model_gateway=gateway,
            embedding_service=state.embedding_service,
            session=graph_session,
            emit_event=emit,
            session_factory=state.session_factory,
            content_fetcher=state.content_fetcher,
            write_session_factory=state.write_session_factory,
            qdrant_client=state.qdrant_client,
        )

        # Load the node from graph-db for dimension generation
        node = await engine.get_node(node_id)
        if node is None:
            logger.warning("Node %s not found in graph-db", input.node_id)
            return EnrichNodeOutput(enriched=False).model_dump()

        # Load facts from the seed (authoritative) or fall back to node facts
        if seed_fact_ids:
            facts = await engine.get_facts_by_ids(seed_fact_ids)
            # Link any seed facts not yet linked to the node
            existing_node_fact_ids = set(str(fid) for fid in (wn.fact_ids or []))
            linked = 0
            for fact in facts:
                if str(fact.id) not in existing_node_fact_ids:
                    await engine.link_fact_to_node(node_id, fact.id)
                    linked += 1
            if linked:
                logger.info("Linked %d seed facts to node '%s'", linked, wn.concept)
        else:
            facts = await engine.get_node_facts_with_sources(node_id)

        sample_size = settings.enrichment_dimension_sample_size
        if len(facts) > sample_size:
            import random

            facts = random.sample(facts, sample_size)

        # Generate dimensions
        from kt_worker_nodes.pipelines.dimensions.pipeline import DimensionPipeline

        dim_pipeline = DimensionPipeline(agent_ctx)
        dim_mode = "entity" if node.node_type == "entity" else "neutral"
        dim_result = await dim_pipeline.generate_and_store(node, facts, mode=dim_mode)

        # Generate definition
        from kt_worker_nodes.pipelines.definitions.pipeline import DefinitionPipeline

        def_pipeline = DefinitionPipeline(agent_ctx)
        await def_pipeline.generate_definition(node.id, node.concept)

        # Update enrichment status
        from sqlalchemy import text

        await write_session.execute(
            text("UPDATE write_nodes SET enrichment_status = 'enriched', updated_at = NOW() WHERE key = :key"),
            {"key": wn.key},
        )

        await write_session.commit()
        if graph_session is not None:
            await graph_session.commit()

    dims_count = len(dim_result.dim_results)
    logger.info("Enriched node '%s': %d dimensions generated", wn.concept, dims_count)

    return EnrichNodeOutput(enriched=True, dimensions_count=dims_count).model_dump()


# ══════════════════════════════════════════════════════════════
# Enrich Edge Task
# ══════════════════════════════════════════════════════════════

enrich_edge_task = hatchet.workflow(
    name="enrich_edge",
    input_validator=EnrichEdgeInput,
)


@enrich_edge_task.task(
    execution_timeout=timedelta(minutes=10),
    schedule_timeout=_schedule_timeout,
)
async def enrich_edge(input: EnrichEdgeInput, ctx: Context) -> dict:
    """Generate justification for a co-occurrence edge.

    Weight stays as the co-occurrence weight (NOT overwritten).
    Only generates a justification text from shared facts.
    """
    import uuid

    state = cast(WorkerState, ctx.lifespan)
    settings = get_settings()
    pipeline = HatchetPipeline(state, api_key=input.api_key)

    edge_id = uuid.UUID(input.edge_id)

    async with pipeline._open_sessions() as (graph_session, write_session):
        from kt_models.gateway import ModelGateway

        if write_session is None:
            return EnrichEdgeOutput(justified=False).model_dump()

        # Find the edge in write-db by UUID
        from sqlalchemy import select

        from kt_db.write_models import WriteEdge

        result = await write_session.execute(select(WriteEdge).where(WriteEdge.key.like(f"%{input.edge_id}%")))
        we = result.scalar_one_or_none()

        if we is None:
            # Try finding by edge UUID derivation
            from kt_db.models import Edge

            if graph_session:
                edge = (await graph_session.execute(select(Edge).where(Edge.id == edge_id))).scalar_one_or_none()
                if edge and edge.justification:
                    return EnrichEdgeOutput(justified=False).model_dump()

            logger.warning("Edge %s not found", input.edge_id)
            return EnrichEdgeOutput(justified=False).model_dump()

        if we.justification:
            logger.info("Edge %s already has justification", we.key)
            return EnrichEdgeOutput(justified=False).model_dump()

        # Load shared facts (cap at sample size)
        max_facts = settings.enrichment_edge_justification_sample_size
        fact_ids = (we.fact_ids or [])[:max_facts]

        if not fact_ids:
            return EnrichEdgeOutput(justified=False).model_dump()

        gateway = ModelGateway()

        # Load the actual facts
        from kt_db.repositories.write_facts import WriteFactRepository

        write_fact_repo = WriteFactRepository(write_session)
        facts = await write_fact_repo.get_by_ids([uuid.UUID(fid) for fid in fact_ids])

        if not facts:
            return EnrichEdgeOutput(justified=False).model_dump()

        # Generate justification via simple LLM call
        fact_texts = [f.content for f in facts[:50]]
        fact_block = "\n".join(f"- {t}" for t in fact_texts)

        prompt = (
            f"Given these {len(fact_texts)} shared facts between two entities, "
            f"write a brief 1-2 sentence justification for why they are related:\n\n"
            f"{fact_block}\n\n"
            f"Justification:"
        )

        try:
            justification = await gateway.generate(
                model_id=settings.edge_resolution_model or settings.default_model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3,
                max_tokens=200,
            )
            justification = justification.strip() if justification else None
        except Exception:
            logger.debug("Failed to generate justification for edge %s", we.key, exc_info=True)
            justification = None

        if justification:
            # Update justification without changing weight
            from sqlalchemy import text

            await write_session.execute(
                text("UPDATE write_edges SET justification = :j, updated_at = NOW() WHERE key = :key"),
                {"j": justification, "key": we.key},
            )
            await write_session.commit()
            logger.info("Generated justification for edge %s", we.key)
            return EnrichEdgeOutput(justified=True).model_dump()

    return EnrichEdgeOutput(justified=False).model_dump()
