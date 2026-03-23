"""Full dedup pipeline experiment: all signals combined.

Simulates the complete deduplicate_seed() flow using real embeddings and
all heuristic signals (trigram, alias, acronym, containment guard, phonetic,
embedding threshold, LLM gate) with mocked DB/Qdrant layers.

This is the ground-truth experiment — every other script tests one signal
in isolation. If this script's accuracy degrades, the production pipeline
is broken.

Usage:
    uv run --project libs/kt-models python scripts/seed_dedup/run_full_pipeline.py
"""

from __future__ import annotations

import asyncio
import sys
from collections import defaultdict
from dataclasses import dataclass

import numpy as np
from datasets import ALL_PAIRS, SeedPair
from utils import cosine_similarity, embed_pairs, load_settings_thresholds

from kt_facts.processing.seed_heuristics import (
    DedupSignals,
    compute_phonetic_code,
    evaluate_dedup_signals,
    is_acronym_match,
    is_containment_mismatch,
    is_prefix_disambiguation_candidate,
    trigram_similarity,
)


@dataclass
class PipelineResult:
    """Result of running one pair through the simulated pipeline."""

    pair: SeedPair
    embedding_score: float
    # Which signal fired (or "none")
    signal: str
    # Final decision
    would_merge: bool
    # Heuristic flags
    is_acronym: bool = False
    is_containment_block: bool = False
    is_prefix_disambig: bool = False
    phonetic_match: bool = False
    # Was the embedding above threshold?
    above_threshold: bool = False
    above_typo_floor: bool = False


def _compute_phonetic_match(name_a: str, name_b: str) -> bool:
    """Check if two names share a phonetic code."""
    code_a = compute_phonetic_code(name_a.split()[0]) if name_a.strip() else ""
    code_b = compute_phonetic_code(name_b.split()[0]) if name_b.strip() else ""
    return bool(code_a and code_a == code_b)


def run_pipeline(
    pair: SeedPair,
    embeddings: dict[str, np.ndarray],
    embed_threshold: float,
    typo_floor: float,
    trigram_threshold: float = 0.3,
) -> PipelineResult:
    """Simulate the full deduplicate_seed pipeline for one pair.

    Uses the shared evaluate_dedup_signals() decision tree from seed_heuristics.
    """
    a_emb = embeddings[pair.name_a]
    b_emb = embeddings[pair.name_b]
    score = cosine_similarity(a_emb, b_emb)

    acro = is_acronym_match(pair.name_a, pair.name_b)
    containment = is_containment_mismatch(pair.name_a.lower(), pair.name_b.lower())
    prefix = is_prefix_disambiguation_candidate(pair.name_a, pair.name_b)
    phonetic = _compute_phonetic_match(pair.name_a, pair.name_b)
    trigram = trigram_similarity(pair.name_a, pair.name_b) >= trigram_threshold
    name_match = pair.name_a.lower().strip() == pair.name_b.lower().strip()

    signals = DedupSignals(
        embedding_score=score,
        trigram_match=trigram,
        is_acronym=acro,
        is_containment_block=containment,
        is_prefix_disambig=prefix,
        phonetic_match=phonetic,
        alias_exact_match=name_match,
    )
    decision = evaluate_dedup_signals(signals, embed_threshold, typo_floor)

    return PipelineResult(
        pair=pair,
        embedding_score=score,
        signal=decision.signal,
        would_merge=decision.would_merge,
        is_acronym=acro,
        is_containment_block=containment,
        is_prefix_disambig=prefix,
        phonetic_match=phonetic,
        above_threshold=score >= embed_threshold,
        above_typo_floor=score >= typo_floor,
    )


async def main() -> None:
    from kt_models.embeddings import EmbeddingService

    embed_threshold, typo_floor = load_settings_thresholds()
    svc = EmbeddingService()

    embeddings = await embed_pairs(ALL_PAIRS, svc)

    # ── Run pipeline on all pairs ──────────────────────────────────
    results: list[PipelineResult] = []
    for pair in ALL_PAIRS:
        r = run_pipeline(pair, embeddings, embed_threshold, typo_floor)
        results.append(r)

    # ── Display results ────────────────────────────────────────────
    print()
    header = f"  {'Pair':<58} {'Score':>6}  {'Signal':<24} {'Result':>6}  {'Expected':>8}  {'Status'}"
    print(header)
    print("  " + "-" * (len(header) - 2))

    category_stats: dict[str, dict[str, int]] = defaultdict(lambda: {"tp": 0, "fp": 0, "tn": 0, "fn": 0, "unknown": 0})
    signal_stats: dict[str, dict[str, int]] = defaultdict(lambda: {"tp": 0, "fp": 0, "tn": 0, "fn": 0})

    total_tp = total_fp = total_tn = total_fn = total_unknown = 0

    current_cat = None
    for r in sorted(results, key=lambda x: x.pair.category):
        if r.pair.category != current_cat:
            if current_cat is not None:
                print()
            current_cat = r.pair.category
            print(f"  [{r.pair.category}]")

        merge_str = "MERGE" if r.would_merge else "skip"
        expected = r.pair.should_merge

        if expected is None:
            status = "?"
            total_unknown += 1
            category_stats[r.pair.category]["unknown"] += 1
        elif r.would_merge and expected:
            status = "TP"
            total_tp += 1
            category_stats[r.pair.category]["tp"] += 1
            signal_stats[r.signal]["tp"] += 1
        elif r.would_merge and not expected:
            status = "FP!"
            total_fp += 1
            category_stats[r.pair.category]["fp"] += 1
            signal_stats[r.signal]["fp"] += 1
        elif not r.would_merge and expected:
            status = "FN"
            total_fn += 1
            category_stats[r.pair.category]["fn"] += 1
            signal_stats[r.signal]["fn"] += 1
        else:
            status = "TN"
            total_tn += 1
            category_stats[r.pair.category]["tn"] += 1
            signal_stats[r.signal]["tn"] += 1

        expected_str = "merge" if expected else ("skip" if expected is False else "?")
        pair_str = f"{r.pair.name_a} <-> {r.pair.name_b}"
        print(f"  {pair_str:<58} {r.embedding_score:>6.4f}  {r.signal:<24} {merge_str:>6}  {expected_str:>8}  {status}")

    # ── Category breakdown ─────────────────────────────────────────
    print()
    print(f"  {'Category':<22} {'TP':>4} {'FP':>4} {'TN':>4} {'FN':>4} {'?':>4}  {'Prec':>6} {'Recall':>6} {'F1':>6}")
    print("  " + "-" * 72)

    for cat in sorted(category_stats):
        s = category_stats[cat]
        tp, fp, tn, fn = s["tp"], s["fp"], s["tn"], s["fn"]
        prec = tp / (tp + fp) if (tp + fp) > 0 else 0.0
        rec = tp / (tp + fn) if (tp + fn) > 0 else 0.0
        f1 = 2 * prec * rec / (prec + rec) if (prec + rec) > 0 else 0.0
        print(f"  {cat:<22} {tp:>4} {fp:>4} {tn:>4} {fn:>4} {s['unknown']:>4}  {prec:>6.1%} {rec:>6.1%} {f1:>6.1%}")

    # ── Signal attribution ─────────────────────────────────────────
    print()
    print(f"  {'Signal':<28} {'TP':>4} {'FP':>4} {'TN':>4} {'FN':>4}")
    print("  " + "-" * 48)
    for sig in sorted(signal_stats):
        s = signal_stats[sig]
        print(f"  {sig:<28} {s['tp']:>4} {s['fp']:>4} {s['tn']:>4} {s['fn']:>4}")

    # ── Overall ────────────────────────────────────────────────────
    total_labeled = total_tp + total_fp + total_tn + total_fn
    precision = total_tp / (total_tp + total_fp) if (total_tp + total_fp) > 0 else 0.0
    recall = total_tp / (total_tp + total_fn) if (total_tp + total_fn) > 0 else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0

    print()
    print(f"  Thresholds: embedding={embed_threshold}, typo_floor={typo_floor}")
    print(
        f"  Total labeled: {total_labeled} (TP={total_tp} FP={total_fp} TN={total_tn} FN={total_fn} ?={total_unknown})"
    )
    print(f"  Precision: {precision:.1%}  Recall: {recall:.1%}  F1: {f1:.1%}")
    print()

    if total_fp > 0:
        print(f"  FALSE POSITIVES ({total_fp}):")
        for r in results:
            if r.pair.should_merge is False and r.would_merge:
                print(f"    {r.pair.name_a} <-> {r.pair.name_b}  (score={r.embedding_score:.4f}, signal={r.signal})")
        print()

    if total_fn > 0:
        print(f"  FALSE NEGATIVES ({total_fn}):")
        for r in results:
            if r.pair.should_merge is True and not r.would_merge:
                print(
                    f"    {r.pair.name_a} <-> {r.pair.name_b}  "
                    f"(score={r.embedding_score:.4f}, signal={r.signal}, "
                    f"acro={r.is_acronym}, phon={r.phonetic_match}, "
                    f"floor={r.above_typo_floor})"
                )
        print()

    # Separate embedding FPs (deferred to LLM gate in production) from
    # heuristic FPs (real bugs in the heuristic pipeline).
    heuristic_fps = [
        r
        for r in results
        if r.pair.should_merge is False
        and r.would_merge
        and r.signal not in ("embedding", "embedding_blocked_by_prefix")
    ]
    embedding_fps = [
        r for r in results if r.pair.should_merge is False and r.would_merge and r.signal in ("embedding",)
    ]

    if embedding_fps:
        print(f"  NOTE: {len(embedding_fps)} embedding FP(s) deferred to LLM gate in production:")
        for r in embedding_fps:
            print(f"    {r.pair.name_a} <-> {r.pair.name_b}  (score={r.embedding_score:.4f})")
        print()

    if heuristic_fps:
        print(f"  FAIL: {len(heuristic_fps)} heuristic false positive(s) — pipeline bug")
        for r in heuristic_fps:
            print(f"    {r.pair.name_a} <-> {r.pair.name_b}  (signal={r.signal})")
        sys.exit(1)
    else:
        print(
            f"  PASS: 0 heuristic false positives, "
            f"{len(embedding_fps)} embedding FP(s) deferred to LLM gate, "
            f"{total_fn} false negatives (recall={recall:.1%})"
        )


if __name__ == "__main__":
    asyncio.run(main())
