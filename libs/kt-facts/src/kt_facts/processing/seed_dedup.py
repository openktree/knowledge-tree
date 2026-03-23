"""Seed deduplication — detects and merges duplicate seeds.

Signals (in order):
0. Key collision — handled automatically by make_seed_key() determinism
1. Alias match — exact name/alias string match via trigram candidate discovery
2. Embedding similarity — embed seed name, search Qdrant (PRIMARY merge signal)
3. Phonetic + trigram typo catch — requires embedding floor confirmation
4. Disambiguation trigger — confusable pair logging (no merge)

When a match is found, the losing seed (fewer facts or less canonical name)
is merged into the winner.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from kt_config.settings import get_settings
from kt_facts.processing.seed_heuristics import (
    edit_distance,
    is_acronym_match,
    is_containment_mismatch,
    is_prefix_disambiguation_candidate,
    is_safe_auto_merge,
)

if TYPE_CHECKING:
    from kt_db.repositories.write_facts import WriteFactRepository
    from kt_db.repositories.write_seeds import WriteSeedRepository
    from kt_models.embeddings import EmbeddingService
    from kt_models.gateway import ModelGateway
    from kt_qdrant.repositories.seeds import QdrantSeedRepository

logger = logging.getLogger(__name__)


# Backward-compat aliases for private names
_is_acronym_match = is_acronym_match
_is_containment_mismatch = is_containment_mismatch
_is_prefix_disambiguation_candidate = is_prefix_disambiguation_candidate
_edit_distance = edit_distance


class _MergeCandidate:
    """A potential merge target discovered by a dedup signal."""

    __slots__ = ("key", "name", "reason")

    def __init__(self, key: str, name: str, reason: str) -> None:
        self.key = key
        self.name = name
        self.reason = reason


async def deduplicate_seed(
    seed_key: str,
    name: str,
    node_type: str,
    write_seed_repo: WriteSeedRepository,
    embedding_service: EmbeddingService,
    qdrant_seed_repo: QdrantSeedRepository,
    model_gateway: ModelGateway | None = None,
    write_fact_repo: WriteFactRepository | None = None,
) -> str:
    """Check for duplicates of a seed and merge if found.

    Returns the surviving seed key (may be different from input if merged).

    Uses a collect-then-decide pattern: all signals contribute candidates,
    then the decision logic handles single match (merge), multi-match with
    shared ancestor (merge into ancestor), or multi-match with distinct
    entities (mark as ambiguous).

    Dedup signals (in order):
    0. Alias matching — exact name/alias string match via trigram candidate discovery
    0b. Reverse alias lookup — existing seed has incoming name as alias
    1. Embedding similarity — Qdrant vector search (PRIMARY merge signal)
    2. Phonetic + trigram typo catch — requires embedding floor confirmation
    3. Disambiguation trigger — confusable pair logging (no merge)
    """
    # Skip dedup entirely for seeds with invalid names — prevents junk seeds
    # from merging with good seeds and amplifying the corruption cascade.
    from kt_facts.processing.entity_extraction import _is_valid_entity_name
    if not _is_valid_entity_name(name):
        logger.debug("Skipping dedup for invalid seed name: '%s'", name)
        return seed_key

    settings = get_settings()

    # Collect all merge candidates from alias/acronym signals
    alias_candidates: list[_MergeCandidate] = []

    # Track best embedding score for disambiguation logging
    best_embedding_score: float = 0.0
    trigram_candidates_found: list[str] = []

    # ── Signal 0: Alias match (trigram finds candidates, exact match merges) ──
    try:
        similar_seeds = await write_seed_repo.find_similar_seeds(
            name,
            node_type,
            limit=5,
            threshold=settings.seed_dedup_trigram_threshold,
        )
        for candidate in similar_seeds:
            if candidate.key == seed_key:
                continue
            if candidate.status not in ("active", "promoted"):
                continue

            # Track trigram candidate for disambiguation logging
            trigram_candidates_found.append(candidate.name)

            # Check alias match — exact name or alias string match
            meta = candidate.metadata_ or {}
            aliases = meta.get("aliases", [])
            name_lower = name.lower().strip()
            candidate_name_lower = candidate.name.lower().strip()
            aliases_lower = [a.lower() for a in aliases]

            # Acronym heuristic: "FBI" <-> "Federal Bureau of Investigation"
            is_acronym = _is_acronym_match(name, candidate.name)

            if name_lower == candidate_name_lower or name_lower in aliases_lower or is_acronym:
                # Containment guard — skip for acronym matches since acronyms
                # are inherently much shorter than their expansions
                if not is_acronym and _is_containment_mismatch(name_lower, candidate_name_lower):
                    logger.debug(
                        "Skipping alias match '%s' <-> '%s' — containment mismatch",
                        name, candidate.name,
                    )
                    continue
                reason = "acronym match" if is_acronym and name_lower != candidate_name_lower and name_lower not in aliases_lower else "alias match"
                alias_candidates.append(_MergeCandidate(candidate.key, candidate.name, reason))
    except Exception:
        logger.debug("Alias/trigram candidate search failed for seed '%s'", name, exc_info=True)

    # ── Signal 0b: Reverse alias lookup ──────────────────────────────
    try:
        reverse_matches = await write_seed_repo.find_seeds_by_alias(name, node_type)
        for candidate in reverse_matches:
            if candidate.key == seed_key:
                continue
            if candidate.status not in ("active", "promoted"):
                continue
            # Skip if already collected
            if any(c.key == candidate.key for c in alias_candidates):
                continue
            # Containment guard
            name_lower = name.lower().strip()
            candidate_name_lower = candidate.name.lower().strip()
            if _is_containment_mismatch(name_lower, candidate_name_lower):
                continue
            alias_candidates.append(_MergeCandidate(candidate.key, candidate.name, "reverse alias match"))
    except Exception:
        logger.debug("Reverse alias lookup failed for seed '%s'", name, exc_info=True)

    # ── Decide on alias/acronym candidates ──────────────────────────
    if alias_candidates:
        # Deduplicate by key
        seen_keys: set[str] = set()
        unique_candidates: list[_MergeCandidate] = []
        for c in alias_candidates:
            if c.key not in seen_keys:
                seen_keys.add(c.key)
                unique_candidates.append(c)

        if len(unique_candidates) == 1:
            # Single match — merge directly
            return await _merge_pair(seed_key, unique_candidates[0].key, write_seed_repo, reason=unique_candidates[0].reason)
        else:
            # Multiple distinct candidates — check if they share a merged ancestor
            result = await _handle_multi_match(seed_key, unique_candidates, write_seed_repo)
            if result is not None:
                return result

    # ── Signal 1: Embedding similarity (PRIMARY merge signal) ──
    try:
        embedding = await embedding_service.embed_text(name)
    except Exception:
        logger.warning(
            "Embedding API failed for seed '%s' — skipping embedding dedup",
            name, exc_info=True,
        )
        embedding = None

    if embedding is not None:
        try:
            # Upsert this seed's embedding to Qdrant
            await qdrant_seed_repo.upsert(
                seed_key=seed_key,
                embedding=embedding,
                name=name,
                node_type=node_type,
            )

            # Search for similar seeds — use typo_floor as threshold to get
            # sub-threshold matches in a single query (avoids second Qdrant call)
            all_similar = await qdrant_seed_repo.find_similar(
                embedding=embedding,
                node_type=node_type,
                limit=5,
                score_threshold=settings.seed_dedup_typo_floor,
                exclude_keys={seed_key},
            )

            # Split into merge candidates (above threshold) and sub-threshold
            similar = [m for m in all_similar if m.score >= settings.seed_dedup_embedding_threshold]
            if all_similar:
                best_embedding_score = max(m.score for m in all_similar)

            for match in similar:
                if match.seed_key == seed_key:
                    continue
                # Verify the matched seed still exists and is active
                matched_seed = await write_seed_repo.get_seed_by_key(match.seed_key)
                if matched_seed is None or matched_seed.status not in ("active", "promoted"):
                    continue

                # ── Tiered merge decision ──
                # Tier 1: Very high embedding + string guards → auto-merge (skip LLM)
                if is_safe_auto_merge(
                    name, matched_seed.name,
                    embedding_score=match.score,
                    auto_merge_threshold=settings.seed_dedup_auto_merge_threshold,
                ):
                    preferred_name = None
                    reason = f"embedding auto-merge (score={match.score:.3f})"
                    logger.info(
                        "Auto-merge (skipped LLM): '%s' vs '%s' (score=%.3f)",
                        name, matched_seed.name, match.score,
                    )
                # Tier 2: Moderate embedding → LLM confirmation required
                elif model_gateway is not None and write_fact_repo is not None:
                    confirmed, preferred_name = await _llm_confirm_merge(
                        incoming_name=name,
                        incoming_key=seed_key,
                        candidate_name=matched_seed.name,
                        candidate_key=match.seed_key,
                        write_seed_repo=write_seed_repo,
                        write_fact_repo=write_fact_repo,
                        model_gateway=model_gateway,
                    )
                    if not confirmed:
                        # LLM says different — create embedding-ambiguity routes
                        await _create_embedding_disambiguation(
                            seed_key, match.seed_key, matched_seed.name,
                            write_seed_repo,
                        )
                        continue  # try next candidate

                    reason = f"embedding + LLM confirmed (score={match.score:.3f})"
                else:
                    preferred_name = None
                    reason = f"embedding similarity (score={match.score:.3f})"

                winner_key = await _merge_pair(
                    seed_key, match.seed_key, write_seed_repo,
                    reason=reason,
                )

                # Specificity upgrade: rename winner to the more specific name
                if preferred_name and winner_key:
                    await write_seed_repo.rename_seed(winner_key, preferred_name)

                return winner_key

        except Exception:
            logger.warning(
                "Qdrant search failed for seed '%s' — skipping embedding dedup",
                name, exc_info=True,
            )

    # ── Signal 2: Phonetic + trigram typo catch (requires embedding floor) ──
    if best_embedding_score >= settings.seed_dedup_typo_floor:
        try:
            from kt_facts.processing.seed_heuristics import compute_phonetic_code

            phonetic_code = compute_phonetic_code(name)
            if phonetic_code:
                phonetic_matches = await write_seed_repo.find_by_phonetic(
                    phonetic_code, node_type, limit=5,
                )
                for candidate in phonetic_matches:
                    if candidate.key == seed_key:
                        continue
                    if candidate.status not in ("active", "promoted"):
                        continue
                    # Containment guard — block merges where names differ
                    # by distinguishing words (e.g. "Kim Jong-un" vs "Kim Jong-il")
                    if _is_containment_mismatch(name.lower(), candidate.name.lower()):
                        logger.debug(
                            "Phonetic match '%s' <-> '%s' blocked by containment guard",
                            name, candidate.name,
                        )
                        continue
                    # Require moderate trigram confirmation
                    try:
                        trigram_matches = await write_seed_repo.find_similar_seeds(
                            name, node_type, limit=1,
                            threshold=settings.seed_phonetic_trigram_threshold,
                        )
                        if any(m.key == candidate.key for m in trigram_matches):
                            return await _merge_pair(
                                seed_key, candidate.key, write_seed_repo,
                                reason=f"phonetic + trigram match (code={phonetic_code}, emb={best_embedding_score:.3f})",
                            )
                    except Exception:
                        pass
        except Exception:
            logger.debug("Phonetic dedup failed for seed '%s'", name, exc_info=True)

    # ── Signal 3: Disambiguation trigger (awareness logging, no merge) ──
    if trigram_candidates_found and best_embedding_score < settings.seed_dedup_embedding_threshold:
        # Trigram found high-similarity candidates but embedding score was low —
        # these are confusable names for different entities.
        if best_embedding_score > 0:
            logger.info(
                "Confusable pair detected: '%s' vs trigram candidates %s "
                "(best_embedding_score=%.3f, below threshold=%.2f)",
                name, trigram_candidates_found, best_embedding_score,
                settings.seed_dedup_embedding_threshold,
            )

    return seed_key


async def _handle_multi_match(
    seed_key: str,
    candidates: list[_MergeCandidate],
    write_seed_repo: WriteSeedRepository,
) -> str | None:
    """Handle multiple merge candidates for an incoming seed.

    If candidates share a merged ancestor (one was merged into the other),
    merge into the common survivor. If candidates are genuinely distinct
    entities, mark the incoming seed as ambiguous and create routes.

    Returns the surviving key if resolved, or None to fall through to
    embedding-based dedup.
    """
    # Follow merge chains to find ultimate ancestors
    ancestor_map: dict[str, str] = {}  # candidate_key -> ultimate_ancestor_key
    for c in candidates:
        ancestor = await _follow_merge_chain(c.key, write_seed_repo)
        ancestor_map[c.key] = ancestor

    unique_ancestors = set(ancestor_map.values())

    if len(unique_ancestors) == 1:
        # All candidates share a common ancestor — merge into that ancestor
        ancestor_key = next(iter(unique_ancestors))
        return await _merge_pair(
            seed_key, ancestor_key, write_seed_repo,
            reason=f"multi-match resolved to common ancestor ({len(candidates)} candidates)",
        )

    # Genuinely different entities — mark incoming seed as ambiguous
    logger.info(
        "Ambiguous seed detected: '%s' matches %d distinct entities: %s",
        seed_key,
        len(unique_ancestors),
        [(c.key, c.name) for c in candidates],
    )

    # Mark as ambiguous and create routes to each candidate
    try:
        from sqlalchemy import update as sa_update
        from kt_db.write_models import WriteSeed

        await write_seed_repo._session.execute(
            sa_update(WriteSeed)
            .where(WriteSeed.key == seed_key)
            .values(status="ambiguous")
        )

        for c in candidates:
            ancestor = ancestor_map[c.key]
            await write_seed_repo.create_route(
                parent_key=seed_key,
                child_key=ancestor,
                label=c.name,
            )
    except Exception:
        logger.debug("Failed to mark seed '%s' as ambiguous", seed_key, exc_info=True)

    return seed_key  # Return own key (now marked ambiguous, won't be merged)


async def _follow_merge_chain(
    seed_key: str,
    write_seed_repo: WriteSeedRepository,
    max_depth: int = 10,
) -> str:
    """Follow the merge chain to find the ultimate surviving ancestor.

    Returns the final key (may be the input key if no merges).
    """
    current = seed_key
    for _ in range(max_depth):
        seed = await write_seed_repo.get_seed_by_key(current)
        if seed is None or seed.status != "merged" or not seed.merged_into_key:
            return current
        current = seed.merged_into_key
    return current


async def _merge_pair(
    incoming_key: str,
    existing_key: str,
    write_seed_repo: WriteSeedRepository,
    reason: str,
) -> str:
    """Merge two seeds, keeping the one with more facts as the winner.

    Returns the winning key.
    """
    incoming = await write_seed_repo.get_seed_by_key(incoming_key)
    existing = await write_seed_repo.get_seed_by_key(existing_key)

    if incoming is None or existing is None:
        return incoming_key

    # Winner has more facts; on tie, prefer the existing (more canonical)
    if incoming.fact_count > existing.fact_count:
        winner_key, loser_key = incoming_key, existing_key
    else:
        winner_key, loser_key = existing_key, incoming_key

    # Advisory lock on winner key to prevent concurrent merges into the
    # same target from corrupting state.
    from sqlalchemy import text
    session = write_seed_repo._session

    try:
        async with session.begin_nested():
            await session.execute(
                text("SELECT pg_advisory_xact_lock(hashtext(:key))"),
                {"key": winner_key},
            )
            await write_seed_repo.merge_seeds(loser_key, winner_key, reason=reason)
        logger.info(
            "Merged seed '%s' into '%s' (reason: %s)",
            loser_key, winner_key, reason,
        )
        return winner_key
    except Exception:
        # Deadlock or other DB error — savepoint rolled back,
        # session stays usable. Skip this merge.
        logger.warning(
            "Merge failed (deadlock?) for '%s' into '%s', skipping",
            loser_key, winner_key, exc_info=True,
        )
        return incoming_key


def _is_prefix_disambiguation_candidate(name_a: str, name_b: str) -> bool:
    """Check if two names share a significant common prefix.

    Returns True if both names start identically up to a word boundary
    and then diverge with distinguishing words.

    Examples:
        "light-dependent reactions" vs "light-independent reactions" -> True
        "North Korea" vs "North Macedonia" -> True
        "light-dependent reactions" vs "dark reactions" -> False
    """
    a_lower = name_a.lower().strip()
    b_lower = name_b.lower().strip()

    if a_lower == b_lower:
        return False

    # Find common prefix length
    prefix_len = 0
    for i, (ca, cb) in enumerate(zip(a_lower, b_lower)):
        if ca != cb:
            break
        prefix_len = i + 1
    else:
        # One is a pure prefix of the other — containment, not disambiguation
        if len(a_lower) != len(b_lower):
            return False

    if prefix_len < 4:
        return False

    # Check that the prefix ends at a word/separator boundary
    prefix = a_lower[:prefix_len]
    if prefix[-1] not in (" ", "-", "_") and prefix_len < len(a_lower) and prefix_len < len(b_lower):
        # Back up to last word boundary
        for j in range(prefix_len - 1, 0, -1):
            if a_lower[j] in (" ", "-", "_"):
                prefix_len = j + 1
                break
        else:
            return False

    # Both must have distinguishing content after the prefix
    suffix_a = a_lower[prefix_len:].strip()
    suffix_b = b_lower[prefix_len:].strip()

    if not suffix_a or not suffix_b:
        return False  # One is a pure prefix of the other

    return True


async def _llm_confirm_merge(
    incoming_name: str,
    incoming_key: str,
    candidate_name: str,
    candidate_key: str,
    write_seed_repo: WriteSeedRepository,
    write_fact_repo: WriteFactRepository,
    model_gateway: ModelGateway,
) -> tuple[bool, str | None]:
    """Ask LLM if two embedding-similar seeds are the same concept.

    Returns (should_merge, preferred_name_or_None).
    On failure -> (False, None) — never false-merge.
    """
    settings = get_settings()
    model_id = settings.seed_dedup_llm_model or settings.decomposition_model

    # Fetch up to 3 facts per seed for context
    incoming_facts_text: list[str] = []
    candidate_facts_text: list[str] = []

    try:
        incoming_fact_ids = await write_seed_repo.get_facts_for_seed(incoming_key)
        if incoming_fact_ids:
            facts = await write_fact_repo.get_by_ids(incoming_fact_ids[:3])
            incoming_facts_text = [f.content[:300] for f in facts if f.content]
    except Exception:
        pass

    try:
        candidate_fact_ids = await write_seed_repo.get_facts_for_seed(candidate_key)
        if candidate_fact_ids:
            facts = await write_fact_repo.get_by_ids(candidate_fact_ids[:3])
            candidate_facts_text = [f.content[:300] for f in facts if f.content]
    except Exception:
        pass

    # Build prompt
    facts_a_str = "\n".join(f"  - {f}" for f in incoming_facts_text) if incoming_facts_text else "  (no facts yet)"
    facts_b_str = "\n".join(f"  - {f}" for f in candidate_facts_text) if candidate_facts_text else "  (no facts yet)"

    prompt = (
        'Are these two knowledge-graph seeds the SAME concept/entity?\n'
        'Be STRICT — only confirm for synonyms, abbreviations, or '
        'different-specificity names for the same thing. '
        'Related-but-distinct concepts = NOT same.\n\n'
        f'Seed A: "{incoming_name}"\n'
        f'Facts:\n{facts_a_str}\n\n'
        f'Seed B: "{candidate_name}"\n'
        f'Facts:\n{facts_b_str}\n\n'
        'JSON: {"same_entity": bool, "preferred_name": "more specific name or null"}'
    )

    try:
        result = await model_gateway.generate_json(
            model_id=model_id,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0,
        )
        if isinstance(result, dict):
            same = bool(result.get("same_entity", False))
            preferred = result.get("preferred_name")
            if preferred and not isinstance(preferred, str):
                preferred = None
            logger.info(
                "LLM merge gate: '%s' vs '%s' -> same=%s preferred=%s",
                incoming_name, candidate_name, same, preferred,
            )
            return same, preferred if same else None
    except Exception:
        logger.debug(
            "LLM merge gate failed for '%s' vs '%s' — defaulting to no-merge",
            incoming_name, candidate_name, exc_info=True,
        )

    return False, None


async def _create_embedding_disambiguation(
    incoming_key: str,
    existing_key: str,
    existing_name: str,
    write_seed_repo: WriteSeedRepository,
) -> None:
    """Convert existing seed into disambiguation anchor with typed routes.

    The existing seed becomes ambiguous. Its facts transfer to a new
    disambiguated child. The incoming seed becomes the other child route.
    Both routes are typed as 'embedding' so routing uses text-search-first.
    """
    existing_seed = await write_seed_repo.get_seed_by_key(existing_key)
    if not existing_seed or existing_seed.status == "ambiguous":
        return  # already split or missing

    existing_facts = await write_seed_repo.get_facts_for_seed(existing_key)

    # The child gets the same name — it IS the specific form
    child_key = f"{existing_key}:disambig"
    try:
        await write_seed_repo.split_seed(
            original_key=existing_key,
            new_seeds=[
                {
                    "key": child_key,
                    "name": existing_name,
                    "node_type": existing_seed.node_type,
                    "entity_subtype": existing_seed.entity_subtype,
                    "label": existing_name,
                },
            ],
            fact_assignments={child_key: existing_facts},
            reason=f"embedding disambiguation: '{existing_name}' vs incoming",
        )

        # Update the route created by split_seed to use embedding ambiguity type
        from sqlalchemy import update as sa_update
        from kt_db.write_models import WriteSeedRoute
        await write_seed_repo._session.execute(
            sa_update(WriteSeedRoute)
            .where(
                WriteSeedRoute.parent_seed_key == existing_key,
                WriteSeedRoute.child_seed_key == child_key,
            )
            .values(ambiguity_type="embedding")
        )

        # Add route from anchor to incoming seed
        incoming_seed = await write_seed_repo.get_seed_by_key(incoming_key)
        incoming_label = incoming_seed.name if incoming_seed else incoming_key
        await write_seed_repo.create_route(
            parent_key=existing_key,
            child_key=incoming_key,
            label=incoming_label,
            ambiguity_type="embedding",
        )

        logger.info(
            "Created embedding disambiguation anchor '%s' -> ['%s', '%s']",
            existing_key, child_key, incoming_key,
        )
    except Exception:
        logger.debug(
            "Failed to create embedding disambiguation for '%s'",
            existing_key, exc_info=True,
        )


async def embed_and_upsert_seed(
    seed_key: str,
    name: str,
    node_type: str,
    embedding_service: EmbeddingService,
    qdrant_seed_repo: QdrantSeedRepository,
) -> None:
    """Embed a seed name and upsert to Qdrant (called during extraction).

    Separated from dedup so extraction can batch-embed seeds efficiently.
    """
    try:
        embedding = await embedding_service.embed_text(name)
        await qdrant_seed_repo.upsert(
            seed_key=seed_key,
            embedding=embedding,
            name=name,
            node_type=node_type,
        )
    except Exception:
        logger.warning("Failed to embed/upsert seed '%s' to Qdrant", name, exc_info=True)
