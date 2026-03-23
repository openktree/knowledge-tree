"""Store perspective thesis/antithesis pairs as lightweight seeds.

Instead of immediately dispatching expensive composite node builds,
this module creates paired WriteSeed entries (node_type="perspective")
with cross-referencing metadata. Users can later choose which pairs
to synthesize into full perspective nodes.
"""

from __future__ import annotations

import logging
from typing import Any

from kt_db.keys import make_seed_key
from kt_db.repositories.write_seeds import WriteSeedRepository

logger = logging.getLogger(__name__)


async def store_perspective_seeds(
    plans: list[dict[str, Any]],
    write_seed_repo: WriteSeedRepository,
    embedding_service: Any | None = None,
    qdrant_seed_repo: Any | None = None,
    fact_ids: list[str] | None = None,
) -> list[str]:
    """Create thesis + antithesis seed pairs from perspective plans.

    Each plan dict should have:
        - claim: str (thesis)
        - antithesis: str
        - source_concept_id: str (name or UUID of parent concept)
        - source_concept_name: str (optional, human-readable)
        - scope_description: str (optional)

    Returns thesis seed keys for all created pairs.
    """
    if not plans:
        return []

    thesis_keys: list[str] = []
    seed_upserts: list[dict] = []
    seed_links: list[dict] = []
    metadata_updates: list[tuple[str, dict]] = []

    for plan in plans:
        claim = plan.get("claim", "").strip()
        antithesis = plan.get("antithesis", "").strip()
        if not claim or not antithesis:
            continue

        source_concept_id = plan.get("source_concept_id", "")
        source_concept_name = plan.get("source_concept_name", source_concept_id)
        scope_description = plan.get("scope_description", "")

        thesis_key = make_seed_key("perspective", claim)
        antithesis_key = make_seed_key("perspective", antithesis)

        # Build thesis seed
        seed_upserts.append({
            "key": thesis_key,
            "name": claim,
            "node_type": "perspective",
            "fact_count": 1,
        })

        # Build antithesis seed
        seed_upserts.append({
            "key": antithesis_key,
            "name": antithesis,
            "node_type": "perspective",
            "fact_count": 1,
        })

        # Metadata for thesis — contains full pair info
        thesis_meta = {
            "claim": claim,
            "antithesis": antithesis,
            "antithesis_seed_key": antithesis_key,
            "source_concept_name": source_concept_name,
            "source_node_ids": [source_concept_id] if source_concept_id else [],
            "scope_description": scope_description,
            "dialectic_role": "thesis",
        }

        # Metadata for antithesis — back-reference to thesis
        antithesis_meta = {
            "claim": antithesis,
            "thesis_seed_key": thesis_key,
            "dialectic_role": "antithesis",
        }

        metadata_updates.append((thesis_key, thesis_meta))
        metadata_updates.append((antithesis_key, antithesis_meta))

        # Link facts if provided
        if fact_ids:
            for fid in fact_ids:
                seed_links.append({"seed_key": thesis_key, "fact_id": fid})
                seed_links.append({"seed_key": antithesis_key, "fact_id": fid})

        thesis_keys.append(thesis_key)

    if not seed_upserts:
        return []

    # Batch upsert seeds
    await write_seed_repo.upsert_seeds_batch(seed_upserts)

    # Update metadata on each seed
    for seed_key, meta in metadata_updates:
        await write_seed_repo.update_seed_metadata(seed_key, meta)

    # Batch link facts
    if seed_links:
        await write_seed_repo.link_facts_batch(seed_links)
        linked_keys = list({lnk["seed_key"] for lnk in seed_links})
        await write_seed_repo.refresh_fact_counts(linked_keys)

    # Embed in Qdrant for dedup if services available
    if embedding_service and qdrant_seed_repo:
        try:
            texts = [s["name"] for s in seed_upserts]
            embeddings = await embedding_service.embed_texts(texts)
            for seed_data, embedding in zip(seed_upserts, embeddings):
                await qdrant_seed_repo.upsert(
                    seed_key=seed_data["key"],
                    embedding=embedding,
                    name=seed_data["name"],
                    node_type="perspective",
                )
        except Exception:
            logger.warning("Failed to embed perspective seeds in Qdrant", exc_info=True)

    logger.info(
        "Stored %d perspective seed pairs (%d thesis keys)",
        len(seed_upserts) // 2,
        len(thesis_keys),
    )

    return thesis_keys
