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

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from kt_config.settings import get_settings
from kt_db.write_models import WriteSeed
from kt_hatchet.client import run_workflow

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

CHUNK_SIZE = 200


async def main(graph_slug: str) -> None:
    settings = get_settings()
    # Append schema search_path via connect_args so we query the per-graph schema.
    schema = "public" if graph_slug == "default" else f"graph_{graph_slug}"
    engine = create_async_engine(
        settings.write_database_url,
        connect_args={"server_settings": {"search_path": schema}},
    )
    sf = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with sf() as session:
        result = await session.execute(
            select(WriteSeed.key).where(WriteSeed.status == "pending")
        )
        keys = [row[0] for row in result.all()]

    logger.info("Found %d pending seeds in schema %s", len(keys), schema)
    if not keys:
        await engine.dispose()
        return

    chunks = [keys[i : i + CHUNK_SIZE] for i in range(0, len(keys), CHUNK_SIZE)]
    logger.info("Dispatching %d seed_dedup_batch runs (chunk_size=%d)", len(chunks), CHUNK_SIZE)

    for i, chunk in enumerate(chunks):
        try:
            await run_workflow(
                "seed_dedup_batch",
                {
                    "seed_keys": chunk,
                    "scope_id": f"manual-dedup-{i}",
                    "graph_slug": graph_slug,
                },
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
