"""End-to-end: raw facts → spaCy NER → big-seed intake → report.

Usage:
    uv run --project services/api python -m experiments.big_seed_from_facts.run \\
        --fixtures experiments/big_seed_from_facts/fixtures \\
        --out experiments/big_seed_from_facts/report.html \\
        --reset-qdrant
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[2] / ".env")

from kt_models.embeddings import EmbeddingService  # noqa: E402
from kt_models.gateway import ModelGateway  # noqa: E402

# reuse the seed-experiment primitives — separate qdrant collection below
from experiments.big_seed_dedup.alias_gen import (  # noqa: E402
    classify_shell_batch,
    generate_aliases_batch,
)
from experiments.big_seed_dedup.big_seed import Decision, Fact, Registry, ShellSeed  # noqa: E402
from experiments.big_seed_dedup.llm import LLMRunner  # noqa: E402
from experiments.big_seed_dedup.multiplex import intake  # noqa: E402
from experiments.big_seed_dedup.qdrant_index import QdrantIndex  # noqa: E402
from experiments.big_seed_dedup.report import generate_report  # noqa: E402

from .extract_entities import Extracted, Ignored, extract_from_facts  # noqa: E402

QDRANT_COLLECTION = "bigseed_facts_experiment_paths"


def _load_fact_fixtures(fixtures_dir: Path) -> tuple[list[dict], list[str]]:
    """Load every *.json in fixtures dir. Return (flat_facts, labels_used)."""
    all_facts: list[dict] = []
    labels: list[str] = []
    for path in sorted(fixtures_dir.glob("*.json")):
        doc = json.loads(path.read_text(encoding="utf-8"))
        if doc.get("fixture_kind") != "facts":
            continue
        labels.append(str(doc.get("fixture_label", path.stem)))
        for f in doc.get("facts", []):
            if isinstance(f, dict) and f.get("content"):
                all_facts.append(f)
    return all_facts, labels


def _extracted_to_intake_items(
    extracted: list[Extracted],
    facts_by_id: dict[str, dict],
) -> list[tuple[str, str, list[Fact]]]:
    """Turn spaCy Extracted items into (name, node_type, facts) tuples."""
    out: list[tuple[str, str, list[Fact]]] = []
    for e in extracted:
        facts = []
        for fid in e.fact_ids:
            f = facts_by_id.get(fid)
            if not f:
                continue
            facts.append(Fact(id=fid, content=str(f.get("content", "")).strip()))
        if not facts:
            continue
        out.append((e.name, e.node_type, facts))
    return out


async def run_facts_pipeline(
    fixtures_dir: Path,
    *,
    runner: LLMRunner,
    qdrant: QdrantIndex,
    min_mentions: int = 1,
    alias_batch_size: int = 20,
    alias_concurrency: int = 5,
    apply_generic_filter: bool = True,
) -> tuple[Registry, list[Ignored], dict[str, int], int, bool]:
    """Phased pipeline:
      A. spaCy extract
      B. exact-name dedup — merge_by_exact_extraction events
      C. alias_gen + shell_classify (parallel LLM)
      D. alias-equivalence dedup (union-find) — merge_by_alias_match events
      E. intake for surviving representatives — genesis / multiplex events
    """
    registry = Registry()

    # ── Phase A: extract ────────────────────────────────────────────
    facts, labels = _load_fact_fixtures(fixtures_dir)
    if not facts:
        print("No fact fixtures found.")
        return registry, [], {}, 0, apply_generic_filter
    facts_by_id = {str(f["id"]): f for f in facts}
    print(f"[A] Loaded {len(facts)} raw facts from fixtures: {labels}")

    print(f"[A] Extracting entities (generic_filter={apply_generic_filter})…")
    extracted, ignored, stats = extract_from_facts(
        facts, min_mentions=min_mentions, apply_generic_filter=apply_generic_filter,
    )
    print(f"  spaCy stats: {stats}")

    # ── Phase B: exact-name dedup events ────────────────────────────
    items = _extracted_to_intake_items(extracted, facts_by_id)
    seen: set[str] = set()
    unique_items: list[tuple[str, str, list[Fact]]] = []
    for name, ntype, fs in items:
        if name in seen:
            continue
        seen.add(name)
        unique_items.append((name, ntype, fs))
    print(f"[B] {len(unique_items)} unique names (from {stats.get('mentions', 0)} mentions)")

    step = 0
    exact_merge_events = 0
    for name, _ntype, fs in unique_items:
        # Emit one event per fact beyond the first (each = one duplicate mention dedup).
        if len(fs) > 1:
            for f in fs[1:]:
                step += 1
                exact_merge_events += 1
                registry.history.append(Decision(
                    step=step,
                    incoming_name=name,
                    incoming_fact_count=1,
                    incoming_fact_samples=[f.content[:240]],
                    kind="merge_by_exact_extraction",
                    target_big_seed_canonical=name,
                    reason=f"second+ extraction of same name (fact_id={f.id})",
                ))
    print(f"[B] emitted {exact_merge_events} merge_by_exact_extraction events")

    # ── Phase C: alias_gen + shell_classify (parallel LLM, both fact-free) ──────────
    print(f"[C] Pre-computing alias + shell for {len(unique_items)} names "
          f"(batch={alias_batch_size}, concurrency={alias_concurrency}) — parallel…")
    names_only = [n for n, _t, _fs in unique_items]
    alias_cache, shell_cache = await asyncio.gather(
        generate_aliases_batch(
            names_only, runner=runner,
            chunk_size=max(40, alias_batch_size), concurrency=alias_concurrency,
        ),
        classify_shell_batch(
            names_only, runner=runner,
            chunk_size=max(40, alias_batch_size), concurrency=alias_concurrency,
        ),
    )
    print(f"[C] alias_gen: {len(alias_cache)} · shell_classify: {len(shell_cache)}")

    # ── Phase D: alias-equivalence dedup (union-find) ───────────────
    # Build DSU over names. Two names are equivalent if one's generated
    # alias list contains the other's canonical (case-insensitive).
    dsu_parent: dict[str, str] = {n: n for n in names_only}

    def find(x: str) -> str:
        while dsu_parent[x] != x:
            dsu_parent[x] = dsu_parent[dsu_parent[x]]
            x = dsu_parent[x]
        return x

    def union(a: str, b: str) -> bool:
        ra, rb = find(a), find(b)
        if ra == rb:
            return False
        dsu_parent[ra] = rb
        return True

    # Index: lowercased_name → canonical_name (for O(1) lookup)
    by_lower = {n.strip().lower(): n for n in names_only}

    alias_bridge: dict[tuple[str, str], str] = {}  # (loser, winner) → alias_that_linked
    for name in names_only:
        entry = alias_cache.get(name)
        if entry is None:
            continue
        aliases = entry[0]
        for a in aliases:
            other = by_lower.get(a.strip().lower())
            if other is None or other == name:
                continue
            if union(name, other):
                alias_bridge[(name, other)] = a

    # Group by root
    groups: dict[str, list[str]] = {}
    for n in names_only:
        root = find(n)
        groups.setdefault(root, []).append(n)

    # For each group pick a representative
    items_by_name = {n: (n, t, fs) for n, t, fs in unique_items}

    def _fact_count(n: str) -> int:
        return len(items_by_name[n][2])

    fold_events = 0
    reps: list[str] = []
    rep_by_member: dict[str, str] = {}
    for _root, members in groups.items():
        members.sort(key=lambda n: (-_fact_count(n), len(n), n.lower()))
        rep = members[0]
        reps.append(rep)
        for m in members:
            rep_by_member[m] = rep
        for m in members[1:]:
            step += 1
            fold_events += 1
            bridge = alias_bridge.get((m, rep)) or alias_bridge.get((rep, m)) or ""
            registry.history.append(Decision(
                step=step,
                incoming_name=m,
                incoming_fact_count=_fact_count(m),
                incoming_fact_samples=[f.content[:240] for f in items_by_name[m][2][:3]],
                kind="merge_by_alias_match",
                target_big_seed_canonical=rep,
                reason=(
                    f"'{m}' merged into '{rep}' — bridged by alias "
                    f"'{bridge}'" if bridge else f"'{m}' merged into '{rep}' via alias DSU"
                ),
                incoming_aliases=alias_cache.get(m, ([], None, None))[0] if alias_cache.get(m) else [],
                alias_gen_usage=alias_cache.get(m, (None, None, None))[1] if alias_cache.get(m) else None,
                alias_gen_response=alias_cache.get(m, (None, None, None))[2] if alias_cache.get(m) else None,
                shell_classification_usage=shell_cache.get(m, (None, None, None, None))[2] if shell_cache.get(m) else None,
                shell_classification_response=shell_cache.get(m, (None, None, None, None))[3] if shell_cache.get(m) else None,
            ))
    print(f"[D] {len(reps)} representatives after alias-equivalence dedup "
          f"({fold_events} merge_by_alias_match events)")

    # Merge facts + aliases across folded members into representative's view.
    merged_facts: dict[str, list[Fact]] = {}
    merged_aliases: dict[str, list[str]] = {}
    for _root, members in groups.items():
        rep = rep_by_member[members[0]]
        fs_acc: list[Fact] = []
        seen_fids: set[str] = set()
        for m in members:
            for f in items_by_name[m][2]:
                if f.id in seen_fids:
                    continue
                seen_fids.add(f.id)
                fs_acc.append(f)
        merged_facts[rep] = fs_acc
        al_acc: list[str] = []
        al_seen: set[str] = set()
        for m in members:
            entry = alias_cache.get(m)
            if not entry:
                continue
            for a in entry[0]:
                k = a.strip().lower()
                if k in al_seen:
                    continue
                al_seen.add(k)
                al_acc.append(a)
            # also include the member's own name as alias when it isn't the rep
            if m != rep:
                k = m.strip().lower()
                if k not in al_seen:
                    al_seen.add(k)
                    al_acc.append(m)
        merged_aliases[rep] = al_acc

    # Pre-embed rep names + merged aliases
    to_embed: list[str] = []
    for r in reps:
        to_embed.append(r)
        to_embed.extend(merged_aliases.get(r, []))
    print(f"[D] Pre-embedding {len(set(to_embed))} unique strings…")
    await runner.embed_batch(to_embed)

    # ── Phase E: intake for each representative ─────────────────────
    print(f"[E] Intake over {len(reps)} representatives…")
    for rep in reps:
        _n, ntype, _fs = items_by_name[rep]
        fs = merged_facts[rep]
        a_entry = alias_cache.get(rep)
        s_entry = shell_cache.get(rep)
        pre = None
        if a_entry is not None and s_entry is not None:
            rep_aliases_from_llm, a_u, a_r = a_entry
            # combine with folded-in aliases
            final_aliases = list(dict.fromkeys(rep_aliases_from_llm + merged_aliases[rep]))
            is_shell, s_reason, s_u, s_r = s_entry
            pre = (final_aliases, is_shell, s_reason, a_u, a_r, s_u, s_r)
        step += 1
        decision = await intake(
            name=rep,
            facts=fs,
            node_type=ntype,
            registry=registry,
            runner=runner,
            qdrant=qdrant,
            step=step,
            precomputed=pre,
        )
        registry.history.append(decision)

    return registry, ignored, stats, len(facts), apply_generic_filter


async def main_async(
    fixtures: Path,
    out: Path,
    cache: Path,
    reset_qdrant: bool,
    model_override: str | None,
    min_mentions: int,
    apply_generic_filter: bool,
) -> None:
    gateway = ModelGateway()
    if model_override:
        gateway.decomposition_model = model_override
    embedder = EmbeddingService()
    runner = LLMRunner(gateway=gateway, embedder=embedder, cache_path=cache)

    qdrant = QdrantIndex(collection_name=QDRANT_COLLECTION)
    await qdrant.ensure(reset=reset_qdrant)

    registry, ignored, stats, fact_count, filter_on = await run_facts_pipeline(
        fixtures, runner=runner, qdrant=qdrant, min_mentions=min_mentions,
        apply_generic_filter=apply_generic_filter,
    )

    print(f"\nRegistry: {len(registry.big_seeds)} big-seed(s), {len(registry.history)} decisions")
    for b in registry.big_seeds[:30]:
        path_info = f"paths={len(b.paths)}" if b.paths else "flat"
        print(f"  - [{b.id}] {b.canonical_name!r} ({b.node_type}) aliases={len(b.aliases)} {path_info}")
    if len(registry.big_seeds) > 30:
        print(f"  … and {len(registry.big_seeds) - 30} more")

    print(f"\nspaCy stats: {stats} · raw facts loaded: {fact_count}")

    # Serialize ignored list for the report renderer
    ignored_section = {
        "filter_on": filter_on,
        "fact_count": fact_count,
        "stats": stats,
        "ignored": [
            {
                "name": i.name,
                "node_type": i.node_type,
                "source": i.source,
                "ner_label": i.ner_label,
                "head_lemma": i.head_lemma,
                "token_count": i.token_count,
                "reason": i.reason,
                "detail": i.detail,
                "fact_count": len(i.fact_ids),
            }
            for i in ignored
        ],
    }
    generate_report(registry, out, model_name=gateway.decomposition_model,
                    ignored_section=ignored_section)


def main() -> None:
    here = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixtures", default=str(here / "fixtures"))
    parser.add_argument("--out", default=str(here / "report.html"))
    parser.add_argument("--cache", default=str(here / "llm_cache.jsonl"))
    parser.add_argument("--reset-qdrant", action="store_true")
    parser.add_argument("--model", default=None)
    parser.add_argument("--min-mentions", type=int, default=1,
                        help="Drop spaCy entities appearing in fewer than this many facts")
    parser.add_argument("--no-generic-filter", dest="generic_filter", action="store_false",
                        help="Disable the NER-label/regex/concreteness pre-filter (default: enabled)")
    parser.set_defaults(generic_filter=True)
    args = parser.parse_args()
    asyncio.run(main_async(
        Path(args.fixtures), Path(args.out), Path(args.cache),
        args.reset_qdrant, args.model, args.min_mentions, args.generic_filter,
    ))


if __name__ == "__main__":
    main()
