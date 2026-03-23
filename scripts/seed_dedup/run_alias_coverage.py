"""Alias & acronym heuristic coverage experiment.

Tests pure functions without DB/Qdrant:
- Acronym heuristic accuracy on all pairs
- Containment guard accuracy on all pairs
- Combined signal coverage matrix

Usage:
    uv run --project libs/kt-facts python scripts/seed_dedup/run_alias_coverage.py
"""

from __future__ import annotations

import sys

from datasets import (
    ACRONYM_PAIRS,
    ALIAS_PAIRS,
    ALL_PAIRS,
    CONCEPT_SYNONYM_PAIRS,
    CONTAINMENT_PAIRS,
    DIFFERENT_ENTITY_PAIRS,
    LOCATION_VARIANT_PAIRS,
    NEGATIVE_BATTERY_PAIRS,
    ORG_VARIANT_PAIRS,
    PERSON_VARIANT_PAIRS,
    SUBTLE_PAIRS,
    TYPO_PAIRS,
    SeedPair,
)

from kt_facts.processing.seed_heuristics import is_acronym_match, is_containment_mismatch


def test_acronym_heuristic(pairs: list[SeedPair]) -> dict[str, int]:
    """Test acronym heuristic on all pairs. Returns stats dict."""
    stats: dict[str, int] = {"tp": 0, "fp": 0, "tn": 0, "fn": 0, "unknown": 0}

    print("\n=== Acronym Heuristic Results ===\n")
    header = f"{'Pair':<65} {'Acronym?':>8}  {'Expected':>8}  {'Status'}"
    print(header)
    print("-" * len(header))

    current_category = None
    for pair in sorted(ALL_PAIRS, key=lambda p: p.category):
        if pair.category != current_category:
            if current_category is not None:
                print()
            current_category = pair.category
            print(f"  [{pair.category}]")

        is_match = is_acronym_match(pair.name_a, pair.name_b)
        match_str = "YES" if is_match else "no"

        if pair.should_merge is None:
            status = "?"
            stats["unknown"] += 1
        elif is_match and pair.should_merge:
            status = "TP"
            stats["tp"] += 1
        elif is_match and not pair.should_merge:
            status = "FP!"
            stats["fp"] += 1
        elif not is_match and pair.should_merge:
            status = "FN"
            stats["fn"] += 1
        else:
            status = "TN"
            stats["tn"] += 1

        expected = "merge" if pair.should_merge else ("skip" if pair.should_merge is False else "?")
        pair_str = f"{pair.name_a} <-> {pair.name_b}"
        print(f"  {pair_str:<63} {match_str:>8}  {expected:>8}  {status}")

    return stats


def test_containment_guard(pairs: list[SeedPair]) -> dict[str, int]:
    """Test containment guard on all pairs. Returns stats dict."""
    stats: dict[str, int] = {"correct_block": 0, "correct_allow": 0, "false_block": 0, "false_allow": 0}

    print("\n=== Containment Guard Results ===\n")
    header = f"{'Pair':<65} {'Blocked?':>8}  {'Expected':>8}  {'Status'}"
    print(header)
    print("-" * len(header))

    for pair in sorted(ALL_PAIRS, key=lambda p: p.category):
        if pair.should_merge is None:
            continue  # Skip unknown pairs

        is_blocked = is_containment_mismatch(pair.name_a.lower(), pair.name_b.lower())
        block_str = "BLOCK" if is_blocked else "allow"

        if is_blocked and not pair.should_merge:
            status = "ok"  # Correctly blocked a non-merge
            stats["correct_block"] += 1
        elif not is_blocked and pair.should_merge:
            status = "ok"  # Correctly allowed a merge
            stats["correct_allow"] += 1
        elif is_blocked and pair.should_merge:
            status = "FALSE_BLOCK"  # Blocked a true merge
            stats["false_block"] += 1
        else:
            status = "ok"  # Not blocked, not a merge — neutral
            stats["correct_allow"] += 1

        expected = "merge" if pair.should_merge else "skip"
        pair_str = f"{pair.name_a} <-> {pair.name_b}"
        print(f"  {pair_str:<63} {block_str:>8}  {expected:>8}  {status}")

    return stats


def print_coverage_matrix() -> None:
    """Show which signal catches which category of pairs."""
    categories = [
        ("typo", TYPO_PAIRS),
        ("different_entity", DIFFERENT_ENTITY_PAIRS),
        ("alias", ALIAS_PAIRS),
        ("containment", CONTAINMENT_PAIRS),
        ("subtle", SUBTLE_PAIRS),
        ("acronym", ACRONYM_PAIRS),
        ("person_variant", PERSON_VARIANT_PAIRS),
        ("org_variant", ORG_VARIANT_PAIRS),
        ("location_variant", LOCATION_VARIANT_PAIRS),
        ("concept_synonym", CONCEPT_SYNONYM_PAIRS),
        ("negative_battery", NEGATIVE_BATTERY_PAIRS),
    ]

    print("\n=== Signal Coverage Matrix ===\n")
    header = f"{'Category':<20} {'Total':>6} {'Acronym↑':>9} {'Contain↓':>9} {'Neither':>8}"
    print(header)
    print("-" * len(header))

    for cat_name, pairs in categories:
        acronym_hits = sum(1 for p in pairs if is_acronym_match(p.name_a, p.name_b))
        contain_hits = sum(1 for p in pairs if is_containment_mismatch(p.name_a.lower(), p.name_b.lower()))
        neither = len(pairs) - acronym_hits - contain_hits
        # Some pairs may trigger both
        both = sum(
            1
            for p in pairs
            if is_acronym_match(p.name_a, p.name_b) and is_containment_mismatch(p.name_a.lower(), p.name_b.lower())
        )
        neither += both  # adjust for double-counting

        print(f"{cat_name:<20} {len(pairs):>6} {acronym_hits:>9} {contain_hits:>9} {neither:>8}")


def main() -> None:
    print(f"Testing {len(ALL_PAIRS)} pairs across {len(set(p.category for p in ALL_PAIRS))} categories\n")

    acro_stats = test_acronym_heuristic(ALL_PAIRS)
    contain_stats = test_containment_guard(ALL_PAIRS)
    print_coverage_matrix()

    # Summary
    print("\n=== Summary ===\n")
    print("Acronym Heuristic:")
    print(f"  True Positives:  {acro_stats['tp']}")
    print(f"  False Positives: {acro_stats['fp']}")
    print(f"  True Negatives:  {acro_stats['tn']}")
    print(f"  False Negatives: {acro_stats['fn']}")
    print(f"  Unknown:         {acro_stats['unknown']}")
    if acro_stats["tp"] + acro_stats["fp"] > 0:
        precision = acro_stats["tp"] / (acro_stats["tp"] + acro_stats["fp"])
        print(f"  Precision:       {precision:.2%}")
    if acro_stats["tp"] + acro_stats["fn"] > 0:
        recall = acro_stats["tp"] / (acro_stats["tp"] + acro_stats["fn"])
        print(f"  Recall:          {recall:.2%}")

    print("\nContainment Guard:")
    print(f"  Correct Blocks:  {contain_stats['correct_block']}")
    print(f"  Correct Allows:  {contain_stats['correct_allow']}")
    print(f"  False Blocks:    {contain_stats['false_block']}")

    if acro_stats["fp"] > 0:
        print(f"\nWARNING: {acro_stats['fp']} false positive(s) in acronym heuristic!")
        sys.exit(1)
    else:
        print("\nNo false positives in acronym heuristic.")


if __name__ == "__main__":
    main()
