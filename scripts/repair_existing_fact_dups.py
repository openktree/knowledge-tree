"""One-shot historical fact-duplication repair.

Run this once after deploying the post-job dedup workflow to collapse
the ~9k+ exact dups and estimated ~25k+ near dups that accumulated
before the insert-time race was fixed.

Two passes:

1. **Exact pass** — groups ``write_facts`` by ``(content, fact_type)``.
   Oldest (by ``created_at``) wins; all others are merged into it via
   :func:`kt_facts.processing.merge.merge_into_heavy`, which handles
   every write-db reference (including array-typed columns), every
   graph-db junction, the graph-db ``facts`` row, and the Qdrant point.

2. **Near pass** — streams every ``write_fact``, fetches its vector
   from Qdrant, runs ``find_most_similar`` at the per-type threshold,
   and if a match with a smaller UUID exists, merges self into that
   match. The smallest-UUID tiebreaker keeps the pass deterministic
   and re-runnable.

Usage::

    uv run --all-packages python scripts/repair_existing_fact_dups.py --dry-run
    uv run --all-packages python scripts/repair_existing_fact_dups.py
    uv run --all-packages python scripts/repair_existing_fact_dups.py --exact-only
    uv run --all-packages python scripts/repair_existing_fact_dups.py --near-only --limit 5000

The script is idempotent — a no-op when there's nothing left to merge —
and batches 100 groups per transaction. Reuses the same client setup
as ``scripts/rebuild_qdrant_facts.py``.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import time
import uuid
from typing import Iterable

from qdrant_client import AsyncQdrantClient
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from kt_config.settings import get_settings
from kt_facts.processing.dedup import _threshold_for_type
from kt_facts.processing.merge import merge_into_heavy
from kt_qdrant.repositories.facts import FACTS_COLLECTION, QdrantFactRepository

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


_EXACT_BATCH = 100


def _chunks(seq: list, size: int) -> Iterable[list]:
    for i in range(0, len(seq), size):
        yield seq[i : i + size]


async def repair_exact_pass(
    write_sf,
    graph_sf,
    qdrant_client: AsyncQdrantClient | None,
    collection: str,
    *,
    limit: int | None,
    dry_run: bool,
) -> int:
    """Merge all exact (content, fact_type) duplicate groups."""
    async with write_sf() as scan_session:
        groups_result = await scan_session.execute(
            text(
                """
                SELECT array_agg(id ORDER BY created_at)::text[] AS ids,
                       content, fact_type, count(*) AS n
                  FROM write_facts
                 GROUP BY content, fact_type
                HAVING count(*) > 1
                """
            )
        )
        groups = groups_result.all()

    logger.info("Exact pass: %d duplicate groups", len(groups))
    if dry_run:
        total = sum(row[3] - 1 for row in groups)
        logger.info("Exact pass (dry-run): would merge %d loser rows", total)
        return total

    merged = 0
    for chunk in _chunks(list(groups), _EXACT_BATCH):
        async with write_sf() as write_session, graph_sf() as graph_session:
            for row in chunk:
                ids = [uuid.UUID(s) for s in row[0]]
                canonical = ids[0]
                for loser in ids[1:]:
                    await merge_into_heavy(
                        write_session=write_session,
                        graph_session=graph_session,
                        qdrant_client=qdrant_client,
                        qdrant_collection=collection,
                        loser_id=loser,
                        canonical_id=canonical,
                    )
                    merged += 1
                    if limit is not None and merged >= limit:
                        break
                if limit is not None and merged >= limit:
                    break
            await write_session.commit()
            await graph_session.commit()
        logger.info("Exact pass: merged %d / %d groups so far", merged, len(groups))
        if limit is not None and merged >= limit:
            break

    logger.info("Exact pass complete: merged %d losers", merged)
    return merged


async def repair_near_pass(
    write_sf,
    graph_sf,
    qdrant_client: AsyncQdrantClient,
    collection: str,
    *,
    limit: int | None,
    dry_run: bool,
) -> int:
    """Stream every fact, fetch its vector, and merge self into a
    smaller-UUID Qdrant match when one exists above the per-type
    threshold.
    """
    qdrant_repo = QdrantFactRepository(qdrant_client, collection_name=collection)

    async with write_sf() as count_session:
        total = (await count_session.execute(text("SELECT count(*) FROM write_facts"))).scalar_one()
    logger.info("Near pass: scanning %d facts", total)

    offset = 0
    batch_size = 500
    merged = 0
    scanned = 0
    while True:
        async with write_sf() as read_session:
            rows = (
                await read_session.execute(
                    text(
                        """
                        SELECT id, fact_type
                          FROM write_facts
                         ORDER BY id
                         OFFSET :offset
                         LIMIT :limit
                        """
                    ),
                    {"offset": offset, "limit": batch_size},
                )
            ).all()
        if not rows:
            break

        for row in rows:
            scanned += 1
            fact_id_str = str(row[0])
            fact_id = uuid.UUID(fact_id_str) if not isinstance(row[0], uuid.UUID) else row[0]
            fact_type = row[1]

            # Fetch vector
            try:
                records = await qdrant_client.retrieve(
                    collection_name=collection,
                    ids=[fact_id_str],
                    with_vectors=True,
                )
            except Exception:
                logger.debug("retrieve failed for %s", fact_id_str, exc_info=True)
                continue
            if not records:
                continue
            vec = records[0].vector
            if vec is None or isinstance(vec, dict):
                continue

            try:
                hit = await qdrant_repo.find_most_similar(
                    vec,  # type: ignore[arg-type]
                    score_threshold=_threshold_for_type(fact_type),
                )
            except Exception:
                logger.debug("find_most_similar failed for %s", fact_id_str, exc_info=True)
                continue
            if hit is None or hit.fact_id == fact_id:
                continue
            # Deterministic tiebreaker: keep the smaller UUID, merge the
            # larger one away.
            if hit.fact_id >= fact_id:
                continue

            if dry_run:
                logger.info("Near pass (dry-run): would merge %s -> %s", fact_id, hit.fact_id)
                merged += 1
            else:
                async with write_sf() as write_session, graph_sf() as graph_session:
                    await merge_into_heavy(
                        write_session=write_session,
                        graph_session=graph_session,
                        qdrant_client=qdrant_client,
                        qdrant_collection=collection,
                        loser_id=fact_id,
                        canonical_id=hit.fact_id,
                    )
                    await write_session.commit()
                    await graph_session.commit()
                merged += 1

            if limit is not None and merged >= limit:
                logger.info("Near pass: limit %d reached", limit)
                return merged

        offset += batch_size
        logger.info("Near pass: scanned %d / %d, merged %d", scanned, total, merged)

    logger.info("Near pass complete: merged %d rows", merged)
    return merged


async def main() -> None:
    parser = argparse.ArgumentParser(description="Repair existing fact duplicates")
    parser.add_argument("--dry-run", action="store_true", help="Report only, do not modify DBs")
    parser.add_argument("--limit", type=int, default=None, help="Stop after N merges")
    parser.add_argument("--exact-only", action="store_true", help="Skip the near-dup pass")
    parser.add_argument("--near-only", action="store_true", help="Skip the exact-dup pass")
    parser.add_argument(
        "--collection",
        default=FACTS_COLLECTION,
        help="Qdrant collection name (for non-default graphs)",
    )
    args = parser.parse_args()

    if args.exact_only and args.near_only:
        parser.error("--exact-only and --near-only are mutually exclusive")

    settings = get_settings()

    qdrant = AsyncQdrantClient(url=settings.qdrant_url)
    write_engine = create_async_engine(settings.write_database_url)
    graph_engine = create_async_engine(settings.database_url)

    from sqlalchemy.ext.asyncio import async_sessionmaker

    write_sf = async_sessionmaker(write_engine, class_=AsyncSession, expire_on_commit=False)
    graph_sf = async_sessionmaker(graph_engine, class_=AsyncSession, expire_on_commit=False)

    t0 = time.monotonic()

    if not args.near_only:
        logger.info("═══ EXACT PASS ═══")
        await repair_exact_pass(
            write_sf,
            graph_sf,
            qdrant,
            args.collection,
            limit=args.limit,
            dry_run=args.dry_run,
        )

    if not args.exact_only:
        logger.info("═══ NEAR PASS ═══")
        await repair_near_pass(
            write_sf,
            graph_sf,
            qdrant,
            args.collection,
            limit=args.limit,
            dry_run=args.dry_run,
        )

    logger.info("Done in %.1fs", time.monotonic() - t0)

    await write_engine.dispose()
    await graph_engine.dispose()


if __name__ == "__main__":
    # Silence unused-import warning on ``select`` / ``func`` — reserved
    # for inline one-off queries during debugging.
    _ = (select, func)
    asyncio.run(main())
