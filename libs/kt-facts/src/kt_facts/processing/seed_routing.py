"""Seed routing — routes new facts to the correct disambiguated seed.

When a seed has been split into disambiguated children (e.g., "Mars" ->
"Mars (planet)" + "Mars (Roman god)"), this module routes new mentions
of the ambiguous name to the correct child via contextual embedding
comparison.

Also handles merged-seed chain following and phonetic typo detection.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from kt_config.settings import get_settings
from kt_db.keys import make_seed_key
from kt_facts.processing.seed_heuristics import (
    build_seed_context,
    compute_context_hash,
    compute_phonetic_code,
    text_search_route,
)

if TYPE_CHECKING:
    from kt_db.repositories.write_seeds import WriteSeedRepository
    from kt_models.embeddings import EmbeddingService
    from kt_models.gateway import ModelGateway
    from kt_qdrant.repositories.seeds import QdrantSeedRepository

logger = logging.getLogger(__name__)

MAX_MERGE_HOPS = 5
MAX_ROUTE_DEPTH = 5

# Backward-compat alias for private name
_text_search_route = text_search_route


async def route_seed(
    name: str,
    fact_content: str,
    write_seed_repo: WriteSeedRepository,
    embedding_service: EmbeddingService | None = None,
    qdrant_seed_repo: QdrantSeedRepository | None = None,
    model_gateway: ModelGateway | None = None,
) -> str:
    """Route a mention to the correct seed key.

    Algorithm:
    1. Look up seed by key
    2. Active/Promoted -> return key (normal path)
    3. Merged -> follow merged_into_key chain, then re-check status
    4. Ambiguous -> route through pipe (embedding comparison to children)
    5. Not found -> check phonetic matches against ambiguous parents

    Returns the resolved seed key.
    """
    key = make_seed_key(name)

    seed = await write_seed_repo.get_seed_by_key(key)

    if seed is None:
        # Not found — check phonetic matches for typo detection
        return await _phonetic_route(
            name,
            key,
            fact_content,
            write_seed_repo,
            embedding_service,
            qdrant_seed_repo,
            model_gateway,
        )

    # Follow merge chain if needed
    if seed.status == "merged":
        resolved_key = await _follow_merge_chain(seed.merged_into_key, write_seed_repo)
        if resolved_key is None:
            return key
        resolved_seed = await write_seed_repo.get_seed_by_key(resolved_key)
        if resolved_seed is None:
            return key
        seed = resolved_seed
        key = resolved_key

    # Resolve through pipes iteratively (handles active, ambiguous, etc.)
    return await _resolve_through_pipes(
        key,
        seed,
        fact_content,
        write_seed_repo,
        embedding_service,
        qdrant_seed_repo,
        model_gateway,
    )


async def _resolve_through_pipes(
    key: str,
    seed: object,  # WriteSeed
    fact_content: str,
    write_seed_repo: WriteSeedRepository,
    embedding_service: EmbeddingService | None = None,
    qdrant_seed_repo: QdrantSeedRepository | None = None,
    model_gateway: ModelGateway | None = None,
) -> str:
    """Iteratively route through ambiguous pipes until reaching a leaf."""
    current_key = key
    current_seed = seed
    for _ in range(MAX_ROUTE_DEPTH):
        status = getattr(current_seed, "status", None)
        if status in ("active", "promoted"):
            return current_key
        if status != "ambiguous":
            return current_key
        routed = await _route_through_pipe(
            current_key,
            fact_content,
            write_seed_repo,
            embedding_service,
            qdrant_seed_repo,
            model_gateway,
        )
        if not routed or routed == current_key:
            return current_key
        routed_seed = await write_seed_repo.get_seed_by_key(routed)
        if routed_seed is None:
            return routed
        current_seed = routed_seed
        current_key = routed
    return current_key


async def _follow_merge_chain(
    start_key: str | None,
    write_seed_repo: WriteSeedRepository,
) -> str | None:
    """Follow merged_into_key chain up to MAX_MERGE_HOPS."""
    current = start_key
    for _ in range(MAX_MERGE_HOPS):
        if current is None:
            return None
        seed = await write_seed_repo.get_seed_by_key(current)
        if seed is None:
            return None
        if seed.status != "merged":
            return current
        current = seed.merged_into_key
    logger.warning("Merge chain exceeded %d hops from '%s'", MAX_MERGE_HOPS, start_key)
    return current


async def _route_through_pipe(
    parent_key: str,
    fact_content: str,
    write_seed_repo: WriteSeedRepository,
    embedding_service: EmbeddingService | None = None,
    qdrant_seed_repo: QdrantSeedRepository | None = None,
    model_gateway: ModelGateway | None = None,
) -> str | None:
    """Route a fact through disambiguation pipe to the best child seed."""
    settings = get_settings()

    routes = await write_seed_repo.get_routes_for_parent(parent_key)
    if not routes:
        return None

    if len(routes) == 1:
        return routes[0].child_seed_key

    # Check ambiguity type from routes (all routes for a parent share the type)
    ambiguity_type = getattr(routes[0], "ambiguity_type", "text")

    if ambiguity_type == "embedding":
        # ── Embedding ambiguity: text search first ──
        result = _text_search_route(fact_content, routes)
        if result:
            return result
        # Text search failed → LLM fallback
        if model_gateway is not None:
            return await _llm_select_child(
                fact_content,
                [],
                routes,
                model_gateway,
            )
        return routes[0].child_seed_key

    # ── Text ambiguity: embedding distance (existing behavior) ──
    if embedding_service is None or qdrant_seed_repo is None:
        # Without embeddings, route to first child
        return routes[0].child_seed_key

    try:
        fact_embedding = await embedding_service.embed_text(fact_content)

        # Search for similar seeds among children
        child_keys = {r.child_seed_key for r in routes}
        similar = await qdrant_seed_repo.find_similar(
            embedding=fact_embedding,
            limit=len(child_keys),
            score_threshold=0.0,  # get all scores
        )

        # Filter to only child seeds
        child_matches = [m for m in similar if m.seed_key in child_keys]

        if not child_matches:
            return routes[0].child_seed_key

        best = child_matches[0]

        if best.score >= settings.seed_routing_embedding_threshold:
            # Check if top-2 are too close (ambiguous)
            if (
                len(child_matches) >= 2
                and (best.score - child_matches[1].score) < settings.seed_routing_llm_ambiguity_margin
                and model_gateway is not None
            ):
                return await _llm_select_child(
                    fact_content,
                    child_matches[:2],
                    routes,
                    model_gateway,
                )
            return best.seed_key

        # Below threshold — route to best match anyway with warning
        logger.warning(
            "Routing to best child '%s' for parent '%s' with low score %.3f",
            best.seed_key,
            parent_key,
            best.score,
        )
        return best.seed_key

    except Exception:
        logger.debug("Pipe routing failed for parent '%s'", parent_key, exc_info=True)
        return routes[0].child_seed_key


async def _llm_select_child(
    fact_content: str,
    top_matches: list,
    routes: list,
    model_gateway: ModelGateway,
) -> str:
    """Ask LLM to select the correct child when embedding scores are ambiguous."""
    # Build options list — from top_matches if available, otherwise from routes
    if top_matches:
        route_map = {r.child_seed_key: r.label for r in routes}
        options = []
        items = []
        for i, m in enumerate(top_matches, 1):
            label = route_map.get(m.seed_key, m.seed_key)
            options.append(f"{i}. {label} (key: {m.seed_key})")
            items.append(m.seed_key)
    else:
        options = []
        items = []
        for i, r in enumerate(routes, 1):
            options.append(f"{i}. {r.label} (key: {r.child_seed_key})")
            items.append(r.child_seed_key)

    prompt = (
        f'Given this fact:\n"{fact_content[:500]}"\n\n'
        f"Which entity does it refer to?\n" + "\n".join(options) + "\n\n"
        'Respond with JSON: {"choice": <number>}'
    )

    try:
        result = await model_gateway.generate_json(
            model_id=model_gateway.default_model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0,
        )
        if isinstance(result, dict):
            choice = result.get("choice", 1)
            idx = max(0, min(int(choice) - 1, len(items) - 1))
            return items[idx]
    except Exception:
        logger.debug("LLM child selection failed", exc_info=True)

    return items[0] if items else routes[0].child_seed_key


async def _phonetic_route(
    name: str,
    original_key: str,
    fact_content: str,
    write_seed_repo: WriteSeedRepository,
    embedding_service: EmbeddingService | None = None,
    qdrant_seed_repo: QdrantSeedRepository | None = None,
    model_gateway: ModelGateway | None = None,
) -> str:
    """Check phonetic matches for typo detection of ambiguous parents.

    If a phonetic match finds an ambiguous parent, routes through its pipe.
    """
    settings = get_settings()
    phonetic_code = compute_phonetic_code(name)
    if not phonetic_code:
        return original_key

    try:
        phonetic_matches = await write_seed_repo.find_by_phonetic(
            phonetic_code,
            "concept",
            limit=5,
        )
        for candidate in phonetic_matches:
            if candidate.key == original_key:
                continue

            # Require trigram confirmation for phonetic matches
            try:
                similar_seeds = await write_seed_repo.find_similar_seeds(
                    name,
                    "concept",
                    limit=1,
                    threshold=settings.seed_phonetic_trigram_threshold,
                )
                trigram_confirmed = any(s.key == candidate.key for s in similar_seeds)
            except Exception:
                trigram_confirmed = False

            if not trigram_confirmed:
                continue

            if candidate.status == "ambiguous":
                return await _resolve_through_pipes(
                    candidate.key,
                    candidate,
                    fact_content,
                    write_seed_repo,
                    embedding_service,
                    qdrant_seed_repo,
                    model_gateway,
                )

            if candidate.status in ("active", "promoted"):
                return candidate.key

    except Exception:
        logger.debug("Phonetic routing failed for '%s'", name, exc_info=True)

    return original_key


async def maybe_re_embed_seed(
    seed_key: str,
    fact_count: int,
    write_seed_repo: WriteSeedRepository,
    embedding_service: EmbeddingService | None = None,
    qdrant_seed_repo: QdrantSeedRepository | None = None,
    write_fact_repo: object | None = None,
) -> None:
    """Re-embed a seed at fact count thresholds for richer contextual representation."""
    if embedding_service is None or qdrant_seed_repo is None:
        return

    settings = get_settings()
    thresholds = [int(t) for t in settings.seed_re_embed_thresholds.split(",")]

    if fact_count not in thresholds:
        return

    seed = await write_seed_repo.get_seed_by_key(seed_key)
    if seed is None:
        return

    try:
        # Load top facts for context
        top_facts: list[str] = []
        if write_fact_repo is not None:
            fact_ids = await write_seed_repo.get_facts_for_seed(seed_key)
            if fact_ids:
                loaded = await write_fact_repo.get_by_ids(fact_ids[:5])  # type: ignore[union-attr]
                top_facts = [f.content for f in loaded if hasattr(f, "content")]

        meta = seed.metadata_ or {}
        aliases = meta.get("aliases", [])

        context_text = build_seed_context(
            seed.name,
            seed.node_type,
            top_facts=top_facts,
            aliases=aliases,
        )
        new_hash = compute_context_hash(context_text)

        if seed.context_hash == new_hash:
            return  # No change in context

        embedding = await embedding_service.embed_text(context_text)
        await qdrant_seed_repo.upsert(
            seed_key=seed_key,
            embedding=embedding,
            name=seed.name,
            node_type=seed.node_type,
            context_text=context_text,
        )
        await write_seed_repo.update_context_hash(seed_key, new_hash)
        logger.info(
            "Re-embedded seed '%s' at fact_count=%d with contextual embedding",
            seed_key,
            fact_count,
        )
    except Exception:
        logger.debug("Re-embed failed for seed '%s'", seed_key, exc_info=True)


async def batch_re_embed_seeds(
    seed_keys: list[str],
    write_seed_repo: WriteSeedRepository,
    embedding_service: EmbeddingService | None = None,
    qdrant_seed_repo: QdrantSeedRepository | None = None,
    write_fact_repo: object | None = None,
) -> int:
    """Batch re-embed seeds that crossed fact-count thresholds.

    Collects all seeds needing re-embedding, batch-embeds their context texts
    in one API call, then batch-upserts to Qdrant. Much faster than per-seed
    sequential calls when many seeds cross thresholds simultaneously.

    Returns:
        Number of seeds re-embedded.
    """
    if embedding_service is None or qdrant_seed_repo is None:
        return 0

    settings = get_settings()
    thresholds = set(int(t) for t in settings.seed_re_embed_thresholds.split(","))

    # Phase 1: Collect seeds that need re-embedding
    to_embed: list[tuple[object, str]] = []  # (seed, context_text)
    for seed_key in seed_keys:
        try:
            seed = await write_seed_repo.get_seed_by_key(seed_key)
            if seed is None or seed.fact_count not in thresholds:
                continue

            # Build context text
            top_facts: list[str] = []
            if write_fact_repo is not None:
                fact_ids = await write_seed_repo.get_facts_for_seed(seed_key)
                if fact_ids:
                    loaded = await write_fact_repo.get_by_ids(fact_ids[:5])  # type: ignore[union-attr]
                    top_facts = [f.content for f in loaded if hasattr(f, "content")]

            meta = seed.metadata_ or {}
            aliases = meta.get("aliases", [])

            context_text = build_seed_context(
                seed.name,
                seed.node_type,
                top_facts=top_facts,
                aliases=aliases,
            )
            new_hash = compute_context_hash(context_text)

            if seed.context_hash == new_hash:
                continue

            to_embed.append((seed, context_text))
        except Exception:
            logger.debug("Re-embed prep failed for '%s'", seed_key, exc_info=True)

    if not to_embed:
        return 0

    # Phase 2: Batch embed all context texts
    try:
        context_texts = [ctx for _, ctx in to_embed]
        embeddings = await embedding_service.embed_batch(context_texts)
    except Exception:
        logger.warning("Batch embedding failed for %d seeds", len(to_embed), exc_info=True)
        return 0

    # Phase 3: Batch upsert to Qdrant + update hashes
    qdrant_batch = []
    count = 0
    for (seed, context_text), embedding in zip(to_embed, embeddings):
        if embedding is None:
            continue
        qdrant_batch.append(
            {
                "seed_key": seed.key,
                "embedding": embedding,
                "name": seed.name,
                "node_type": seed.node_type,
                "context_text": context_text,
            }
        )

    if qdrant_batch:
        try:
            await qdrant_seed_repo.upsert_batch(qdrant_batch)
        except Exception:
            logger.warning("Batch Qdrant upsert failed for %d seeds", len(qdrant_batch), exc_info=True)
            return 0

    # Phase 4: Update context hashes
    for (seed, context_text), embedding in zip(to_embed, embeddings):
        if embedding is None:
            continue
        try:
            new_hash = compute_context_hash(context_text)
            await write_seed_repo.update_context_hash(seed.key, new_hash)
            count += 1
        except Exception:
            logger.debug("Hash update failed for '%s'", seed.key, exc_info=True)

    if count:
        logger.info("Batch re-embedded %d seeds (of %d candidates)", count, len(to_embed))
    return count
