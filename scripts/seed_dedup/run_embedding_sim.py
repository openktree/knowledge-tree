"""Core embedding similarity experiment for seed dedup.

Embeds all test pairs and reports cosine similarity vs threshold.
Groups by category, shows per-category accuracy.
Exit code 1 if any should_merge=True is missed or should_merge=False is matched.

Usage:
    uv run --project libs/kt-models python scripts/seed_dedup/run_embedding_sim.py
"""

from __future__ import annotations

import asyncio
import sys
from collections import defaultdict

from datasets import ALL_PAIRS
from utils import cosine_similarity, embed_pairs, load_settings_thresholds

from kt_facts.processing.seed_heuristics import is_acronym_match, is_containment_mismatch


async def main() -> None:
    from kt_models.embeddings import EmbeddingService

    threshold, _ = load_settings_thresholds()
    svc = EmbeddingService()

    embeddings = await embed_pairs(ALL_PAIRS, svc)

    # Track per-category stats
    category_stats: dict[str, dict[str, int]] = defaultdict(lambda: {"correct": 0, "wrong": 0, "unknown": 0})

    print()
    header = f"{'Pair':<65} {'Score':>6}  {'Result':>8}  {'Expected':>8}  {'Acro':>5}  {'Cont':>5}  {'Status'}"
    print(header)
    print("-" * len(header))

    total_correct = 0
    total_wrong = 0
    total_unknown = 0

    current_category = None
    for pair in sorted(ALL_PAIRS, key=lambda p: p.category):
        if pair.category != current_category:
            if current_category is not None:
                print()
            current_category = pair.category
            print(f"  [{pair.category}]")

        a_emb = embeddings[pair.name_a]
        b_emb = embeddings[pair.name_b]
        score = cosine_similarity(a_emb, b_emb)

        would_merge = score >= threshold
        merge_str = "MERGE" if would_merge else "skip"

        # Heuristic signals
        is_acronym = is_acronym_match(pair.name_a, pair.name_b)
        is_containment = is_containment_mismatch(pair.name_a.lower(), pair.name_b.lower())
        acro_str = "ACR" if is_acronym else ""
        cont_str = "BLK" if is_containment else ""

        if pair.should_merge is None:
            status = "?"
            total_unknown += 1
            category_stats[pair.category]["unknown"] += 1
        elif would_merge == pair.should_merge:
            status = "ok"
            total_correct += 1
            category_stats[pair.category]["correct"] += 1
        else:
            status = "WRONG" if pair.should_merge else "FALSE+"
            total_wrong += 1
            category_stats[pair.category]["wrong"] += 1

        expected_str = "merge" if pair.should_merge else ("skip" if pair.should_merge is False else "?")
        pair_str = f"{pair.name_a} <-> {pair.name_b}"
        print(
            f"  {pair_str:<63} {score:>6.4f}  {merge_str:>8}  {expected_str:>8}  {acro_str:>5}  {cont_str:>5}  {status}"
        )

    # Category summary
    print()
    print(f"{'Category':<20} {'Correct':>8} {'Wrong':>8} {'Unknown':>8}")
    print("-" * 48)
    for cat in sorted(category_stats):
        s = category_stats[cat]
        print(f"{cat:<20} {s['correct']:>8} {s['wrong']:>8} {s['unknown']:>8}")

    # Overall
    print()
    print(f"Threshold: {threshold}")
    print(f"Results: {total_correct} correct, {total_wrong} wrong, {total_unknown} unknown")

    if total_wrong > 0:
        print(f"\n{total_wrong} pair(s) produced unexpected results")
        sys.exit(1)
    else:
        print("\nAll classified pairs match expectations.")


if __name__ == "__main__":
    asyncio.run(main())
