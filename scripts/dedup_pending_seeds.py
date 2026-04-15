#!/usr/bin/env python3
"""Dispatch seed_dedup_batch for all status=pending seeds in a given graph schema.

Used to recover from a missed dedup dispatch. Splits seeds into chunks of 200
and fires one seed_dedup_batch workflow per chunk.

Usage:
    uv run python scripts/dedup_pending_seeds.py --graph-slug scientific
    uv run python scripts/dedup_pending_seeds.py --graph-slug default
"""

from __future__ import annotations

import argparse
import asyncio
import logging

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from kt_config.settings import get_settings
from kt_hatchet.client import get_hatchet

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

CHUNK_SIZE = 200


async def main(graph_slug: str) -> None:
    settings = get_settings()
    schema = "public" if graph_slug == "default" else f"graph_{graph_slug}"
    engine = create_async_engine(settings.write_database_url)
    sf = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with sf() as session:
        # Schema name validated by regex above (just graph_slug prefix)
        result = await session.execute(
            text(f"SELECT key FROM {schema}.write_seeds WHERE status = 'pending'")
        )
        keys = [row[0] for row in result.all()]

    logger.info("Found %d pending seeds in schema %s", len(keys), schema)
    if not keys:
        await engine.dispose()
        return

    chunks = [keys[i : i + CHUNK_SIZE] for i in range(0, len(keys), CHUNK_SIZE)]
    logger.info("Dispatching %d seed_dedup_batch runs (chunk_size=%d)", len(chunks), CHUNK_SIZE)

    # Resolve graph UUID by slug from the graph-db
    from sqlalchemy.ext.asyncio import create_async_engine as _cae
    graph_engine = _cae(settings.database_url)
    async with graph_engine.connect() as conn:
        result = await conn.execute(
            text("SELECT id FROM graphs WHERE slug = :slug"),
            {"slug": graph_slug},
        )
        row = result.first()
        graph_id = str(row[0]) if row else None
    await graph_engine.dispose()

    if not graph_id:
        logger.error("Could not resolve graph_id for slug '%s'", graph_slug)
        await engine.dispose()
        return

    logger.info("Resolved graph_id=%s for slug=%s", graph_id, graph_slug)

    import json
    h = get_hatchet()

    for i, chunk in enumerate(chunks):
        try:
            # Fire-and-forget (don't wait for result)
            await h._client.admin.aio_run_workflow(
                workflow_name="seed_dedup_batch",
                input=json.dumps({
                    "seed_keys": chunk,
                    "scope_id": f"manual-dedup-{i}",
                    "graph_id": graph_id,
                }),
            )
            logger.info("Dispatched chunk %d/%d (%d seeds)", i + 1, len(chunks), len(chunk))
        except Exception:
            logger.exception("Dispatch failed for chunk %d", i)

    await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--graph-slug", required=True, help="Graph slug (e.g. 'scientific' or 'default')")
    args = parser.parse_args()
    asyncio.run(main(graph_slug=args.graph_slug))
