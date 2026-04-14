"""Global intake pipeline — v2.

Per incoming seed:
  1. generate aliases
  2. embed
  3. reverse alias lookup (global)
  4. qdrant search across all indexed embeddings
  5. merge candidates, dedup by (big_seed_id, path_id)
  6. LLM multiplex (if any candidates) OR genesis (if none)
  7. apply + index
"""

from __future__ import annotations

import json

from .alias_gen import generate_aliases
from .big_seed import (
    BigSeed,
    Candidate,
    Decision,
    Fact,
    NamedVec,
    Path,
    Registry,
    Usage,
)
from .llm import LLMRunner
from .qdrant_index import QdrantIndex

EMBED_THRESHOLD = 0.90
SAMPLE_FACTS_FOR_LLM = 3
SAMPLE_FACTS_STORED = 10
MAX_QDRANT_CANDIDATES = 8


_MULTIPLEX_SYSTEM = """\
You are maintaining a knowledge-graph deduplication registry. Every
"big-seed" represents ONE real-world entity or concept. A big-seed may be
flat (single concept) or disambiguated (a surface form shared by several
distinct real-world entities, split into named paths like "John (actor)"
vs "John (scientist)").

An incoming surface form has surfaced one or more candidate big-seeds/paths
via exact alias lookup and/or embedding similarity. You must decide what
happens to the incoming seed.

KEY PRINCIPLE
- Alias or embedding similarity is an AMBIGUITY SIGNAL, not a merge signal.
- Merge ONLY when two names refer to the IDENTICAL real-world thing
  (synonyms, acronym expansion, singular/plural, spelling variant).
- When two names share surface form but refer to DIFFERENT real-world
  things, split into disambiguated paths. Every new path MUST have an
  unambiguous label (e.g. "Nate Silver (statistician)").

HARD RULES — never merge the following (split into paths or birth new bigseed):
- Practitioner vs practice/discipline ("homeopath" ≠ "homeopathy")
- Tool vs user ("hammer" ≠ "carpenter")
- Instance vs category ("Apollo 11" ≠ "space mission")
- Part vs whole ("wheel" ≠ "car")
- Organization vs member ("NASA" ≠ "astronaut")
- Parent concept vs specialization

ACTIONS (pick exactly one):

1. "merge_into_big_seed"
   Use when candidate big-seed is flat AND incoming is literally the same
   concept. Incoming aliases/embeddings/facts append to the flat big-seed.
   Response: {"action": "merge_into_big_seed", "target_big_seed_id": "..."}

2. "merge_into_path"
   Use when candidate big-seed is already disambiguated AND incoming is
   literally the same entity as one of its existing paths.
   Response: {"action": "merge_into_path",
              "target_big_seed_id": "...", "target_path_id": "..."}

3. "split_big_seed"
   Use when candidate big-seed was flat BUT incoming reveals it was
   actually ambiguous. Produce TWO or more disambiguated paths: partition
   the existing big-seed's aliases into those paths, and add incoming as
   a new path. Each path needs an unambiguous label.
   Response: {"action": "split_big_seed",
              "target_big_seed_id": "...",
              "paths": [
                {"label": "Name (role)",
                 "from_parent_aliases": [<subset of parent aliases>]},
                ...
              ],
              "incoming_goes_to_label": "Name (role)"  (which of the new paths)
             }

4. "new_disambig_path"
   Use when candidate big-seed is already disambiguated AND incoming is a
   NEW distinct entity that shares the surface form.
   Response: {"action": "new_disambig_path",
              "target_big_seed_id": "...",
              "disambig_label": "Name (role)"}

Pick only ONE candidate big-seed when multiple surfaced. Prefer the one
with the closest semantic match. If genuinely none match the incoming,
still return one of the above; the orchestration layer will take it as
signal. Output JSON only.
"""


def _fact_samples(facts: list[Fact]) -> list[str]:
    return [f.content[:240] for f in facts[:SAMPLE_FACTS_FOR_LLM] if f.content.strip()]


def _build_multiplex_user(
    incoming_name: str,
    incoming_aliases: list[str],
    facts: list[Fact],
    candidates: list[tuple[BigSeed, Candidate]],
) -> str:
    lines: list[str] = []
    lines.append(f'INCOMING: "{incoming_name}"')
    lines.append(f"Generated aliases: {incoming_aliases or '[]'}")
    lines.append("Incoming sample facts:")
    for f in facts[:SAMPLE_FACTS_FOR_LLM]:
        lines.append(f"  • {f.content[:240]}")
    lines.append("")
    lines.append(f"CANDIDATE big-seeds / paths ({len(candidates)}):")
    seen: set[str] = set()
    for big, cand in candidates:
        if big.id in seen:
            continue
        seen.add(big.id)
        lines.append(
            f'- big_seed_id={big.id}  canonical="{big.canonical_name}"  '
            f"node_type={big.node_type}  ambiguous={big.ambiguous}"
        )
        lines.append(f"    aliases: {big.aliases or '[]'}")
        if big.paths:
            for p in big.paths:
                lines.append(f'    path_id={p.id}  label="{p.label}"  aliases={p.aliases}')
                for f in p.facts[:SAMPLE_FACTS_FOR_LLM]:
                    lines.append(f"      • {f.content[:200]}")
        else:
            for f in big.facts[:SAMPLE_FACTS_FOR_LLM]:
                lines.append(f"    • {f.content[:200]}")
        lines.append(f"    matched via: {cand.via} (score={cand.score:.3f})")
    lines.append("")
    lines.append("Return JSON only. Follow the HARD RULES.")
    return "\n".join(lines)


async def _embed_with_aliases(
    runner: LLMRunner, name: str, aliases: list[str]
) -> list[NamedVec]:
    """Embed the canonical + each alias. Cached — cheap on re-runs."""
    out = [NamedVec(source_name=name, vec=await runner.embed(name))]
    for a in aliases:
        if a.strip() and a.strip().lower() != name.strip().lower():
            out.append(NamedVec(source_name=a, vec=await runner.embed(a)))
    return out


async def _index_many(
    qi: QdrantIndex,
    *,
    big_seed_id: str,
    path_id: str | None,
    canonical_name: str,
    path_label: str | None,
    vecs: list[NamedVec],
) -> None:
    for nv in vecs:
        await qi.upsert(
            big_seed_id=big_seed_id,
            path_id=path_id,
            canonical_name=canonical_name,
            path_label=path_label,
            source_name=nv.source_name,
            vec=nv.vec,
        )


async def intake(
    name: str,
    facts: list[Fact],
    node_type: str,
    *,
    registry: Registry,
    runner: LLMRunner,
    qdrant: QdrantIndex,
    step: int,
    precomputed_aliases: tuple[list[str], "Usage", dict] | None = None,
) -> Decision:
    d = Decision(
        step=step,
        incoming_name=name,
        incoming_fact_count=len(facts),
        incoming_fact_samples=_fact_samples(facts),
    )

    # ── 1. alias_gen ────────────────────────────────────────────────
    if precomputed_aliases is not None:
        aliases, alias_usage, alias_resp = precomputed_aliases
    else:
        aliases, alias_usage, alias_resp = await generate_aliases(name, facts, runner=runner)
    d.incoming_aliases = aliases
    d.alias_gen_usage = alias_usage
    d.alias_gen_response = alias_resp

    # ── 2. embed ────────────────────────────────────────────────────
    vecs = await _embed_with_aliases(runner, name, aliases)

    # ── 3. reverse alias ────────────────────────────────────────────
    hit_candidates: dict[tuple[str, str | None], Candidate] = {}
    for matched, bs_id, path_id in registry.lookup_aliases([name, *aliases]):
        big = registry.find_big_seed(bs_id)
        if big is None:
            continue
        p = big.find_path(path_id) if path_id else None
        hit_candidates[(bs_id, path_id)] = Candidate(
            big_seed_id=bs_id,
            path_id=path_id,
            canonical_name=big.canonical_name,
            path_label=p.label if p else None,
            score=1.0,
            via="alias",
            matched_alias=matched,
        )
    d.reverse_alias_hits = list(hit_candidates.values())

    # ── 4. qdrant search (use canonical vec as query) ───────────────
    all_hits = await qdrant.search(vecs[0].vec, threshold=EMBED_THRESHOLD, limit=MAX_QDRANT_CANDIDATES)
    # diagnostic: lower-threshold scan so we can show "near misses" in report
    diag = await qdrant.search(vecs[0].vec, threshold=0.70, limit=20)
    d.all_embed_scores = [(h.path_label or h.canonical_name, h.score) for h in diag]

    for h in all_hits:
        key = (h.big_seed_id, h.path_id)
        if key in hit_candidates:
            existing = hit_candidates[key]
            existing.via = "both" if existing.via == "alias" else existing.via
            existing.score = max(existing.score, h.score)
            existing.matched_source_name = h.source_name
            continue
        big = registry.find_big_seed(h.big_seed_id)
        if big is None:
            continue
        hit_candidates[key] = Candidate(
            big_seed_id=h.big_seed_id,
            path_id=h.path_id,
            canonical_name=h.canonical_name,
            path_label=h.path_label,
            score=h.score,
            via="embedding",
            matched_source_name=h.source_name,
        )
    d.embed_candidates = [c for c in hit_candidates.values() if c.via != "alias"]

    # ── 5. decide ───────────────────────────────────────────────────
    candidates = list(hit_candidates.values())
    if not candidates:
        _apply_genesis(registry, d, name, node_type, aliases, facts, vecs)
        await _index_big_seed(qdrant, registry.find_big_seed(d.target_big_seed_id))  # type: ignore[arg-type]
        _register_aliases_for_big_seed(registry, d.target_big_seed_id)  # type: ignore[arg-type]
        return d

    # hand candidates to LLM
    cand_pairs: list[tuple[BigSeed, Candidate]] = []
    for c in candidates:
        big = registry.find_big_seed(c.big_seed_id)
        if big:
            cand_pairs.append((big, c))

    user = _build_multiplex_user(name, aliases, facts, cand_pairs)
    response, usage = await runner.call_json(
        kind="multiplex",
        system_prompt=_MULTIPLEX_SYSTEM,
        user_content=user,
        max_tokens=600,
    )
    d.multiplex_usage = usage
    d.multiplex_response = response if isinstance(response, dict) else {}

    await _apply_llm_decision(
        registry=registry,
        qdrant=qdrant,
        decision=d,
        incoming_name=name,
        node_type=node_type,
        aliases=aliases,
        facts=facts,
        vecs=vecs,
        response=d.multiplex_response or {},
    )
    return d


# ── application helpers ────────────────────────────────────────────

def _apply_genesis(
    registry: Registry,
    d: Decision,
    name: str,
    node_type: str,
    aliases: list[str],
    facts: list[Fact],
    vecs: list[NamedVec],
) -> None:
    big = BigSeed.new(canonical=name, node_type=node_type)
    big.aliases = list(dict.fromkeys([name, *aliases]))
    big.embeddings = vecs
    big.facts.extend(facts[:SAMPLE_FACTS_STORED])
    big.alias_gen_usage = d.alias_gen_usage
    big.alias_gen_response = d.alias_gen_response
    registry.big_seeds.append(big)
    d.kind = "genesis"
    d.target_big_seed_id = big.id
    d.target_big_seed_canonical = big.canonical_name
    d.reason = "no candidates above threshold — new flat big-seed"


def _register_aliases_for_big_seed(registry: Registry, bs_id: str | None) -> None:
    if not bs_id:
        return
    big = registry.find_big_seed(bs_id)
    if not big:
        return
    if big.paths:
        for p in big.paths:
            registry.register_alias(p.label, big.id, p.id)
            for a in p.aliases:
                registry.register_alias(a, big.id, p.id)
    else:
        registry.register_alias(big.canonical_name, big.id, None)
        for a in big.aliases:
            registry.register_alias(a, big.id, None)


async def _index_big_seed(qdrant: QdrantIndex, big: BigSeed | None) -> None:
    if not big:
        return
    if big.paths:
        for p in big.paths:
            await _index_many(
                qdrant,
                big_seed_id=big.id,
                path_id=p.id,
                canonical_name=big.canonical_name,
                path_label=p.label,
                vecs=p.embeddings,
            )
    else:
        await _index_many(
            qdrant,
            big_seed_id=big.id,
            path_id=None,
            canonical_name=big.canonical_name,
            path_label=None,
            vecs=big.embeddings,
        )


async def _apply_llm_decision(
    *,
    registry: Registry,
    qdrant: QdrantIndex,
    decision: Decision,
    incoming_name: str,
    node_type: str,
    aliases: list[str],
    facts: list[Fact],
    vecs: list[NamedVec],
    response: dict,
) -> None:
    action = str(response.get("action", "")).strip()
    target_id = str(response.get("target_big_seed_id", "")).strip()
    big = registry.find_big_seed(target_id) if target_id else None

    # fallback: if LLM gave bad bigseed id, pick best candidate ourselves
    if big is None and (decision.embed_candidates or decision.reverse_alias_hits):
        pool = decision.reverse_alias_hits + decision.embed_candidates
        pool.sort(key=lambda c: -c.score)
        big = registry.find_big_seed(pool[0].big_seed_id)

    if big is None:
        # LLM gave us nothing actionable — fall back to genesis
        _apply_genesis(registry, decision, incoming_name, node_type, aliases, facts, vecs)
        await _index_big_seed(qdrant, registry.find_big_seed(decision.target_big_seed_id))  # type: ignore[arg-type]
        _register_aliases_for_big_seed(registry, decision.target_big_seed_id)
        decision.reason = "LLM target invalid — fell back to genesis"
        return

    decision.target_big_seed_id = big.id
    decision.target_big_seed_canonical = big.canonical_name
    decision.reason = str(response.get("reason", "")) or action

    if action == "merge_into_big_seed":
        _merge_flat(big, incoming_name, aliases, facts, vecs)
        decision.kind = "merge_into_big_seed"

    elif action == "merge_into_path":
        target_path_id = str(response.get("target_path_id", "")).strip()
        p = big.find_path(target_path_id)
        if p is None and big.paths:
            p = big.paths[0]
        if p is None:
            # bigseed not actually split — fall back to flat merge
            _merge_flat(big, incoming_name, aliases, facts, vecs)
            decision.kind = "merge_into_big_seed"
            decision.reason += " | fallback: target_path missing, merged flat"
        else:
            _merge_path(p, incoming_name, aliases, facts, vecs)
            decision.kind = "merge_into_path"
            decision.target_path_id = p.id
            decision.target_path_label = p.label

    elif action == "split_big_seed":
        new_paths = _split_big_seed(
            big, incoming_name, aliases, facts, vecs, response, runner_vecs=vecs
        )
        decision.kind = "split_big_seed"
        decision.split_paths = [
            {"id": p.id, "label": p.label, "aliases": p.aliases} for p in new_paths
        ]
        # which path the incoming ended up on
        goes = str(response.get("incoming_goes_to_label", "")).strip().lower()
        for p in new_paths:
            if p.label.strip().lower() == goes:
                decision.target_path_id = p.id
                decision.target_path_label = p.label
                break

    elif action == "new_disambig_path":
        label = str(response.get("disambig_label", "")).strip() or f"{incoming_name} (variant)"
        p = _new_disambig_path(big, label, incoming_name, aliases, facts, vecs)
        decision.kind = "new_disambig_path"
        decision.target_path_id = p.id
        decision.target_path_label = p.label
        decision.disambig_label = label

    else:
        # unknown action — treat as merge_into_big_seed as a safe default
        _merge_flat(big, incoming_name, aliases, facts, vecs)
        decision.kind = "merge_into_big_seed"
        decision.reason += f" | unknown action={action!r}, merged flat"

    await _index_big_seed(qdrant, big)
    _register_aliases_for_big_seed(registry, big.id)


def _merge_flat(
    big: BigSeed, name: str, aliases: list[str], facts: list[Fact], vecs: list[NamedVec]
) -> None:
    for n in [name, *aliases]:
        if n.strip() and n.strip().lower() not in {a.strip().lower() for a in big.aliases}:
            big.aliases.append(n)
    existing_srcs = {nv.source_name.strip().lower() for nv in big.embeddings}
    for nv in vecs:
        if nv.source_name.strip().lower() not in existing_srcs:
            big.embeddings.append(nv)
    remaining = SAMPLE_FACTS_STORED - len(big.facts)
    if remaining > 0:
        big.facts.extend(facts[:remaining])


def _merge_path(
    p: Path, name: str, aliases: list[str], facts: list[Fact], vecs: list[NamedVec]
) -> None:
    for n in [name, *aliases]:
        if n.strip() and n.strip().lower() not in {a.strip().lower() for a in p.aliases}:
            p.aliases.append(n)
    existing_srcs = {nv.source_name.strip().lower() for nv in p.embeddings}
    for nv in vecs:
        if nv.source_name.strip().lower() not in existing_srcs:
            p.embeddings.append(nv)
    remaining = SAMPLE_FACTS_STORED - len(p.facts)
    if remaining > 0:
        p.facts.extend(facts[:remaining])


def _split_big_seed(
    big: BigSeed,
    incoming_name: str,
    incoming_aliases: list[str],
    incoming_facts: list[Fact],
    incoming_vecs: list[NamedVec],
    response: dict,
    *,
    runner_vecs: list[NamedVec],
) -> list[Path]:
    """Partition flat big-seed into paths per LLM directive."""
    raw_paths = response.get("paths") or []
    goes = str(response.get("incoming_goes_to_label", "")).strip().lower()

    src_aliases = {a.strip().lower(): a for a in big.aliases}
    src_embeddings = {nv.source_name.strip().lower(): nv for nv in big.embeddings}

    new_paths: list[Path] = []
    for raw in raw_paths:
        if not isinstance(raw, dict):
            continue
        label = str(raw.get("label", "")).strip()
        if not label:
            continue
        p = Path.new(label=label)
        for a in raw.get("from_parent_aliases", []) or []:
            if not isinstance(a, str):
                continue
            key = a.strip().lower()
            if key in src_aliases:
                p.aliases.append(src_aliases[key])
                if key in src_embeddings:
                    p.embeddings.append(src_embeddings[key])
        # If label isn't already an alias, keep it discoverable
        if not any(a.strip().lower() == label.strip().lower() for a in p.aliases):
            p.aliases.insert(0, label)
        new_paths.append(p)

    # Fallback: if LLM returned zero usable paths, synthesize two-way split
    if not new_paths:
        p1 = Path.new(label=big.canonical_name)
        p1.aliases = list(big.aliases)
        p1.embeddings = list(big.embeddings)
        p1.facts = list(big.facts)
        p2 = Path.new(label=incoming_name)
        new_paths = [p1, p2]

    # Place the incoming into the labelled target path (or the last new path)
    target: Path | None = None
    for p in new_paths:
        if p.label.strip().lower() == goes:
            target = p
            break
    if target is None:
        # find path whose aliases don't include the incoming canonical — treat as new branch
        target = new_paths[-1]
    _merge_path(target, incoming_name, incoming_aliases, incoming_facts, incoming_vecs)

    # Distribute parent facts: each path gets facts whose content mentions any of
    # its aliases (cheap heuristic). Unassigned parent facts go to first path.
    unassigned: list[Fact] = []
    for f in big.facts:
        placed = False
        low = f.content.lower()
        for p in new_paths:
            if any(a.strip().lower() in low for a in p.aliases if a.strip()):
                if len(p.facts) < SAMPLE_FACTS_STORED and f not in p.facts:
                    p.facts.append(f)
                placed = True
                break
        if not placed:
            unassigned.append(f)
    if unassigned and new_paths:
        for f in unassigned:
            if len(new_paths[0].facts) < SAMPLE_FACTS_STORED and f not in new_paths[0].facts:
                new_paths[0].facts.append(f)

    # Commit: flatten parent; paths take over
    big.paths = new_paths
    big.aliases = []
    big.embeddings = []
    big.facts = []
    return new_paths


def _new_disambig_path(
    big: BigSeed,
    label: str,
    incoming_name: str,
    aliases: list[str],
    facts: list[Fact],
    vecs: list[NamedVec],
) -> Path:
    p = Path.new(label=label)
    p.aliases.append(label)
    if incoming_name.strip().lower() != label.strip().lower():
        p.aliases.append(incoming_name)
    for a in aliases:
        if a.strip() and a.strip().lower() not in {x.strip().lower() for x in p.aliases}:
            p.aliases.append(a)
    p.embeddings = vecs
    p.facts.extend(facts[:SAMPLE_FACTS_STORED])
    big.paths.append(p)
    return p


__all__ = ["intake", "EMBED_THRESHOLD"]


_ = json  # silence unused import warning
