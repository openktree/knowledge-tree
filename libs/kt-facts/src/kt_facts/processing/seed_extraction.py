"""Seed extraction — stores entity/concept mentions as seeds during fact decomposition.

Seeds are lightweight proto-nodes that track which entities and concepts are
mentioned across facts. When enough facts accumulate for a seed, it can be
promoted to a full node with pre-accumulated evidence.

The pipeline is structured in three sub-phases for efficiency:
  A. Pre-compute all data (pure Python, no DB)
  B. Route seeds sequentially (DB-dependent, must be serial)
  C. Batch write (single session, few SQL statements)
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

from kt_db.keys import make_seed_key

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from kt_db.repositories.write_seeds import WriteSeedRepository

logger = logging.getLogger(__name__)


async def store_seeds_from_extracted_nodes(
    extracted_nodes: list[dict[str, Any]],
    all_facts: list,
    write_seed_repo: WriteSeedRepository,
    embedding_service: object,
    qdrant_seed_repo: object,
    model_gateway: object | None = None,
    write_fact_repo: object | None = None,
) -> tuple[int, list[str]]:
    """Create seeds from entity extraction output and link to facts.

    For each extracted node, upserts a seed and links it to the facts
    indicated by ``fact_indices`` (1-indexed into *all_facts*).
    Then derives edge candidates from co-occurring seeds (seeds sharing
    at least one fact).

    Structured as pre-compute → route → batch-write for efficiency.

    Returns:
        Tuple of (total seed-fact links created, list of unique seed keys touched).
    """
    if not extracted_nodes or not all_facts:
        return 0, []

    # ── Sub-phase A: Pre-compute all data (pure Python) ──────────────

    # Collect per-seed data, grouped by key to handle duplicate mentions
    seed_data: dict[str, dict[str, Any]] = {}  # key -> {name, node_type, ...}
    seed_links: list[dict[str, Any]] = []  # [{seed_key, fact_id, extraction_context}]
    fact_to_seeds: dict[object, list[str]] = {}  # fact_id -> [seed_keys]
    seed_contexts: dict[str, str] = {}  # key -> fact_context for routing
    seed_aliases: dict[str, list[str]] = {}  # key -> LLM-provided aliases

    for node in extracted_nodes:
        name = node.get("name")
        node_type = node.get("node_type", "concept")
        if not name:
            continue

        seed_key = make_seed_key(node_type, name)
        fact_indices = node.get("fact_indices", [])

        # Build fact context for routing
        fact_contents: list[str] = []
        for idx in fact_indices:
            if isinstance(idx, int) and 1 <= idx <= len(all_facts):
                fact = all_facts[idx - 1]
                if hasattr(fact, "content") and fact.content:
                    fact_contents.append(fact.content[:200])

        # Collect LLM-provided aliases
        node_aliases = node.get("aliases", [])

        # Aggregate: if same key appears multiple times, merge fact_indices
        if seed_key in seed_data:
            seed_data[seed_key]["fact_count"] += 1
            # Merge aliases from duplicate mentions
            existing_aliases = set(seed_aliases.get(seed_key, []))
            existing_aliases.update(node_aliases)
            seed_aliases[seed_key] = list(existing_aliases)
        else:
            seed_data[seed_key] = {
                "key": seed_key,
                "name": name,
                "node_type": node_type,
                "entity_subtype": node.get("entity_subtype"),
                "fact_count": 1,
            }
            seed_contexts[seed_key] = "; ".join(fact_contents) if fact_contents else name
            if node_aliases:
                seed_aliases[seed_key] = list(node_aliases)

        # Collect fact links
        extraction_role = node.get("extraction_role", "mentioned")
        for idx in fact_indices:
            if not isinstance(idx, int) or idx < 1 or idx > len(all_facts):
                continue
            fact = all_facts[idx - 1]
            fact_id = fact.id
            seed_links.append(
                {
                    "seed_key": seed_key,
                    "fact_id": fact_id,
                    "extraction_context": (fact.content[:500] if hasattr(fact, "content") and fact.content else None),
                    "extraction_role": extraction_role,
                }
            )
            if fact_id not in fact_to_seeds:
                fact_to_seeds[fact_id] = []
            fact_to_seeds[fact_id].append(seed_key)

    if not seed_data:
        return 0, []

    # ── Sub-phase B: Route seeds sequentially (DB-dependent) ─────────
    # Routing can modify seed state (merge chains, disambiguation) so
    # concurrent routing of the same key could conflict.

    key_remap: dict[str, str] = {}  # original_key -> resolved_key
    routed_seeds: dict[str, dict[str, Any]] = {}  # resolved_key -> seed_data
    phonetic_codes: dict[str, str] = {}  # seed_key -> phonetic_code

    for seed_key, sdata in seed_data.items():
        name = sdata["name"]
        node_type = sdata["node_type"]
        fact_context = seed_contexts.get(seed_key, name)

        resolved_key = seed_key
        try:
            from kt_facts.processing.seed_routing import (
                compute_phonetic_code,
                route_seed,
            )

            async with write_seed_repo._session.begin_nested():
                resolved_key = await route_seed(
                    name,
                    node_type,
                    fact_context,
                    write_seed_repo,
                    embedding_service=embedding_service,  # type: ignore[arg-type]
                    qdrant_seed_repo=qdrant_seed_repo,  # type: ignore[arg-type]
                    model_gateway=model_gateway,  # type: ignore[arg-type]
                )
                if resolved_key != seed_key:
                    # Store original name as alias on resolved seed
                    resolved_seed = await write_seed_repo.get_seed_by_key(resolved_key)
                    if resolved_seed:
                        meta = resolved_seed.metadata_ or {}
                        aliases = meta.get("aliases", [])
                        if name not in aliases:
                            aliases.append(name)
                            meta["aliases"] = aliases
                            from sqlalchemy import update as sa_update

                            from kt_db.write_models import WriteSeed

                            await write_seed_repo._session.execute(
                                sa_update(WriteSeed).where(WriteSeed.key == resolved_key).values(metadata_=meta)
                            )
                else:
                    phonetic_code = compute_phonetic_code(name)
                    if phonetic_code:
                        phonetic_codes[seed_key] = phonetic_code
        except Exception:
            logger.debug("Routing failed for '%s', will batch-upsert", name, exc_info=True)

        key_remap[seed_key] = resolved_key

        # Aggregate into routed_seeds (resolved key may differ)
        if resolved_key in routed_seeds:
            routed_seeds[resolved_key]["fact_count"] += sdata["fact_count"]
        else:
            routed_seeds[resolved_key] = {**sdata, "key": resolved_key}

    # Apply key remap to links and fact_to_seeds
    remapped_links: list[dict[str, Any]] = []
    remapped_fact_to_seeds: dict[object, list[str]] = {}
    for lnk in seed_links:
        resolved = key_remap.get(lnk["seed_key"], lnk["seed_key"])
        remapped_links.append({**lnk, "seed_key": resolved})

    for fact_id, keys in fact_to_seeds.items():
        remapped = [key_remap.get(k, k) for k in keys]
        remapped_fact_to_seeds[fact_id] = remapped

    # ── Sub-phase C: Batch write ─────────────────────────────────────

    # 1. Batch upsert seeds (single INSERT ... ON CONFLICT)
    try:
        async with write_seed_repo._session.begin_nested():
            await write_seed_repo.upsert_seeds_batch(list(routed_seeds.values()))
    except Exception:
        logger.exception("Batch seed upsert failed, falling back to sequential")
        for sdata in routed_seeds.values():
            try:
                async with write_seed_repo._session.begin_nested():
                    await write_seed_repo.upsert_seed(
                        sdata["key"],
                        sdata["name"],
                        sdata["node_type"],
                        sdata.get("entity_subtype"),
                    )
            except Exception:
                logger.exception("Error upserting seed '%s'", sdata["key"])

    # 1b. Batch update LLM-provided aliases on seeds
    if seed_aliases:
        alias_updates: list[tuple[str, list[str]]] = []
        for orig_key, aliases_list in seed_aliases.items():
            resolved = key_remap.get(orig_key, orig_key)
            if aliases_list:
                alias_updates.append((resolved, aliases_list))
        if alias_updates:
            try:
                async with write_seed_repo._session.begin_nested():
                    await write_seed_repo.update_aliases_batch(alias_updates)
            except Exception:
                logger.debug("Alias batch update failed", exc_info=True)

    # 2. Batch phonetic updates
    for seed_key, code in phonetic_codes.items():
        resolved = key_remap.get(seed_key, seed_key)
        try:
            async with write_seed_repo._session.begin_nested():
                await write_seed_repo.update_phonetic_code(resolved, code)
        except Exception:
            logger.debug("Phonetic update failed for '%s'", resolved, exc_info=True)

    # 3. Batch link facts (single INSERT ... ON CONFLICT DO NOTHING)
    total_links = 0
    try:
        async with write_seed_repo._session.begin_nested():
            total_links = await write_seed_repo.link_facts_batch(remapped_links)
    except Exception:
        logger.exception("Batch fact linking failed, falling back to sequential")
        for lnk in remapped_links:
            try:
                async with write_seed_repo._session.begin_nested():
                    is_new = await write_seed_repo.link_fact(
                        lnk["seed_key"],
                        lnk["fact_id"],
                        extraction_context=lnk.get("extraction_context"),
                    )
                    if is_new:
                        total_links += 1
            except Exception:
                logger.debug("Fact link failed", exc_info=True)

    # 3b. Refresh fact_count from actual WriteSeedFact rows
    all_linked_keys = list({lnk["seed_key"] for lnk in remapped_links})
    if all_linked_keys:
        try:
            async with write_seed_repo._session.begin_nested():
                await write_seed_repo.refresh_fact_counts(all_linked_keys)
        except Exception:
            logger.debug("Fact count refresh failed", exc_info=True)

    # 4. Batch edge candidates from co-occurring seeds
    edge_candidates: list[dict[str, Any]] = []
    for fact_id, seed_keys_in_fact in remapped_fact_to_seeds.items():
        unique_keys = list(dict.fromkeys(seed_keys_in_fact))
        for i, key_a in enumerate(unique_keys):
            for key_b in unique_keys[i + 1 :]:
                a, b = sorted([key_a, key_b])
                edge_candidates.append(
                    {
                        "seed_key_a": a,
                        "seed_key_b": b,
                        "fact_id": str(fact_id),
                    }
                )

    if edge_candidates:
        try:
            async with write_seed_repo._session.begin_nested():
                await write_seed_repo.upsert_edge_candidates_batch(edge_candidates)
        except Exception:
            logger.exception("Batch edge candidate upsert failed, falling back to sequential")
            for ec in edge_candidates:
                try:
                    async with write_seed_repo._session.begin_nested():
                        await write_seed_repo.upsert_edge_candidate(
                            ec["seed_key_a"],
                            ec["seed_key_b"],
                            ec["fact_id"],
                        )
                except Exception:
                    logger.debug(
                        "Edge candidate upsert failed for %s<->%s",
                        ec["seed_key_a"],
                        ec["seed_key_b"],
                        exc_info=True,
                    )

    all_seed_keys = list(routed_seeds.keys())

    return total_links, all_seed_keys


async def dedup_seeds_batch(
    seed_keys: list[str],
    write_seed_repo: WriteSeedRepository,
    embedding_service: object,
    qdrant_seed_repo: object,
    write_session_factory: async_sessionmaker[AsyncSession] | None = None,
    max_concurrency: int = 5,
    model_gateway: object | None = None,
    write_fact_repo: object | None = None,
) -> dict[str, str]:
    """Run dedup on a batch of recently extracted seeds.

    When *write_session_factory* is provided, dedup operations run in
    parallel with bounded concurrency (each gets its own session).
    Otherwise falls back to sequential execution on the shared session.

    Returns a mapping of original_key -> surviving_key for any merges.
    """
    from kt_facts.processing.seed_dedup import deduplicate_seed

    # Batch-fetch all seeds in one query to filter early
    unique_keys = list(dict.fromkeys(seed_keys))
    seeds_by_key = await write_seed_repo.get_seeds_by_keys_batch(unique_keys)
    active_seeds = [(k, s) for k, s in seeds_by_key.items() if s.status == "active"]

    if not active_seeds:
        return {}

    merges: dict[str, str] = {}

    if write_session_factory is not None:
        # Parallel dedup with separate sessions
        sem = asyncio.Semaphore(max_concurrency)

        async def _dedup_one(seed_key: str, name: str, node_type: str) -> tuple[str, str]:
            async with sem:
                async with write_session_factory() as session:
                    async with session.begin():
                        from kt_db.repositories.write_seeds import WriteSeedRepository as _WSR

                        repo = _WSR(session)
                        # Build per-session write_fact_repo if model_gateway available
                        _wfr = None
                        if model_gateway is not None:
                            from kt_db.repositories.write_facts import WriteFactRepository

                            _wfr = WriteFactRepository(session)
                        surviving = await deduplicate_seed(
                            seed_key=seed_key,
                            name=name,
                            node_type=node_type,
                            write_seed_repo=repo,
                            embedding_service=embedding_service,
                            qdrant_seed_repo=qdrant_seed_repo,
                            model_gateway=model_gateway,
                            write_fact_repo=_wfr,
                        )
                        return seed_key, surviving

        tasks = [_dedup_one(k, s.name, s.node_type) for k, s in active_seeds]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for r in results:
            if isinstance(r, BaseException):
                logger.exception("Parallel dedup failed: %s", r)
                continue
            seed_key, surviving = r
            if surviving != seed_key:
                merges[seed_key] = surviving
    else:
        # Sequential fallback
        for seed_key, seed in active_seeds:
            try:
                surviving_key = await deduplicate_seed(
                    seed_key=seed_key,
                    name=seed.name,
                    node_type=seed.node_type,
                    write_seed_repo=write_seed_repo,
                    embedding_service=embedding_service,
                    qdrant_seed_repo=qdrant_seed_repo,
                    model_gateway=model_gateway,
                    write_fact_repo=write_fact_repo,
                )
                if surviving_key != seed_key:
                    merges[seed_key] = surviving_key
            except Exception:
                logger.exception("Dedup failed for seed '%s'", seed_key)

    return merges
