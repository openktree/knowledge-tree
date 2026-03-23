"""Seed-based enrichment: enrich existing nodes with facts from their seed.

Seeds are the sole source of facts for nodes. This module finds the node's
seed, loads its facts, and links any that aren't already linked to the node.

NOTE: This module only links new facts to nodes.  Dimension generation is
handled exclusively by the Hatchet DAG's ``generate_dimensions`` task
(via ``DimensionPipeline``) to avoid duplicate dimension creation.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from kt_agents_core.state import AgentContext
from kt_db.models import Fact
from kt_worker_nodes.pipelines.models import EnrichResult

logger = logging.getLogger(__name__)


class PoolEnricher:
    """Enriches existing nodes with facts from their seed."""

    def __init__(self, ctx: AgentContext) -> None:
        self._ctx = ctx

    async def enrich(self, node: Any) -> EnrichResult:
        """Find facts from the node's seed and link any unlinked ones.

        Seeds are the sole source of facts. If no seed is found, no
        enrichment happens.

        Only links facts — does NOT regenerate dimensions or definitions.
        Those are handled by downstream pipeline phases (Hatchet DAG or
        BatchPipeline) to avoid duplicate generation.

        Returns an EnrichResult with the count of new facts linked.
        """
        ctx = self._ctx
        write_session = getattr(ctx.graph_engine, "_write_session", None)
        if write_session is None:
            logger.info("enrich '%s': no write session, skipping", node.concept)
            return EnrichResult(new_facts_linked=0, dimensions_regenerated=False)

        from kt_db.repositories.write_nodes import WriteNodeRepository
        from kt_db.repositories.write_seeds import WriteSeedRepository

        write_repo = WriteNodeRepository(write_session)
        wn = await write_repo.get_by_uuid(node.id)
        if wn is None:
            logger.info("enrich '%s': not found in write-db, skipping", node.concept)
            return EnrichResult(new_facts_linked=0, dimensions_regenerated=False)

        seed_repo = WriteSeedRepository(write_session)
        seed = await seed_repo.get_seed_by_promoted_node_key(wn.key)
        if seed is None:
            logger.info("enrich '%s': no seed found, skipping", node.concept)
            return EnrichResult(new_facts_linked=0, dimensions_regenerated=False)

        # Load fact IDs from seed (aggregate descendants for ambiguous seeds)
        if seed.status == "ambiguous":
            seed_fact_ids = await seed_repo.get_all_descendant_facts(seed.key)
        else:
            seed_fact_ids = await seed_repo.get_facts_for_seed(seed.key)

        if not seed_fact_ids:
            logger.info("enrich '%s': seed has no facts", node.concept)
            return EnrichResult(new_facts_linked=0, dimensions_regenerated=False)

        # Compare against write-db's record of linked facts (more reliable
        # than graph-db since sync may lag)
        existing_ids: set[str] = {str(fid) for fid in (wn.fact_ids or [])}
        new_fact_ids: list[uuid.UUID] = [fid for fid in seed_fact_ids if str(fid) not in existing_ids]

        if not new_fact_ids:
            logger.info(
                "enrich '%s': %d seed facts, all already linked",
                node.concept,
                len(seed_fact_ids),
            )
            return EnrichResult(new_facts_linked=0, dimensions_regenerated=False)

        # Load and link new facts
        new_facts: list[Fact] = await ctx.graph_engine.get_facts_by_ids(new_fact_ids)

        logger.info(
            "enrich '%s': %d existing facts, %d new from seed",
            node.concept,
            len(existing_ids),
            len(new_facts),
        )

        linked: list[Fact] = []
        for f in new_facts:
            try:
                await ctx.graph_engine.link_fact_to_node(node.id, f.id)
                linked.append(f)
            except Exception:
                logger.debug("Skipping fact link %s→%s (error)", node.id, f.id)

        if linked:
            await ctx.emit(
                "activity_log",
                action=f"Enriched '{node.concept}' with {len(linked)} seed facts",
                tool="build_concept",
            )

        return EnrichResult(new_facts_linked=len(linked), dimensions_regenerated=False)
