"""On-demand enrichment task for co-occurrence edges.

Generates justifications for edges that were created during auto-build
without LLM justification. Triggered by API access or explicit user action.

NOTE: The enrich_node task has been replaced by ``rebuild_node`` in
``rebuild_node.py``.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import cast

from hatchet_sdk import Context

from kt_config.settings import get_settings
from kt_hatchet.client import get_hatchet
from kt_hatchet.lifespan import WorkerState
from kt_hatchet.models import EnrichEdgeInput, EnrichEdgeOutput

logger = logging.getLogger(__name__)

hatchet = get_hatchet()
_schedule_timeout = timedelta(minutes=get_settings().hatchet_schedule_timeout_minutes)


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

    from kt_worker_nodes.hatchet_pipeline import HatchetPipeline

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
