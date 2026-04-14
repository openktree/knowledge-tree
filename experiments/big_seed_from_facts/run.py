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
from experiments.big_seed_dedup.big_seed import Fact, Registry  # noqa: E402
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
    facts, labels = _load_fact_fixtures(fixtures_dir)
    if not facts:
        print("No fact fixtures found.")
        return Registry(), [], {}, 0, apply_generic_filter

    facts_by_id = {str(f["id"]): f for f in facts}
    print(f"Loaded {len(facts)} raw facts from fixtures: {labels}")

    print(f"Extracting entities with spaCy (generic_filter={apply_generic_filter})…")
    extracted, ignored, stats = extract_from_facts(
        facts, min_mentions=min_mentions, apply_generic_filter=apply_generic_filter,
    )
    print(f"  stats: {stats}")

    items = _extracted_to_intake_items(extracted, facts_by_id)
    # Dedup by exact name (first wins)
    seen: set[str] = set()
    unique_items: list[tuple[str, str, list[Fact]]] = []
    for name, ntype, fs in items:
        if name in seen:
            continue
        seen.add(name)
        unique_items.append((name, ntype, fs))
    print(f"  {len(unique_items)} unique entity names to run through intake")

    # Parallel precompute: alias_gen (with facts) + shell_classify (name-only)
    print(f"Pre-computing alias + shell for {len(unique_items)} names "
          f"(batch={alias_batch_size}, concurrency={alias_concurrency}) — in parallel…")
    names_only = [n for n, _t, _fs in unique_items]
    alias_cache, shell_cache = await asyncio.gather(
        generate_aliases_batch(
            [(n, fs) for n, _t, fs in unique_items],
            runner=runner, chunk_size=alias_batch_size, concurrency=alias_concurrency,
        ),
        classify_shell_batch(
            names_only, runner=runner,
            chunk_size=max(20, alias_batch_size), concurrency=alias_concurrency,
        ),
    )
    print(f"  alias_gen: {len(alias_cache)} · shell_classify: {len(shell_cache)}")

    # Pre-embed names + generated aliases
    to_embed: list[str] = []
    for name, (aliases, _u, _r) in alias_cache.items():
        to_embed.append(name)
        to_embed.extend(aliases)
    print(f"Pre-embedding {len(set(to_embed))} unique strings…")
    await runner.embed_batch(to_embed)

    # Sequential intake through the registry
    registry = Registry()
    step = 0
    for name, ntype, fs in unique_items:
        step += 1
        a = alias_cache.get(name)
        s = shell_cache.get(name)
        pre = None
        if a is not None and s is not None:
            aliases, a_u, a_r = a
            is_shell, s_reason, s_u, s_r = s
            pre = (aliases, is_shell, s_reason, a_u, a_r, s_u, s_r)
        decision = await intake(
            name=name,
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
