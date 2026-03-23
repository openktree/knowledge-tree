"""Threshold sweep: find optimal embedding_threshold and typo_floor.

Sweeps both thresholds and reports precision/recall/F1 at each point.

Usage:
    uv run --project libs/kt-models python scripts/seed_dedup/run_threshold_sweep.py
"""

from __future__ import annotations

import asyncio

from datasets import ALL_PAIRS
from utils import cosine_similarity, embed_pairs


async def main() -> None:
    from kt_models.embeddings import EmbeddingService

    svc = EmbeddingService()
    embeddings = await embed_pairs(ALL_PAIRS, svc)

    # Pre-compute scores
    scores: list[tuple[float, bool | None]] = []
    for pair in ALL_PAIRS:
        score = cosine_similarity(embeddings[pair.name_a], embeddings[pair.name_b])
        scores.append((score, pair.should_merge))

    # Only use pairs with known labels
    labeled = [(s, m) for s, m in scores if m is not None]

    # ── Sweep embedding threshold ──────────────────────────────
    print("Embedding threshold sweep (direct merge signal)")
    print(f"{'Threshold':>10}  {'TP':>4}  {'FP':>4}  {'FN':>4}  {'TN':>4}  {'Prec':>6}  {'Recall':>6}  {'F1':>6}")
    print("-" * 60)

    best_f1 = 0.0
    best_thresh = 0.0

    for thresh_int in range(70, 96):
        thresh = thresh_int / 100.0
        tp = fp = fn = tn = 0
        for score, should_merge in labeled:
            would_merge = score >= thresh
            if would_merge and should_merge:
                tp += 1
            elif would_merge and not should_merge:
                fp += 1
            elif not would_merge and should_merge:
                fn += 1
            else:
                tn += 1

        precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
        recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
        f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0

        marker = " <--" if fp == 0 and f1 > best_f1 else ""
        if fp == 0 and f1 > best_f1:
            best_f1 = f1
            best_thresh = thresh

        print(
            f"{thresh:>10.2f}  {tp:>4}  {fp:>4}  {fn:>4}  {tn:>4}  {precision:>6.3f}  {recall:>6.3f}  {f1:>6.3f}{marker}"
        )

    print(f"\nBest zero-FP threshold: {best_thresh:.2f} (F1={best_f1:.3f})")

    # ── Sweep typo floor ──────────────────────────────────────
    print("\n\nTypo floor sweep (for phonetic+trigram fallback)")
    print("Shows how many TRUE merges fall in the gap [floor, threshold)")
    print(f"  Using embedding threshold = {best_thresh:.2f}\n")

    print(f"{'Floor':>10}  {'In gap':>6}  {'Below':>6}")
    print("-" * 30)

    for floor_int in range(60, 86):
        floor = floor_int / 100.0
        in_gap = sum(1 for score, should_merge in labeled if should_merge and floor <= score < best_thresh)
        below = sum(1 for score, should_merge in labeled if should_merge and score < floor)
        print(f"{floor:>10.2f}  {in_gap:>6}  {below:>6}")


if __name__ == "__main__":
    asyncio.run(main())
