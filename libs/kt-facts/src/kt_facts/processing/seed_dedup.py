"""Seed deduplication — pending-first pipeline.

Pipeline (per seed, after all batch writes complete):
  1. DB text search — exact key + GIN alias array overlap
  2. Qdrant embedding search — single threshold (0.90), mandatory LLM above it
  3. LLM multiplex — merge_into_seed | merge_into_path | new_disambig_path
  4. Genesis path (no candidates) — promote pending→active + suggest_disambig

Dropped vs old design:
  - Signal cascade (trigram → alias match → phonetic → auto-merge)
  - Phonetic + trigram merge signals
  - Auto-merge at 0.95 (skipped LLM)
  - _llm_confirm_merge (per-pair binary gate)
  - _handle_multi_match (replaced by multiplex)
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from kt_config.settings import get_settings

if TYPE_CHECKING:
    from kt_db.repositories.write_seeds import WriteSeedRepository
    from kt_models.embeddings import EmbeddingService
    from kt_models.gateway import ModelGateway
    from kt_qdrant.repositories.seeds import QdrantSeedRepository

logger = logging.getLogger(__name__)


# ── Multiplex prompt (ported from experiments/big_seed_dedup/multiplex.py) ──

_MULTIPLEX_SYSTEM = """\
You are maintaining a knowledge-graph deduplication registry. Every
"seed" represents ONE real-world entity or concept.

An incoming surface form has surfaced one or more candidate seeds
via exact alias lookup and/or embedding similarity. You must decide
what happens to the incoming seed.

KEY PRINCIPLE
- Alias or embedding similarity is an AMBIGUITY SIGNAL, not a merge signal.
- Merge ONLY when two names refer to the IDENTICAL real-world thing
  (synonyms, acronym expansion, singular/plural, spelling variant).
- When two names share surface form but refer to DIFFERENT real-world
  things, split into disambiguated paths. Every new path MUST have an
  unambiguous label (e.g. "Nate Silver (statistician)").

HARD RULES — never merge the following (split or birth new seed):
- Practitioner vs practice/discipline ("homeopath" ≠ "homeopathy")
- Tool vs user ("hammer" ≠ "carpenter")
- Instance vs category ("Apollo 11" ≠ "space mission")
- Part vs whole ("wheel" ≠ "car")
- Organization vs member ("NASA" ≠ "astronaut")
- Parent concept vs specialization

ACTIONS (pick exactly one):

1. "merge_into_seed"
   Use when the target seed is FLAT (not yet disambiguated) AND incoming
   is literally the same concept.
   Response: {"action": "merge_into_seed", "target_seed_key": "...", "reason": "..."}

2. "merge_into_path"
   Use when the target seed is ALREADY DISAMBIGUATED (has existing paths)
   AND incoming is literally the same concept as one of those paths.
   Provide the key of the specific child path to merge into.
   Response: {"action": "merge_into_path",
              "target_seed_key": "...",
              "target_path_key": "...",
              "reason": "..."}

3. "new_disambig_path"
   Use when the incoming reveals a distinct concept sharing a surface
   form with the candidate seed. Provide unambiguous labels for both.
   If the target seed is still flat, also provide existing_disambig_label
   so the system can promote it into a sibling path.
   Response: {"action": "new_disambig_path",
              "target_seed_key": "...",
              "incoming_disambig_label": "Name (role-for-incoming)",
              "existing_disambig_label": "Name (role-for-existing)",
              "reason": "..."}

Output JSON only. Follow the HARD RULES.
"""

# ── Suggest-disambig prompt (ported from experiments/big_seed_dedup/alias_gen.py) ──

_SUGGEST_DISAMBIG_SYSTEM = """\
NATURAL AMBIGUITY RULE

Given a bare name, list canonical disambiguation labels if the name
is a naturally ambiguous term that commonly refers to multiple
distinct real-world entities or concepts. Use only world knowledge
about the name itself — no context.

Examples:
  Mercury     → ["Mercury (planet)", "Mercury (element)", "Mercury (Roman god)"]
  Apollo      → ["Apollo (Greek god)", "Apollo (NASA program)", "Apollo (theatre)"]
  Java        → ["Java (programming language)", "Java (island)"]
  Jaguar      → ["Jaguar (animal)", "Jaguar (car brand)"]
  Newton      → ["Newton (physicist)", "Newton (unit of force)"]
  Python      → ["Python (programming language)", "Python (snake)", "Python (mythology)"]
  Paris       → ["Paris (France)", "Paris (Greek myth)"]
  Amazon      → ["Amazon (company)", "Amazon (river)", "Amazon (rainforest)"]
  Cambridge   → ["Cambridge (city, UK)", "Cambridge (city, MA)", "Cambridge University"]
  Washington  → ["Washington (state)", "Washington D.C.", "George Washington"]
  Saturn      → ["Saturn (planet)", "Saturn (Roman god)", "Saturn (car brand)"]

Multi-token names are typically NOT naturally ambiguous — emit
empty list for them.

Default: empty list. When unsure, empty list. Missing ambiguity is
cheap (downstream multiplex catches late cases); wrongly pre-
disambiguating a single-meaning name is expensive.

Output JSON exactly: {"paths": ["Label1", ...]}
"""

# ── Route-facts prompt (ported from experiments/big_seed_dedup/multiplex.py) ──

_ROUTE_FACTS_SYSTEM = """\
You route facts to the correct disambiguation path of a name.

Given a canonical name N, a list of disambiguation paths (each
labelled e.g. "N (role)"), and facts mentioning N, assign each
fact to the path it is actually about. Use only the fact content.

Return JSON exactly:
{"assignments": [{"fact_id": "...", "path_label": "..."}, ...]}

If a fact does not fit any path, set "path_label" to null — that
fact will remain at the parent level.
"""


async def deduplicate_seed(
    seed_key: str,
    name: str,
    node_type: str,
    write_seed_repo: WriteSeedRepository,
    embedding_service: EmbeddingService,
    qdrant_seed_repo: QdrantSeedRepository,
    model_gateway: ModelGateway | None = None,
    write_fact_repo: object | None = None,  # kept for call-site compat, unused
    aliases: list[str] | None = None,
) -> str:
    """Deduplicate a seed using text search → embedding → LLM multiplex.

    Returns the surviving seed key (may differ if merged).
    Promotes pending seeds to active and runs genesis disambiguation
    when no candidates are found.

    aliases: slugified keys from entity extractor (make_seed_key(alias)).
    """
    from kt_facts.processing.entity_extraction import _is_valid_entity_name

    if not _is_valid_entity_name(name):
        logger.debug("Skipping dedup for invalid seed name: '%s'", name)
        return seed_key

    alias_keys: list[str] = list(aliases or [])

    # ── Step 1: DB text search (exact key + GIN alias overlap) ──────────
    text_candidates = []
    try:
        text_candidates = await write_seed_repo.find_seeds_by_keys_or_aliases(
            keys=[seed_key] + alias_keys,
            exclude_key=seed_key,
            node_type=node_type,
        )
    except Exception:
        logger.debug("Text candidate search failed for '%s'", name, exc_info=True)

    # ── Step 2: Qdrant embedding search ─────────────────────────────────
    settings = get_settings()
    embed_candidates = []
    embedding = None
    try:
        embedding = await embedding_service.embed_text(name)
        raw_hits = await qdrant_seed_repo.find_similar(
            embedding=embedding,
            node_type=node_type,
            limit=8,
            score_threshold=settings.seed_dedup_embedding_threshold,
            exclude_keys={seed_key},
        )
        # Verify each hit is still an active/ambiguous/promoted seed
        for hit in raw_hits:
            seed = await write_seed_repo.get_seed_by_key(hit.seed_key)
            if seed and seed.status not in ("merged", "pending", "garbage"):
                embed_candidates.append(seed)
    except Exception:
        logger.debug("Embedding candidate search failed for '%s'", name, exc_info=True)

    # Merge + deduplicate candidates by key (text first, then embedding)
    seen_keys: set[str] = set()
    all_candidates = []
    for c in text_candidates + embed_candidates:
        if c.key not in seen_keys:
            seen_keys.add(c.key)
            all_candidates.append(c)

    if not all_candidates:
        # Genesis path: no candidates → promote pending + suggest disambig
        await _promote_and_genesis_disambig(
            seed_key=seed_key,
            name=name,
            node_type=node_type,
            write_seed_repo=write_seed_repo,
            model_gateway=model_gateway,
        )
        return seed_key

    # ── Step 3: LLM multiplex ────────────────────────────────────────────
    if model_gateway is None:
        # No LLM available — merge into highest-fact candidate (safe default)
        best = max(all_candidates, key=lambda s: s.fact_count)
        logger.info("No LLM — default merge: '%s' → '%s'", name, best.name)
        return await _merge_pair(seed_key, best.key, write_seed_repo, reason="no_llm_default_merge")

    response = await _llm_multiplex(name, all_candidates, model_gateway)
    action = response.get("action", "")
    target_key = str(response.get("target_seed_key", "")).strip()
    reason = str(response.get("reason", action))

    # Validate target key points to a real candidate
    target = next((c for c in all_candidates if c.key == target_key), None)
    if target is None and all_candidates:
        # LLM returned bad ID — use best candidate
        target = max(all_candidates, key=lambda s: s.fact_count)
        target_key = target.key
        reason += " | fallback: LLM target invalid"

    if action == "merge_into_seed" and target is not None:
        winner = await _merge_pair(seed_key, target_key, write_seed_repo, reason=f"llm_multiplex:{reason}")
        return winner

    elif action == "merge_into_path" and target is not None:
        # Target is already disambiguated — merge incoming into a specific child path
        path_key = str(response.get("target_path_key", "")).strip()
        # Validate path_key is a real child of target
        routes = await write_seed_repo.get_routes_for_parent(target_key)
        valid_child_keys = {r.child_seed_key for r in routes}
        if path_key not in valid_child_keys:
            if valid_child_keys:
                # LLM gave bad path key — use first child as fallback
                path_key = next(iter(valid_child_keys))
                reason += " | fallback: path_key invalid, used first child"
            else:
                # Target has no routes — treat as flat merge
                logger.info(
                    "merge_into_path: target '%s' has no routes, falling back to merge_into_seed",
                    target_key,
                )
                winner = await _merge_pair(seed_key, target_key, write_seed_repo, reason=f"llm_multiplex:merge_into_path_flat_fallback:{reason}")
                return winner
        logger.info("Merging '%s' into path '%s' of '%s' (reason: %s)", name, path_key, target_key, reason)
        winner = await _merge_pair(seed_key, path_key, write_seed_repo, reason=f"llm_multiplex:merge_into_path:{reason}")
        return winner

    elif action == "new_disambig_path" and target is not None:
        incoming_label = str(response.get("incoming_disambig_label", f"{name} (variant)")).strip()
        existing_label = str(response.get("existing_disambig_label", target.name)).strip()
        await _apply_disambig_path(
            incoming_key=seed_key,
            target_key=target_key,
            incoming_label=incoming_label,
            existing_label=existing_label,
            write_seed_repo=write_seed_repo,
        )
        # Promote incoming from pending → active (it stays as its own seed)
        await _promote_pending(seed_key, write_seed_repo)
        return seed_key

    else:
        # Unknown action or no target — safe fallback: no merge
        logger.info(
            "Multiplex unknown action '%s' for '%s' — no merge (fallback)", action, name
        )
        await _promote_pending(seed_key, write_seed_repo)
        return seed_key


async def _promote_pending(seed_key: str, write_seed_repo: WriteSeedRepository) -> None:
    """Promote a seed from pending → active if currently pending."""
    seed = await write_seed_repo.get_seed_by_key(seed_key)
    if seed and seed.status == "pending":
        await write_seed_repo.set_status(seed_key, "active")


async def _promote_and_genesis_disambig(
    seed_key: str,
    name: str,
    node_type: str,
    write_seed_repo: WriteSeedRepository,
    model_gateway: ModelGateway | None,
) -> None:
    """Promote a pending seed to active and run genesis disambiguation.

    Only runs for seeds with status=pending. Skips silently for active seeds
    (they've already been through genesis on a previous dedup pass).
    """
    settings = get_settings()
    seed = await write_seed_repo.get_seed_by_key(seed_key)
    if seed is None or seed.status != "pending":
        return

    # 1. Promote: pending → active
    await write_seed_repo.set_status(seed_key, "active")
    logger.debug("Promoted seed '%s' ('%s') from pending to active", seed_key, name)

    # 2. Suggest disambiguation (world-knowledge, fact-free)
    if not settings.seed_suggest_disambig_enabled or model_gateway is None:
        return

    paths = await _suggest_disambig(name, model_gateway)
    if not paths or len(paths) < 2:
        return

    logger.info("Genesis disambig: '%s' → %d paths %s", name, len(paths), paths)

    # 3. Route existing facts to paths (if any)
    try:
        facts = await write_seed_repo.get_facts_with_content_for_seed(seed_key)
    except Exception:
        facts = []

    fact_assignments: dict[str, str | None] = {}
    if facts:
        fact_assignments = await _route_facts_to_paths(name, paths, facts, model_gateway)

    # 4. Create WriteSeedRoute children per path label
    await _create_seed_routes_with_facts(
        parent_key=seed_key,
        path_labels=paths,
        fact_assignments=fact_assignments,
        all_facts=facts,
        write_seed_repo=write_seed_repo,
    )
    await write_seed_repo.set_status(seed_key, "ambiguous")


async def _suggest_disambig(name: str, model_gateway: ModelGateway) -> list[str]:
    """Ask LLM if name is naturally polysemous. Returns path labels or []."""
    settings = get_settings()
    model_id = settings.seed_dedup_llm_model or settings.decomposition_model
    user = f'Name: "{name}"\n\nReturn JSON: {{"paths": [...]}}. Only the JSON.'
    try:
        result = await model_gateway.generate_json(
            model_id=model_id,
            messages=[{"role": "user", "content": user}],
            system=_SUGGEST_DISAMBIG_SYSTEM,
            temperature=0.0,
        )
        if isinstance(result, dict):
            raw = result.get("paths", [])
            return [str(p).strip() for p in raw if isinstance(p, str) and str(p).strip()]
    except Exception:
        logger.debug("suggest_disambig failed for '%s'", name, exc_info=True)
    return []


async def _route_facts_to_paths(
    name: str,
    path_labels: list[str],
    facts: list[tuple],  # (fact_id, content)
    model_gateway: ModelGateway,
) -> dict[str, str | None]:
    """Ask LLM to assign each fact to a path label. Returns fact_id → path_label | None."""
    settings = get_settings()
    model_id = settings.seed_dedup_llm_model or settings.decomposition_model
    label_lines = "\n".join(f"  [{i + 1}] {p}" for i, p in enumerate(path_labels))
    fact_lines = "\n".join(
        f"  F{i + 1} (id={fid}): {content[:400]}" for i, (fid, content) in enumerate(facts)
    )
    user = (
        f'Canonical name: "{name}"\n\nPaths:\n{label_lines}\n\nFacts:\n{fact_lines}\n\n'
        'Return JSON: {"assignments": [{"fact_id": "...", "path_label": "..."}, ...]}.'
    )
    try:
        result = await model_gateway.generate_json(
            model_id=model_id,
            messages=[{"role": "user", "content": user}],
            system=_ROUTE_FACTS_SYSTEM,
            temperature=0.0,
        )
        if isinstance(result, dict):
            assignments: dict[str, str | None] = {}
            valid = {p.strip().lower(): p for p in path_labels}
            for a in result.get("assignments", []):
                if not isinstance(a, dict):
                    continue
                fid = str(a.get("fact_id", "")).strip()
                lab = a.get("path_label")
                if not fid:
                    continue
                if isinstance(lab, str) and lab.strip().lower() in valid:
                    assignments[fid] = valid[lab.strip().lower()]
                else:
                    assignments[fid] = None
            return assignments
    except Exception:
        logger.debug("route_facts_to_paths failed for '%s'", name, exc_info=True)
    return {}


async def _create_seed_routes_with_facts(
    parent_key: str,
    path_labels: list[str],
    fact_assignments: dict[str, str | None],
    all_facts: list[tuple],  # (fact_id, content)
    write_seed_repo: WriteSeedRepository,
) -> None:
    """Create WriteSeedRoute children for each path label.

    Moves assigned facts to the appropriate child seed via split_seed.
    """
    from kt_db.keys import make_seed_key

    parent_seed = await write_seed_repo.get_seed_by_key(parent_key)
    if parent_seed is None:
        return

    import uuid as _uuid

    # Build fact assignment map: path_label → [fact_id]
    path_fact_ids: dict[str, list[_uuid.UUID]] = {label: [] for label in path_labels}
    for fact_id, _ in all_facts:
        assigned_label = fact_assignments.get(str(fact_id))
        if assigned_label and assigned_label in path_fact_ids:
            path_fact_ids[assigned_label].append(fact_id if isinstance(fact_id, _uuid.UUID) else _uuid.UUID(str(fact_id)))

    # Create child seeds via split_seed
    new_seeds = []
    fact_assignments_by_key: dict[str, list[_uuid.UUID]] = {}
    for label in path_labels:
        child_key = make_seed_key(label)
        new_seeds.append({
            "key": child_key,
            "name": label,
            "node_type": parent_seed.node_type,
            "entity_subtype": parent_seed.entity_subtype,
            "label": label,
        })
        fact_assignments_by_key[child_key] = path_fact_ids.get(label, [])

    try:
        await write_seed_repo.split_seed(
            original_key=parent_key,
            new_seeds=new_seeds,
            fact_assignments=fact_assignments_by_key,
            reason="genesis_disambig",
        )
    except Exception:
        logger.warning("split_seed failed for genesis disambig on '%s'", parent_key, exc_info=True)


async def _llm_multiplex(
    incoming_name: str,
    candidates: list,  # list[WriteSeed]
    model_gateway: ModelGateway,
) -> dict:
    """Call LLM multiplex to decide what to do with an incoming seed + candidates."""
    settings = get_settings()
    model_id = settings.seed_dedup_llm_model or settings.decomposition_model

    lines: list[str] = [f'INCOMING: "{incoming_name}"', ""]
    lines.append(f"CANDIDATES ({len(candidates)}):")
    for c in candidates[:5]:  # cap at 5 to keep prompt size manageable
        lines.append(
            f'- seed_key="{c.key}"  name="{c.name}"  '
            f"node_type={c.node_type}  fact_count={c.fact_count}"
        )
    lines.append("")
    lines.append("Return JSON only. Follow the HARD RULES.")
    user = "\n".join(lines)

    try:
        result = await model_gateway.generate_json(
            model_id=model_id,
            messages=[{"role": "user", "content": user}],
            system=_MULTIPLEX_SYSTEM,
            temperature=0.0,
        )
        if isinstance(result, dict):
            return result
    except Exception:
        logger.debug("LLM multiplex failed for '%s'", incoming_name, exc_info=True)
    return {}


async def _apply_disambig_path(
    incoming_key: str,
    target_key: str,
    incoming_label: str,
    existing_label: str,
    write_seed_repo: WriteSeedRepository,
) -> None:
    """Create disambiguation routes between incoming and target seeds.

    If target is still flat (no routes), auto-promotes its content into a
    sibling path labelled existing_label and creates the incoming path.
    If target already has routes, just create an incoming path route.
    """
    from kt_db.keys import make_seed_key

    target_seed = await write_seed_repo.get_seed_by_key(target_key)
    if target_seed is None:
        return

    existing_routes = await write_seed_repo.get_routes_for_parent(target_key)

    try:
        if not existing_routes:
            # Target is flat — promote existing content into sibling path
            existing_facts = await write_seed_repo.get_facts_for_seed(target_key)
            existing_child_key = make_seed_key(existing_label or target_seed.name)
            await write_seed_repo.split_seed(
                original_key=target_key,
                new_seeds=[{
                    "key": existing_child_key,
                    "name": existing_label or target_seed.name,
                    "node_type": target_seed.node_type,
                    "entity_subtype": target_seed.entity_subtype,
                    "label": existing_label or target_seed.name,
                }],
                fact_assignments={existing_child_key: existing_facts},
                reason="multiplex_new_disambig_path:promote_existing",
            )

        # Add incoming as a new child route of the target
        incoming_child_key = make_seed_key(incoming_label)
        incoming_seed = await write_seed_repo.get_seed_by_key(incoming_key)
        incoming_facts = await write_seed_repo.get_facts_for_seed(incoming_key)

        # Create the child seed for incoming and move its facts there
        await write_seed_repo.split_seed(
            original_key=target_key,
            new_seeds=[{
                "key": incoming_child_key,
                "name": incoming_label,
                "node_type": (incoming_seed.node_type if incoming_seed else target_seed.node_type),
                "entity_subtype": (incoming_seed.entity_subtype if incoming_seed else None),
                "label": incoming_label,
            }],
            fact_assignments={incoming_child_key: incoming_facts},
            reason="multiplex_new_disambig_path:incoming",
        )

        # Mark target as ambiguous
        await write_seed_repo.set_status(target_key, "ambiguous")

        # Merge incoming seed into its labelled child (incoming_key → incoming_child_key)
        if incoming_seed and incoming_child_key != incoming_key:
            await write_seed_repo.merge_seeds(
                incoming_key, incoming_child_key, reason="multiplex_fold_incoming_into_path"
            )

    except Exception:
        logger.warning("_apply_disambig_path failed for '%s'→'%s'", incoming_key, target_key, exc_info=True)


async def _follow_merge_chain(
    seed_key: str,
    write_seed_repo: WriteSeedRepository,
    max_depth: int = 10,
) -> str:
    """Follow the merge chain to find the ultimate surviving ancestor."""
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
    """Merge two seeds. Winner = more facts. Loser aliases propagate to winner.

    Returns the winning key.
    """
    incoming = await write_seed_repo.get_seed_by_key(incoming_key)
    existing = await write_seed_repo.get_seed_by_key(existing_key)

    if incoming is None or existing is None:
        return incoming_key

    # Winner: more facts; on tie, prefer existing (already established)
    if incoming.fact_count > existing.fact_count:
        winner_key, loser_key = incoming_key, existing_key
        winner, loser = incoming, existing
    else:
        winner_key, loser_key = existing_key, incoming_key
        winner, loser = existing, incoming

    # Longest-wins canonical: if loser name is longer, rename winner
    winner_name = winner.name
    if len(loser.name) > len(winner.name):
        winner_name = loser.name

    from sqlalchemy import text

    session = write_seed_repo._session
    try:
        async with session.begin_nested():
            await session.execute(
                text("SELECT pg_advisory_xact_lock(hashtext(:key))"),
                {"key": winner_key},
            )
            await write_seed_repo.merge_seeds(loser_key, winner_key, reason=reason)

            # Propagate loser's aliases to winner
            loser_aliases = [loser_key] + list(loser.aliases or [])
            if loser_aliases:
                await write_seed_repo.merge_aliases_into_winner(winner_key, loser_aliases)

            # Apply longest-wins rename if needed
            if winner_name != winner.name:
                await write_seed_repo.rename_seed(winner_key, winner_name)

        logger.info("Merged seed '%s' → '%s' (reason: %s)", loser_key, winner_key, reason)
        return winner_key
    except Exception:
        logger.warning(
            "Merge failed for '%s'→'%s', skipping", loser_key, winner_key, exc_info=True
        )
        return incoming_key


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
