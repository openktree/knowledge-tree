"""Dispatch rebuild_full node_pipeline runs for all user nodes in scientific graph.

Skips root "All X" nodes. Uses aio_run_no_wait for parallel dispatch.
"""

from __future__ import annotations

import asyncio
import logging

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from kt_config.settings import get_settings
from kt_hatchet.models import NodePipelineInput

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

SCIENTIFIC_GRAPH_ID = "c54fa69f-83fe-4e12-99d1-4be3aff01582"
SCHEMA = "graph_scientific"
ROOT_CONCEPTS = {"All Concepts", "All Entities", "All Events", "All Perspectives"}


async def main() -> None:
    # Import here so Hatchet client is initialized at call time
    from kt_worker_nodes.workflows.node_pipeline import node_pipeline_wf

    settings = get_settings()
    engine = create_async_engine(settings.database_url)

    async with engine.connect() as conn:
        await conn.execute(text(f"SET search_path TO {SCHEMA}"))
        result = await conn.execute(
            text("SELECT id, concept FROM nodes WHERE concept NOT IN :roots").bindparams(roots=tuple(ROOT_CONCEPTS))
        )
        nodes = [(str(row[0]), row[1]) for row in result.fetchall()]

    await engine.dispose()

    logger.info("Dispatching rebuild_full for %d scientific nodes", len(nodes))

    dispatched = 0
    for node_id, concept in nodes:
        try:
            await node_pipeline_wf.aio_run_no_wait(
                NodePipelineInput(
                    mode="rebuild_full",
                    scope="all",
                    node_id=node_id,
                    graph_id=SCIENTIFIC_GRAPH_ID,
                )
            )
            logger.info("Dispatched rebuild for %s (%s)", concept, node_id)
            dispatched += 1
        except Exception:
            logger.exception("Failed to dispatch %s (%s)", concept, node_id)

    logger.info("Done. Dispatched %d/%d rebuilds", dispatched, len(nodes))


if __name__ == "__main__":
    asyncio.run(main())
