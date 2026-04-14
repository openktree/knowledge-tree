"""Multiplexer admit logic: alias match → embedding distance → LLM decision."""

from __future__ import annotations

from .alias_gen import generate_aliases
from .big_seed import BigSeed, Decision, Fact, Path
from .llm import LLMRunner, cosine

# Thresholds (kept local — experiment only, no settings dep)
EMBED_REJECT_FLOOR = 0.70       # below this vs ALL paths → reject (different concept)
EMBED_AUTO_ROUTE = 0.95         # above this AND surface forms rhyme → auto-route
SAMPLE_FACTS_FOR_LLM = 3        # per-path sample shown to multiplex LLM
SAMPLE_FACTS_STORED = 10        # kept on path for future LLM context


def _norm(s: str) -> str:
    return s.strip().lower()


def _alias_hit(name: str, big: BigSeed) -> tuple[str | None, str]:
    """Return (path_id, reason) if incoming name hits an alias; path_id None = parent alias."""
    n = _norm(name)
    if n == _norm(big.canonical_name):
        return None, "canonical_name_match"
    if any(_norm(a) == n for a in big.merged_surface_forms):
        return None, "parent_merged_form_match"
    for p in big.paths:
        if _norm(p.label) == n:
            return p.id, "path_label_match"
        if any(_norm(a) == n for a in p.known_aliases):
            return p.id, "path_known_alias_match"
        if any(_norm(o) == n for o in p.merged_surface_forms):
            return p.id, "path_merged_form_match"
    return None, ""


_MULTIPLEX_SYSTEM = """\
You are disambiguating incoming surface forms against an existing big-seed in a
knowledge graph.

A big-seed has a canonical name plus N disambiguation paths. Each path is ONE
real-world entity/concept sharing the surface form with the others.

Given the canonical + existing paths (with sample facts) + an INCOMING name
(with sample facts), decide exactly one action:

- "merge_path": incoming refers to the SAME entity as an existing path → merge into that path_id
- "alias_to_parent": incoming is a surface variant of the canonical itself (not a separate sub-entity)
- "new_path": incoming is a distinct entity/concept that happens to share the surface form → create new path
- "reject": incoming is actually a DIFFERENT concept that should not live under this big-seed at all

Output JSON exactly:
{"action": "merge_path|alias_to_parent|new_path|reject",
 "path_id": "..." or null,
 "new_label": "..." or null,
 "reason": "short justification"}
"""


def _build_multiplex_user(big: BigSeed, name: str, facts: list[Fact]) -> str:
    lines: list[str] = []
    lines.append(f'Canonical: "{big.canonical_name}" (node_type={big.node_type})')
    lines.append(f"Parent merged surface forms: {big.merged_surface_forms or '[]'}")
    lines.append("")
    lines.append(f"Existing paths ({len(big.paths)}):")
    if not big.paths:
        lines.append("  (none)")
    for p in big.paths:
        lines.append(f'  - path_id={p.id}  label="{p.label}"')
        lines.append(f"    known aliases: {p.known_aliases or '[]'}")
        lines.append(f"    merged surface forms: {p.merged_surface_forms or '[]'}")
        for f in p.facts[:SAMPLE_FACTS_FOR_LLM]:
            lines.append(f"    • {f.content[:240]}")
    lines.append("")
    lines.append(f'INCOMING: "{name}"')
    lines.append("Incoming sample facts:")
    for f in facts[:SAMPLE_FACTS_FOR_LLM]:
        lines.append(f"  • {f.content[:240]}")
    lines.append("")
    lines.append("Return JSON only.")
    return "\n".join(lines)


async def _score_embeddings(
    big: BigSeed,
    incoming_vec: list[float],
) -> tuple[dict[str, float], float, str | None]:
    """Return {path_label: score}, best_score, best_path_id."""
    scores: dict[str, float] = {}
    best_score = 0.0
    best_id: str | None = None
    for p in big.paths:
        if not p.embedding:
            continue
        s = cosine(incoming_vec, p.embedding)
        scores[p.label] = s
        if s > best_score:
            best_score = s
            best_id = p.id
    return scores, best_score, best_id


def _fact_samples(facts: list[Fact]) -> list[str]:
    return [f.content[:240] for f in facts[:SAMPLE_FACTS_FOR_LLM] if f.content.strip()]


async def admit(
    big: BigSeed,
    name: str,
    facts: list[Fact],
    *,
    runner: LLMRunner,
    step: int,
) -> Decision:
    """Admit one incoming surface form. Mutates big. Returns decision record."""

    decision = Decision(
        step=step,
        incoming_name=name,
        incoming_fact_count=len(facts),
        incoming_fact_samples=_fact_samples(facts),
    )

    # ── 1. Alias match ────────────────────────────────────────────────
    path_id, reason = _alias_hit(name, big)
    if reason:
        decision.kind = "alias_match"
        decision.alias_gate = reason
        decision.reason = reason
        if path_id:
            p = big.find_path(path_id)
            if p:
                decision.routed_to_path_id = p.id
                decision.routed_to_path_label = p.label
                _absorb_into_path(p, name, facts)
        else:
            _absorb_into_parent(big, name)
        return decision

    # ── 2. Embedding distance ─────────────────────────────────────────
    vec = await runner.embed(name)
    scores, best_score, best_id = await _score_embeddings(big, vec)
    decision.embed_scores = scores
    decision.best_embed_score = best_score

    if big.paths and best_score < EMBED_REJECT_FLOOR:
        decision.kind = "embed_reject"
        decision.reason = f"best embed {best_score:.3f} < floor {EMBED_REJECT_FLOOR}"
        return decision

    if best_id and best_score >= EMBED_AUTO_ROUTE:
        p = big.find_path(best_id)
        if p and _surface_compatible(name, p):
            decision.kind = "embed_auto_route"
            decision.routed_to_path_id = p.id
            decision.routed_to_path_label = p.label
            decision.reason = f"embed {best_score:.3f} >= {EMBED_AUTO_ROUTE}"
            _absorb_into_path(p, name, facts)
            return decision

    # ── 3. LLM multiplexer ────────────────────────────────────────────
    user = _build_multiplex_user(big, name, facts)
    response, usage = await runner.call_json(
        kind="multiplex",
        system_prompt=_MULTIPLEX_SYSTEM,
        user_content=user,
        max_tokens=400,
    )
    decision.multiplex_usage = usage
    decision.multiplex_response = response if isinstance(response, dict) else {}

    action = str(response.get("action", "reject")) if isinstance(response, dict) else "reject"
    llm_reason = str(response.get("reason", "")) if isinstance(response, dict) else ""
    llm_path_id = response.get("path_id") if isinstance(response, dict) else None
    new_label = response.get("new_label") if isinstance(response, dict) else None

    decision.reason = llm_reason or action

    if action == "merge_path" and llm_path_id:
        p = big.find_path(str(llm_path_id))
        if p:
            decision.kind = "llm_merge_path"
            decision.routed_to_path_id = p.id
            decision.routed_to_path_label = p.label
            _absorb_into_path(p, name, facts)
            return decision
        # fall through to new_path if id invalid

    if action == "alias_to_parent":
        decision.kind = "llm_alias_to_parent"
        _absorb_into_parent(big, name)
        return decision

    if action == "reject":
        decision.kind = "llm_reject"
        return decision

    # new_path — create, generate aliases, embed label
    label = str(new_label).strip() if isinstance(new_label, str) and new_label.strip() else name
    new_path = Path.new(label=label)
    new_path.merged_surface_forms.append(name)
    new_path.facts.extend(facts[:SAMPLE_FACTS_STORED])
    new_path.embedding = await runner.embed(label)

    aliases, alias_usage, alias_resp = await generate_aliases(label, facts, runner=runner)
    new_path.known_aliases = aliases
    new_path.alias_gen_usage = alias_usage
    new_path.alias_gen_response = alias_resp

    big.paths.append(new_path)

    decision.kind = "llm_new_path"
    decision.routed_to_path_id = new_path.id
    decision.routed_to_path_label = new_path.label
    decision.alias_gen_usage = alias_usage
    decision.alias_gen_response = alias_resp
    return decision


def _is_more_specific(new: str, old: str) -> bool:
    """Longer-wins heuristic: new is more specific iff strictly longer AND
    contains old as whole-word substring. Fixes prod's short-canonical bug.
    """
    n = new.strip()
    o = old.strip()
    if len(n) <= len(o) + 2:
        return False
    nl = n.lower()
    ol = o.lower()
    if ol not in nl:
        return False
    idx = nl.find(ol)
    before_ok = idx == 0 or not nl[idx - 1].isalnum()
    end = idx + len(ol)
    after_ok = end == len(nl) or not nl[end].isalnum()
    return before_ok and after_ok


def _absorb_into_path(p: Path, name: str, facts: list[Fact]) -> None:
    if name not in p.merged_surface_forms and _norm(name) != _norm(p.label):
        p.merged_surface_forms.append(name)
    remaining = SAMPLE_FACTS_STORED - len(p.facts)
    if remaining > 0:
        p.facts.extend(facts[:remaining])
    if _is_more_specific(name, p.label):
        # demote old label into merged_surface_forms
        if p.label not in p.merged_surface_forms:
            p.merged_surface_forms.insert(0, p.label)
        p.label = name


def _absorb_into_parent(big: BigSeed, name: str) -> None:
    if _norm(name) == _norm(big.canonical_name):
        return
    lowered = {a.strip().lower() for a in big.merged_surface_forms}
    if _is_more_specific(name, big.canonical_name):
        old = big.canonical_name
        big.canonical_name = name
        if old.lower() not in lowered:
            big.merged_surface_forms.append(old)
        return
    if name.strip().lower() not in lowered:
        big.merged_surface_forms.append(name)


def _surface_compatible(name: str, p: Path) -> bool:
    n = _norm(name)
    if n == _norm(p.label):
        return True
    for a in p.known_aliases + p.merged_surface_forms:
        if _norm(a) == n:
            return True
    lbl = _norm(p.label)
    if len(n) >= 4 and (n in lbl or lbl in n):
        return True
    return False


async def seed_from_first(
    canonical_name: str,
    node_type: str,
    facts: list[Fact],
    *,
    runner: LLMRunner,
) -> tuple[BigSeed, Decision]:
    """Initialize a BigSeed from its first member. Runs alias_gen once."""
    big = BigSeed(canonical_name=canonical_name, node_type=node_type)
    first_path = Path.new(label=canonical_name)
    first_path.facts.extend(facts[:SAMPLE_FACTS_STORED])
    first_path.embedding = await runner.embed(canonical_name)

    aliases, alias_usage, alias_resp = await generate_aliases(canonical_name, facts, runner=runner)
    first_path.known_aliases = aliases
    first_path.alias_gen_usage = alias_usage
    first_path.alias_gen_response = alias_resp
    big.paths.append(first_path)

    dec = Decision(
        step=0,
        incoming_name=canonical_name,
        incoming_fact_count=len(facts),
        incoming_fact_samples=_fact_samples(facts),
        kind="seed_init",
        routed_to_path_id=first_path.id,
        routed_to_path_label=first_path.label,
        reason="seed initialized from first family member",
        alias_gen_usage=alias_usage,
        alias_gen_response=alias_resp,
    )
    big.history.append(dec)
    return big, dec


__all__ = ["admit", "seed_from_first", "EMBED_REJECT_FLOOR", "EMBED_AUTO_ROUTE"]
