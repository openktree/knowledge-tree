"""Rebuild the Qdrant facts collection from write-db.

Wipes the existing facts collection (which may contain orphaned points
from failed Hatchet tasks), re-creates it with proper config, embeds
all facts from write-db, and backfills node_ids from graph-db.

Usage:
    # Local dev:
    uv run python scripts/rebuild_qdrant_facts.py

    # Production (exec into any worker pod):
    kubectl -n knowledge-tree exec -it <pod> -- uv run python scripts/rebuild_qdrant_facts.py

    # Skip node_ids backfill:
    uv run python scripts/rebuild_qdrant_facts.py --skip-node-ids

    # Custom batch size:
    uv run python scripts/rebuild_qdrant_facts.py --batch-size 200
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import time
from collections import defaultdict

from qdrant_client import AsyncQdrantClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from kt_config.settings import get_settings
from kt_db.models import NodeFact
from kt_db.write_models import WriteFact
from kt_models.embeddings import EmbeddingService
from kt_qdrant.repositories.facts import FACTS_COLLECTION, QdrantFactRepository

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

DEFAULT_BATCH_SIZE = 500


async def main(batch_size: int = DEFAULT_BATCH_SIZE, skip_node_ids: bool = False) -> None:
    settings = get_settings()

    # Initialize clients
    qdrant = AsyncQdrantClient(url=settings.qdrant_url)
    qdrant_repo = QdrantFactRepository(qdrant)
    embedding_service = EmbeddingService()
    write_engine = create_async_engine(settings.write_database_url)
    graph_engine = create_async_engine(settings.database_url)

    t0 = time.monotonic()

    # ── Step 1: Wipe and recreate collection ─────────────────────────
    logger.info("Wiping existing facts collection...")
    try:
        await qdrant.delete_collection(FACTS_COLLECTION)
        logger.info("Deleted existing collection")
    except Exception:
        logger.info("No existing collection to delete")

    await qdrant_repo.ensure_collection()
    logger.info("Created facts collection (dim=%d, cosine)", settings.embedding_dimensions)

    try:
        await qdrant_repo.ensure_text_index()
        logger.info("Created text index on content field")
    except AttributeError:
        logger.info("ensure_text_index not available (PR #95 not deployed) — skipping text index")

    # ── Step 2: Count write-db facts ─────────────────────────────────
    async with AsyncSession(write_engine) as session:
        total = (await session.execute(select(func.count(WriteFact.id)))).scalar_one()
    logger.info("Total facts in write-db: %d", total)

    if total == 0:
        logger.info("No facts to process. Done.")
        await write_engine.dispose()
        await graph_engine.dispose()
        return

    # ── Step 3: Stream, embed, and upsert in batches ─────────────────
    offset = 0
    processed = 0
    skipped = 0
    embed_time = 0.0
    upsert_time = 0.0

    while offset < total:
        async with AsyncSession(write_engine) as session:
            result = await session.execute(
                select(WriteFact).order_by(WriteFact.created_at).offset(offset).limit(batch_size)
            )
            facts = result.scalars().all()

        if not facts:
            break

        texts = [f.content for f in facts]
        fact_ids = [f.id for f in facts]
        fact_types = [f.fact_type for f in facts]

        # Embed
        try:
            t_embed = time.monotonic()
            embeddings = await embedding_service.embed_batch(texts)
            embed_time += time.monotonic() - t_embed
        except Exception:
            logger.exception("Embedding failed for batch at offset %d, skipping", offset)
            skipped += len(facts)
            offset += batch_size
            continue

        # Upsert to Qdrant — build points directly to work regardless of
        # whether the deployed QdrantFactRepository supports 4-tuples
        from qdrant_client.models import PointStruct

        points = [
            PointStruct(
                id=str(fid),
                vector=emb,
                payload={"fact_type": ft, "content": content},
            )
            for fid, emb, ft, content in zip(fact_ids, embeddings, fact_types, texts)
        ]
        try:
            t_upsert = time.monotonic()
            for chunk_start in range(0, len(points), 200):
                chunk = points[chunk_start : chunk_start + 200]
                await qdrant.upsert(collection_name=FACTS_COLLECTION, points=chunk)
            upsert_time += time.monotonic() - t_upsert
            processed += len(points)
        except Exception:
            logger.exception("Qdrant upsert failed for batch at offset %d, skipping", offset)
            skipped += len(facts)
            offset += batch_size
            continue

        offset += batch_size
        elapsed = time.monotonic() - t0
        rate = processed / elapsed if elapsed > 0 else 0
        logger.info(
            "Progress: %d/%d (%.1f%%) — %.0f facts/s — embed: %.1fs, upsert: %.1fs",
            processed,
            total,
            processed / total * 100,
            rate,
            embed_time,
            upsert_time,
        )

    logger.info("Embedding + upsert complete: %d processed, %d skipped", processed, skipped)

    # ── Step 4: Backfill node_ids from graph-db ──────────────────────
    if not skip_node_ids:
        logger.info("Backfilling node_ids from graph-db...")
        async with AsyncSession(graph_engine) as session:
            nf_count = (await session.execute(select(func.count(func.distinct(NodeFact.fact_id))))).scalar_one()
            logger.info("Facts with node links: %d", nf_count)

            result = await session.execute(select(NodeFact.fact_id, NodeFact.node_id))
            fact_nodes: dict[str, list[str]] = defaultdict(list)
            for fact_id, node_id in result.all():
                fact_nodes[str(fact_id)].append(str(node_id))

        updated = 0
        node_skipped = 0
        fact_id_list = list(fact_nodes.keys())

        for i in range(0, len(fact_id_list), batch_size):
            batch = fact_id_list[i : i + batch_size]
            for fact_id in batch:
                try:
                    await qdrant.set_payload(
                        collection_name=FACTS_COLLECTION,
                        payload={"node_ids": fact_nodes[fact_id]},
                        points=[fact_id],
                    )
                    updated += 1
                except Exception:
                    node_skipped += 1
                    if node_skipped <= 5:
                        logger.warning("Failed to set node_ids for %s", fact_id, exc_info=True)

            progress = min(i + batch_size, len(fact_id_list))
            if progress % 2000 < batch_size:
                logger.info("node_ids progress: %d/%d", progress, len(fact_id_list))

        logger.info("node_ids backfill: %d updated, %d skipped", updated, node_skipped)

    # ── Step 5: Verify ───────────────────────────────────────────────
    qdrant_count = await qdrant_repo.count()
    elapsed = time.monotonic() - t0
    logger.info("Verification: Qdrant has %d points (write-db has %d)", qdrant_count, total)

    # Sample scroll
    sample = await qdrant.scroll(FACTS_COLLECTION, limit=3, with_payload=True)
    logger.info("Sample points:")
    for point in sample[0]:
        payload = point.payload or {}
        has_content = "content" in payload
        has_node_ids = "node_ids" in payload
        logger.info(
            "  %s: fact_type=%s, has_content=%s, has_node_ids=%s",
            point.id,
            payload.get("fact_type"),
            has_content,
            has_node_ids,
        )

    logger.info("Done in %.1fs!", elapsed)

    await write_engine.dispose()
    await graph_engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Rebuild Qdrant facts collection from write-db")
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE, help="Facts per batch")
    parser.add_argument("--skip-node-ids", action="store_true", help="Skip node_ids backfill from graph-db")
    args = parser.parse_args()
    asyncio.run(main(batch_size=args.batch_size, skip_node_ids=args.skip_node_ids))
