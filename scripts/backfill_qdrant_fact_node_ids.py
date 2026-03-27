"""Backfill node_ids into Qdrant fact point payloads.

Reads from the NodeFact junction table in graph-db and batch-updates
each Qdrant fact point's payload with the list of linked node IDs.

Usage:
    uv run python scripts/backfill_qdrant_fact_node_ids.py
"""

import asyncio
import logging
from collections import defaultdict

from qdrant_client import AsyncQdrantClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from kt_config.settings import get_settings
from kt_db.models import NodeFact
from kt_qdrant.repositories.facts import FACTS_COLLECTION

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

BATCH_SIZE = 500  # facts per Qdrant set_payload batch


async def main() -> None:
    settings = get_settings()
    engine = create_async_engine(settings.database_url)
    qdrant = AsyncQdrantClient(url=settings.qdrant_url)

    # 1. Count total facts with node links
    async with AsyncSession(engine) as session:
        total = (await session.execute(select(func.count(func.distinct(NodeFact.fact_id))))).scalar_one()
        logger.info("Total facts with node links: %d", total)

        # 2. Build fact_id -> [node_id, ...] mapping
        logger.info("Loading NodeFact mappings...")
        result = await session.execute(select(NodeFact.fact_id, NodeFact.node_id))
        fact_nodes: dict[str, list[str]] = defaultdict(list)
        row_count = 0
        for fact_id, node_id in result.all():
            fact_nodes[str(fact_id)].append(str(node_id))
            row_count += 1
        logger.info("Loaded %d NodeFact rows for %d unique facts", row_count, len(fact_nodes))

    # 3. Check how many Qdrant points exist
    collection_info = await qdrant.get_collection(FACTS_COLLECTION)
    logger.info("Qdrant facts collection: %d points", collection_info.points_count)

    # 4. Batch update Qdrant payloads
    fact_ids = list(fact_nodes.keys())
    updated = 0
    skipped = 0

    for i in range(0, len(fact_ids), BATCH_SIZE):
        batch = fact_ids[i : i + BATCH_SIZE]

        # Use set_payload with batch of point IDs sharing the same operation
        # Qdrant set_payload supports multiple points at once, but each needs
        # its own node_ids list, so we update one at a time within the batch
        for fact_id in batch:
            node_ids = fact_nodes[fact_id]
            try:
                await qdrant.set_payload(
                    collection_name=FACTS_COLLECTION,
                    payload={"node_ids": node_ids},
                    points=[fact_id],
                )
                updated += 1
            except Exception:
                skipped += 1
                if skipped <= 5:
                    logger.warning("Failed to update fact %s", fact_id, exc_info=True)

        progress = min(i + BATCH_SIZE, len(fact_ids))
        logger.info("Progress: %d/%d facts updated (%d skipped)", progress, len(fact_ids), skipped)

    logger.info("Done! Updated %d facts, skipped %d", updated, skipped)

    # 5. Verify a sample
    sample = await qdrant.scroll(FACTS_COLLECTION, limit=5, with_payload=True)
    logger.info("Sample verification:")
    for point in sample[0]:
        node_ids = point.payload.get("node_ids") if point.payload else None
        logger.info("  fact %s: node_ids=%s", point.id, node_ids)

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
