"""Remap Qdrant seed points after type-prefix stripping migration.

The write-db migration 5b16c2127652 strips type prefixes from seed keys
(e.g. "concept:foo" -> "foo"). Qdrant points still have old prefixed keys
in their payload and old key-derived point IDs. This script remaps them
without re-embedding.

For each point:
  1. Strip prefix from payload.seed_key
  2. Compute new point ID = key_to_uuid(new_key)
  3. Upsert point with new ID + same vector + updated payload
  4. Delete old point

Idempotent: already-stripped keys are skipped.

Usage:
    uv run python scripts/remap_qdrant_seed_keys.py [--dry-run] [--batch-size 500]
"""

from __future__ import annotations

import argparse
import asyncio
import logging

from qdrant_client import AsyncQdrantClient
from qdrant_client.models import PointStruct

from kt_config.settings import get_settings
from kt_db.keys import key_to_uuid

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

COLLECTION = "seeds"
PREFIXES = ("concept:", "entity:", "event:", "location:")


def strip_prefix(key: str) -> str:
    for p in PREFIXES:
        if key.startswith(p):
            return key[len(p) :]
    return key


async def remap(dry_run: bool = False, batch_size: int = 500) -> None:
    settings = get_settings()
    client = AsyncQdrantClient(url=settings.qdrant_url)

    # Check collection exists
    collections = await client.get_collections()
    if COLLECTION not in {c.name for c in collections.collections}:
        logger.error("Collection '%s' does not exist", COLLECTION)
        return

    total_remapped = 0
    total_skipped = 0
    total_deleted = 0
    offset = None

    while True:
        # Scroll through all points
        results = await client.scroll(
            collection_name=COLLECTION,
            limit=batch_size,
            offset=offset,
            with_payload=True,
            with_vectors=True,
        )
        points, next_offset = results

        if not points:
            break

        upsert_batch: list[PointStruct] = []
        delete_ids: list[str] = []

        for point in points:
            payload = point.payload or {}
            old_key = payload.get("seed_key", "")

            if not any(old_key.startswith(p) for p in PREFIXES):
                total_skipped += 1
                continue

            new_key = strip_prefix(old_key)
            new_id = str(key_to_uuid(new_key))
            old_id = str(point.id)

            # Update payload
            new_payload = dict(payload)
            new_payload["seed_key"] = new_key
            if "node_type" in new_payload:
                new_payload["node_type"] = "concept"

            upsert_batch.append(
                PointStruct(
                    id=new_id,
                    vector=point.vector,
                    payload=new_payload,
                )
            )

            # Only delete old point if ID actually changed
            if old_id != new_id:
                delete_ids.append(old_id)

        if upsert_batch:
            if dry_run:
                logger.info(
                    "[DRY RUN] Would upsert %d points, delete %d old IDs",
                    len(upsert_batch),
                    len(delete_ids),
                )
            else:
                await client.upsert(
                    collection_name=COLLECTION,
                    points=upsert_batch,
                )
                if delete_ids:
                    await client.delete(
                        collection_name=COLLECTION,
                        points_selector=delete_ids,
                    )
                logger.info(
                    "Remapped %d points, deleted %d old IDs",
                    len(upsert_batch),
                    len(delete_ids),
                )

            total_remapped += len(upsert_batch)
            total_deleted += len(delete_ids)

        offset = next_offset
        if offset is None:
            break

    logger.info(
        "Done. Remapped: %d, Skipped (already clean): %d, Old IDs deleted: %d",
        total_remapped,
        total_skipped,
        total_deleted,
    )

    await client.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Remap Qdrant seed keys after prefix stripping")
    parser.add_argument("--dry-run", action="store_true", help="Log changes without writing")
    parser.add_argument("--batch-size", type=int, default=500, help="Points per scroll batch")
    args = parser.parse_args()
    asyncio.run(remap(dry_run=args.dry_run, batch_size=args.batch_size))


if __name__ == "__main__":
    main()
