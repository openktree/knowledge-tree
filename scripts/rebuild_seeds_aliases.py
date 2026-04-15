#!/usr/bin/env python3
"""One-time backfill: populate write_seeds.aliases[] from JSONB metadata and merge history.

Steps:
  1. Read write_seed_merges — append loser's slugified name + loser's JSONB aliases
     to winner's aliases[] column.
  2. Read write_seeds with metadata_.aliases — migrate JSONB aliases to column
     (slugified, deduped, exclude self).
  3. Set status='active' for any seeds still with NULL/unknown status.

Usage:
    uv run python scripts/rebuild_seeds_aliases.py [--dry-run]
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

# Ensure monorepo packages are importable
sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from kt_config.settings import get_settings
from kt_db.keys import make_seed_key
from kt_db.write_models import WriteSeed, WriteSeedMerge

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)


async def backfill(session: AsyncSession, dry_run: bool) -> None:
    settings = get_settings()
    logger.info("Starting aliases backfill (dry_run=%s)", dry_run)

    # ── Step 1: From merge history ─────────────────────────────────────────
    logger.info("Step 1: propagate loser aliases from write_seed_merges")
    merges_result = await session.execute(
        select(WriteSeedMerge).order_by(WriteSeedMerge.created_at)
    )
    merges = merges_result.scalars().all()

    # Build winner → extra aliases map
    winner_extras: dict[str, set[str]] = {}
    for m in merges:
        loser_key = m.loser_key
        winner_key = m.winner_key
        if not loser_key or not winner_key or loser_key == winner_key:
            continue
        winner_extras.setdefault(winner_key, set()).add(loser_key)

        # Also pull JSONB aliases from the loser seed (if it still exists)
        loser_seed = await session.get(WriteSeed, loser_key)
        if loser_seed and loser_seed.metadata_ and isinstance(loser_seed.metadata_.get("aliases"), list):
            for a in loser_seed.metadata_["aliases"]:
                if isinstance(a, str) and a.strip():
                    winner_extras[winner_key].add(make_seed_key(a))

    merge_updates = 0
    for winner_key, extras in winner_extras.items():
        winner = await session.get(WriteSeed, winner_key)
        if winner is None:
            continue
        existing = set(winner.aliases or [])
        new_aliases = list((existing | extras) - {winner_key})
        if set(new_aliases) == existing:
            continue
        logger.info("  merge: %s ← %d aliases (was %d)", winner_key, len(new_aliases), len(existing))
        if not dry_run:
            winner.aliases = new_aliases
        merge_updates += 1

    logger.info("Step 1 complete: %d winners updated from merge history", merge_updates)

    # ── Step 2: Migrate JSONB metadata_.aliases ────────────────────────────
    logger.info("Step 2: migrate JSONB metadata_.aliases → aliases column")
    seeds_result = await session.execute(select(WriteSeed))
    seeds = seeds_result.scalars().all()

    jsonb_updates = 0
    for seed in seeds:
        meta_aliases: list[str] = []
        if seed.metadata_ and isinstance(seed.metadata_.get("aliases"), list):
            for a in seed.metadata_["aliases"]:
                if isinstance(a, str) and a.strip():
                    meta_aliases.append(make_seed_key(a))

        if not meta_aliases:
            continue

        existing = set(seed.aliases or [])
        merged = (existing | set(meta_aliases)) - {seed.key}
        if merged == existing:
            continue

        logger.info(
            "  jsonb: %s — adding %d aliases (total %d)",
            seed.key,
            len(merged - existing),
            len(merged),
        )
        if not dry_run:
            seed.aliases = list(merged)
        jsonb_updates += 1

    logger.info("Step 2 complete: %d seeds updated from JSONB aliases", jsonb_updates)

    # ── Step 3: Activate null-status seeds ────────────────────────────────
    logger.info("Step 3: set status='active' for seeds with null/blank status")
    null_status_result = await session.execute(
        select(WriteSeed).where(
            (WriteSeed.status.is_(None)) | (WriteSeed.status == "")
        )
    )
    null_seeds = null_status_result.scalars().all()
    null_fixed = 0
    for seed in null_seeds:
        logger.info("  activating: %s (was '%s')", seed.key, seed.status)
        if not dry_run:
            seed.status = "active"
        null_fixed += 1
    logger.info("Step 3 complete: %d seeds activated", null_fixed)

    if not dry_run:
        await session.commit()
        logger.info("Committed all changes")
    else:
        logger.info("Dry-run: no changes committed")

    logger.info(
        "Backfill done — merge_updates=%d  jsonb_updates=%d  null_fixed=%d",
        merge_updates,
        jsonb_updates,
        null_fixed,
    )


async def main(dry_run: bool) -> None:
    settings = get_settings()
    engine = create_async_engine(settings.write_database_url, echo=False)
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with async_session() as session:
        await backfill(session, dry_run=dry_run)

    await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Backfill write_seeds.aliases[] column")
    parser.add_argument("--dry-run", action="store_true", help="Print changes without writing")
    args = parser.parse_args()
    asyncio.run(main(dry_run=args.dry_run))
