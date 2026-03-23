"""Experiment: Can we skip the LLM gate for high-confidence embedding matches?

Pulls real merged + rejected seed pairs from write-db, computes their
embedding similarity via Qdrant, and checks whether a tiered threshold
(auto-merge above X, LLM-gate between X and Y) would produce false merges.
"""

import asyncio
import os
import re
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

# Load .env from project root
_root = Path(__file__).resolve().parents[4]
load_dotenv(_root / ".env")

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

WRITE_DB_URL = os.environ.get(
    "WRITE_DATABASE_URL",
    "postgresql+asyncpg://kt:localdev@localhost:5435/knowledge_tree_write",
)


@dataclass
class SeedPair:
    name_a: str
    name_b: str
    key_a: str
    key_b: str
    llm_verdict: str  # "merge" or "reject"
    recorded_score: float | None  # from merge reason if available


async def fetch_merged_pairs(session: AsyncSession) -> list[SeedPair]:
    """Fetch all LLM-confirmed merges with their embedding scores."""
    result = await session.execute(
        text("""
        SELECT source_seed_key, target_seed_key, reason
        FROM write_seed_merges
        WHERE operation = 'merge' AND reason LIKE '%LLM confirmed%'
    """)
    )
    pairs = []
    for row in result.fetchall():
        score_match = re.search(r"score=([0-9.]+)", row[2])
        score = float(score_match.group(1)) if score_match else None
        # Get names from keys (strip type prefix, replace hyphens)
        name_a = row[0].split(":", 1)[1].replace("-", " ") if ":" in row[0] else row[0]
        name_b = row[1].split(":", 1)[1].replace("-", " ") if ":" in row[1] else row[1]
        pairs.append(
            SeedPair(
                name_a=name_a,
                name_b=name_b,
                key_a=row[0],
                key_b=row[1],
                llm_verdict="merge",
                recorded_score=score,
            )
        )
    return pairs


async def fetch_rejected_pairs(session: AsyncSession) -> list[SeedPair]:
    """Fetch LLM-rejected pairs (embedding disambiguation routes)."""
    result = await session.execute(
        text("""
        SELECT DISTINCT ON (r.parent_seed_key, r.child_seed_key)
            p.name, c.name, r.parent_seed_key, r.child_seed_key
        FROM write_seed_routes r
        JOIN write_seeds p ON p.key = r.parent_seed_key
        JOIN write_seeds c ON c.key = r.child_seed_key
        WHERE r.ambiguity_type = 'embedding'
          AND r.child_seed_key NOT LIKE '%\\:disambig'
        ORDER BY r.parent_seed_key, r.child_seed_key
    """)
    )
    pairs = []
    for row in result.fetchall():
        pairs.append(
            SeedPair(
                name_a=row[0],
                name_b=row[1],
                key_a=row[2],
                key_b=row[3],
                llm_verdict="reject",
                recorded_score=None,
            )
        )
    return pairs


async def compute_embedding_scores(
    pairs: list[SeedPair],
) -> list[tuple[SeedPair, float]]:
    """Compute fresh embedding similarity for each pair."""
    from kt_models.embeddings import EmbeddingService

    svc = EmbeddingService()
    scored: list[tuple[SeedPair, float]] = []

    # Batch embed all unique names
    all_names = list({p.name_a for p in pairs} | {p.name_b for p in pairs})
    print(f"Embedding {len(all_names)} unique names...")

    # Embed in batches of 100
    name_to_embedding: dict[str, list[float]] = {}
    batch_size = 100
    for i in range(0, len(all_names), batch_size):
        batch = all_names[i : i + batch_size]
        embeddings = await svc.embed_batch(batch)
        for name, emb in zip(batch, embeddings):
            name_to_embedding[name] = emb
        print(f"  Embedded {min(i + batch_size, len(all_names))}/{len(all_names)}")

    # Compute cosine similarity for each pair
    import numpy as np

    for pair in pairs:
        emb_a = name_to_embedding.get(pair.name_a)
        emb_b = name_to_embedding.get(pair.name_b)
        if emb_a is None or emb_b is None:
            continue
        a = np.array(emb_a)
        b = np.array(emb_b)
        cos_sim = float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b)))
        scored.append((pair, cos_sim))

    return scored


def analyze_threshold(
    scored_pairs: list[tuple[SeedPair, float]],
    auto_merge_threshold: float,
) -> dict:
    """Check how many false merges a given auto-merge threshold would cause."""
    auto_merges = [(p, s) for p, s in scored_pairs if s >= auto_merge_threshold]
    false_merges = [(p, s) for p, s in auto_merges if p.llm_verdict == "reject"]
    true_merges = [(p, s) for p, s in auto_merges if p.llm_verdict == "merge"]

    total_merges = len([p for p, _ in scored_pairs if p.llm_verdict == "merge"])
    llm_saved = total_merges - len(true_merges)  # merges that still need LLM

    return {
        "threshold": auto_merge_threshold,
        "auto_merge_count": len(auto_merges),
        "true_auto_merges": len(true_merges),
        "false_merges": len(false_merges),
        "false_merge_pairs": [(p.name_a, p.name_b, s) for p, s in false_merges],
        "llm_calls_saved": len(true_merges),
        "llm_calls_remaining": llm_saved,
        "pct_llm_saved": len(true_merges) / total_merges * 100 if total_merges else 0,
    }


async def run_experiment() -> None:
    engine = create_async_engine(WRITE_DB_URL)
    async_session = async_sessionmaker(engine, expire_on_commit=False)

    async with async_session() as session:
        print("Fetching merged pairs...")
        merged = await fetch_merged_pairs(session)
        print(f"  Found {len(merged)} LLM-confirmed merges")

        print("Fetching rejected pairs...")
        rejected = await fetch_rejected_pairs(session)
        print(f"  Found {len(rejected)} LLM-rejected pairs")

    all_pairs = merged + rejected
    print(f"\nTotal pairs to evaluate: {len(all_pairs)}")

    # Compute embeddings
    scored = await compute_embedding_scores(all_pairs)
    print(f"Scored {len(scored)} pairs\n")

    # Sort rejected pairs by score descending to see the dangerous ones
    rejected_scored = sorted(
        [(p, s) for p, s in scored if p.llm_verdict == "reject"],
        key=lambda x: x[1],
        reverse=True,
    )

    print("=" * 80)
    print("TOP 20 HIGHEST-SCORING REJECTED PAIRS (potential false merges)")
    print("=" * 80)
    for pair, score in rejected_scored[:20]:
        print(f"  {score:.4f}  '{pair.name_a}' vs '{pair.name_b}'")

    print()
    print("=" * 80)
    print("TIERED THRESHOLD ANALYSIS")
    print("=" * 80)

    thresholds = [0.98, 0.97, 0.96, 0.95, 0.94, 0.93, 0.92, 0.91, 0.90]
    for t in thresholds:
        result = analyze_threshold(scored, t)
        status = "SAFE" if result["false_merges"] == 0 else f"UNSAFE ({result['false_merges']} false merges)"
        print(
            f"  threshold={t:.2f}: "
            f"auto-merge={result['true_auto_merges']:3d} | "
            f"false-merge={result['false_merges']:3d} | "
            f"LLM calls saved={result['pct_llm_saved']:.1f}% | "
            f"{status}"
        )
        if result["false_merges"] > 0:
            for name_a, name_b, s in result["false_merge_pairs"][:3]:
                print(f"    CONFLICT: '{name_a}' vs '{name_b}' (score={s:.4f})")

    # Find the optimal safe threshold
    print()
    print("=" * 80)
    print("FINDING OPTIMAL SAFE THRESHOLD")
    print("=" * 80)

    max_rejected_score = rejected_scored[0][1] if rejected_scored else 0.0
    print(f"  Highest rejected pair score: {max_rejected_score:.4f}")
    safe_threshold = max_rejected_score + 0.01
    print(f"  Recommended auto-merge threshold: {safe_threshold:.3f} (rejected max + 0.01 margin)")

    result = analyze_threshold(scored, safe_threshold)
    print(
        f"  At {safe_threshold:.3f}: {result['true_auto_merges']} auto-merges, "
        f"{result['false_merges']} false merges, "
        f"{result['pct_llm_saved']:.1f}% LLM calls saved"
    )

    # ── Tiered analysis: embedding + string heuristics ─────────────
    print()
    print("=" * 80)
    print("TIERED ANALYSIS: EMBEDDING + STRING HEURISTICS")
    print("=" * 80)
    print("Adding containment guard + edit-distance ratio as co-signal")
    print()

    from kt_facts.processing.seed_heuristics import (
        edit_distance,
        is_containment_mismatch,
    )
    from kt_facts.processing.seed_heuristics import (
        is_safe_auto_merge as _is_safe_auto_merge,
    )

    def string_similarity(a: str, b: str) -> float:
        """Normalized edit-distance similarity (1.0 = identical)."""
        d = edit_distance(a.lower(), b.lower())
        max_len = max(len(a), len(b))
        return 1.0 - d / max_len if max_len else 1.0

    def is_safe_auto_merge(pair: SeedPair, emb_score: float, emb_threshold: float) -> bool:
        """Delegate to production is_safe_auto_merge."""
        return _is_safe_auto_merge(pair.name_a, pair.name_b, emb_score, emb_threshold)

    for t in [0.98, 0.97, 0.96, 0.95, 0.94, 0.93, 0.92, 0.91, 0.90]:
        auto = [(p, s) for p, s in scored if is_safe_auto_merge(p, s, t)]
        false_m = [(p, s) for p, s in auto if p.llm_verdict == "reject"]
        true_m = [(p, s) for p, s in auto if p.llm_verdict == "merge"]
        status = "SAFE" if not false_m else f"UNSAFE ({len(false_m)} false merges)"
        print(
            f"  threshold={t:.2f}: "
            f"auto-merge={len(true_m):3d} | "
            f"false-merge={len(false_m):3d} | "
            f"LLM calls saved={len(true_m) / len([p for p, _ in scored if p.llm_verdict == 'merge']) * 100:.1f}% | "
            f"{status}"
        )
        if false_m:
            for p, s in false_m[:5]:
                ss = string_similarity(p.name_a, p.name_b)
                print(f"    CONFLICT: '{p.name_a}' vs '{p.name_b}' (emb={s:.4f}, str={ss:.3f})")

    # ── Analyze: which rejected pairs are LLM bugs vs real? ────────
    print()
    print("=" * 80)
    print("REJECTED PAIR CLASSIFICATION (embedding >= 0.93)")
    print("=" * 80)
    high_rejected = [(p, s) for p, s in scored if p.llm_verdict == "reject" and is_safe_auto_merge(p, s, 0.93)]
    for p, s in sorted(high_rejected, key=lambda x: -x[1]):
        ss = string_similarity(p.name_a, p.name_b)
        cm = is_containment_mismatch(p.name_a.lower(), p.name_b.lower())
        print(f"  emb={s:.4f} str={ss:.3f} cm={cm} | '{p.name_a}' vs '{p.name_b}'")

    # Find best safe threshold with heuristics
    print()
    for t in [0.90, 0.91, 0.92, 0.93, 0.94, 0.95, 0.96, 0.97, 0.98]:
        auto = [(p, s) for p, s in scored if is_safe_auto_merge(p, s, t)]
        false_m = [(p, s) for p, s in auto if p.llm_verdict == "reject"]
        if not false_m:
            true_m = [(p, s) for p, s in auto if p.llm_verdict == "merge"]
            total_merges = len([p for p, _ in scored if p.llm_verdict == "merge"])
            print(f"  BEST SAFE THRESHOLD: {t:.2f}")
            print(f"    Auto-merges: {len(true_m)} ({len(true_m) / total_merges * 100:.1f}% of LLM calls saved)")
            print("    False merges: 0")
            # Show some examples of what auto-merges at this level
            print("    Example auto-merges:")
            for p, s in sorted(true_m, key=lambda x: x[1])[:5]:
                print(f"      {s:.4f} '{p.name_a}' vs '{p.name_b}'")
            break

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(run_experiment())
