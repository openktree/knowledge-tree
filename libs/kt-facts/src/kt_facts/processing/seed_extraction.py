"""Seed extraction — stores entity/concept mentions as seeds during fact decomposition.

Seeds are lightweight proto-nodes that track which entities and concepts are
mentioned across facts. When enough facts accumulate for a seed, it can be
promoted to a full node with pre-accumulated evidence.

Pipeline:
  A. Pre-compute + in-memory dedup (pure Python, no DB)
       - group by make_seed_key(name) — fold exact key duplicates
       - alias DSU — fold seeds whose alias key matches another seed's canonical key
       - longest-wins canonical within each group
  B. Batch write (status=pending) + Qdrant embed
       - upsert_seeds_batch_with_aliases(status='pending')
       - embed canonical → Qdrant upsert
       - link_facts_batch, upsert_edge_candidates_batch
  Dedup runs separately after this function returns (called by caller).
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

    # ── Sub-phase A: Pre-compute + in-memory dedup (pure Python) ─────

    # Pass 1: collect per-seed data grouped by key
    seed_data: dict[str, dict[str, Any]] = {}  # key -> {name, node_type, aliases, ...}
    seed_links: list[dict[str, Any]] = []       # [{seed_key, fact_id, ...}]
    fact_to_seeds: dict[object, list[str]] = {} # fact_id -> [seed_keys]

    for node in extracted_nodes:
        name = node.get("name")
        node_type = node.get("node_type", "concept")
        if not name:
            continue

        seed_key = make_seed_key(name)
        fact_indices = node.get("fact_indices", [])
        node_aliases: list[str] = node.get("aliases", [])  # from entity extractor

        if seed_key in seed_data:
            seed_data[seed_key]["fact_count"] += 1
            # Merge aliases (dedup)
            existing = set(seed_data[seed_key]["aliases"])
            for a in node_aliases:
                a_key = make_seed_key(a)
                existing.add(a_key)
            seed_data[seed_key]["aliases"] = list(existing)
            # Longest-wins canonical within same key group
            if len(name) > len(seed_data[seed_key]["name"]):
                seed_data[seed_key]["name"] = name
        else:
            seed_data[seed_key] = {
                "key": seed_key,
                "name": name,
                "node_type": node_type,
                "entity_subtype": None,
                "fact_count": 1,
                "aliases": [make_seed_key(a) for a in node_aliases if a],
            }

        extraction_role = node.get("extraction_role", "mentioned")
        for idx in fact_indices:
            if not isinstance(idx, int) or idx < 1 or idx > len(all_facts):
                continue
            fact = all_facts[idx - 1]
            fact_id = fact.id
            seed_links.append({
                "seed_key": seed_key,
                "fact_id": fact_id,
                "extraction_context": (fact.content[:500] if hasattr(fact, "content") and fact.content else None),
                "extraction_role": extraction_role,
            })
            fact_to_seeds.setdefault(fact_id, []).append(seed_key)

    if not seed_data:
        return 0, []

    # Pass 2: alias DSU — fold seeds whose alias key matches another seed's canonical key
    # Build index: alias_key -> seed_key that owns it
    alias_to_canonical: dict[str, str] = {}
    for sk, sdata in seed_data.items():
        alias_to_canonical[sk] = sk  # canonical maps to itself
        for ak in sdata["aliases"]:
            if ak not in alias_to_canonical:
                alias_to_canonical[ak] = sk

    # DSU
    dsu: dict[str, str] = {sk: sk for sk in seed_data}

    def _find(x: str) -> str:
        while dsu.get(x, x) != x:
            dsu[x] = dsu.get(dsu[x], dsu[x])
            x = dsu[x]
        return x

    def _union(a: str, b: str) -> None:
        ra, rb = _find(a), _find(b)
        if ra != rb:
            # Longest-wins: keep longer name's key as root
            name_a = seed_data.get(ra, {}).get("name", ra)
            name_b = seed_data.get(rb, {}).get("name", rb)
            if len(name_b) > len(name_a):
                dsu[ra] = rb
            else:
                dsu[rb] = ra

    # If any seed's alias key matches another seed's canonical key → union them
    for sk, sdata in seed_data.items():
        for ak in sdata["aliases"]:
            if ak in seed_data and ak != sk:
                _union(sk, ak)

    # Build representative groups
    groups: dict[str, list[str]] = {}
    for sk in seed_data:
        root = _find(sk)
        groups.setdefault(root, []).append(sk)

    # Merge group members into representative
    representatives: dict[str, dict[str, Any]] = {}
    key_remap: dict[str, str] = {}  # original_key -> representative_key

    for root, members in groups.items():
        # Pick representative: highest fact_count; on tie, longest name
        rep_key = max(
            members,
            key=lambda k: (seed_data[k]["fact_count"], len(seed_data[k]["name"])),
        )
        rep_data = dict(seed_data[rep_key])
        merged_aliases: set[str] = set(rep_data["aliases"])
        for m in members:
            if m != rep_key:
                merged_aliases.add(m)  # member's own key becomes an alias
                merged_aliases.update(seed_data[m]["aliases"])
                rep_data["fact_count"] += seed_data[m]["fact_count"]
                if len(seed_data[m]["name"]) > len(rep_data["name"]):
                    rep_data["name"] = seed_data[m]["name"]
        rep_data["aliases"] = list(merged_aliases - {rep_key})  # exclude self from aliases
        representatives[rep_key] = rep_data
        for m in members:
            key_remap[m] = rep_key

    # Remap links and fact_to_seeds to representatives
    remapped_links: list[dict[str, Any]] = []
    remapped_fact_to_seeds: dict[object, list[str]] = {}
    for lnk in seed_links:
        resolved = key_remap.get(lnk["seed_key"], lnk["seed_key"])
        remapped_links.append({**lnk, "seed_key": resolved})
    for fact_id, keys in fact_to_seeds.items():
        remapped_fact_to_seeds[fact_id] = [key_remap.get(k, k) for k in keys]

    # ── Sub-phase B: Batch write (status=pending) + Qdrant embed ──────

    # 1. Upsert seeds as pending with aliases column
    seeds_to_upsert = list(representatives.values())
    try:
        async with write_seed_repo._session.begin_nested():
            await write_seed_repo.upsert_seeds_batch_with_aliases(seeds_to_upsert, status="pending")
    except Exception:
        logger.exception("Batch seed upsert (pending) failed, falling back to sequential")
        for sdata in seeds_to_upsert:
            try:
                async with write_seed_repo._session.begin_nested():
                    await write_seed_repo.upsert_seed_with_aliases(
                        sdata["key"],
                        sdata["name"],
                        sdata["node_type"],
                        sdata.get("entity_subtype"),
                        sdata.get("aliases", []),
                        status="pending",
                    )
            except Exception:
                logger.exception("Error upserting seed '%s'", sdata["key"])

    # 2. Embed each canonical name → Qdrant upsert (one point per seed)
    from kt_facts.processing.seed_dedup import embed_and_upsert_seed

    for sdata in seeds_to_upsert:
        try:
            await embed_and_upsert_seed(
                seed_key=sdata["key"],
                name=sdata["name"],
                node_type=sdata["node_type"],
                embedding_service=embedding_service,  # type: ignore[arg-type]
                qdrant_seed_repo=qdrant_seed_repo,  # type: ignore[arg-type]
            )
        except Exception:
            logger.debug("embed_and_upsert_seed failed for '%s'", sdata["key"], exc_info=True)

    # 3. Link facts
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

    # 4. Refresh fact_count
    all_linked_keys = list({lnk["seed_key"] for lnk in remapped_links})
    if all_linked_keys:
        try:
            async with write_seed_repo._session.begin_nested():
                await write_seed_repo.refresh_fact_counts(all_linked_keys)
        except Exception:
            logger.debug("Fact count refresh failed", exc_info=True)

    # 5. Edge candidates from co-occurring seeds
    edge_candidates: list[dict[str, Any]] = []
    for fact_id, seed_keys_in_fact in remapped_fact_to_seeds.items():
        unique_keys = list(dict.fromkeys(seed_keys_in_fact))
        for i, key_a in enumerate(unique_keys):
            for key_b in unique_keys[i + 1:]:
                a, b = sorted([key_a, key_b])
                edge_candidates.append({"seed_key_a": a, "seed_key_b": b, "fact_id": str(fact_id)})

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
                            ec["seed_key_a"], ec["seed_key_b"], ec["fact_id"],
                        )
                except Exception:
                    logger.debug(
                        "Edge candidate upsert failed for %s<->%s",
                        ec["seed_key_a"], ec["seed_key_b"], exc_info=True,
                    )

    return total_links, list(representatives.keys())


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
