"""Ablation study: generic vs specific GLiNER2 labels per KT node type.

Runs multiple label configurations and compares recall per category.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

# Reuse expected entities from main script
from test_gliner2 import EXPECTED_ENTITIES, KT_TYPE_MAP

SAMPLE_PATH = Path(__file__).parent / "sample_facts.txt"

# ── Label strategy variants ──────────────────────────────────────────

STRATEGIES: dict[str, dict[str, list[str]]] = {
    # Strategy 1: Generic only (minimal labels)
    "generic": {
        "person": ["person"],
        "organization": ["organization"],
        "concept": ["concept"],
        "event": ["event"],
        "location": ["location"],
    },
    # Strategy 2: Specific only (role/domain labels, no generic)
    "specific": {
        "person": ["scientist", "researcher", "author", "engineer", "politician"],
        "organization": ["company", "government agency", "research institution", "university", "international organization", "energy company"],
        "concept": ["technology", "scientific theory", "technique", "field of study", "energy source", "energy technology", "physical phenomenon", "scientific principle", "publication", "textbook", "report", "regulation", "policy", "infrastructure", "system"],
        "event": ["historical event", "scientific discovery", "award", "milestone"],
        "location": ["country", "city", "region", "geographic feature"],
    },
    # Strategy 3: Both generic + specific
    "both": {
        "person": ["person", "scientist", "researcher", "author", "engineer", "politician"],
        "organization": ["organization", "company", "government agency", "research institution", "university", "international organization", "energy company"],
        "concept": ["concept", "technology", "scientific theory", "technique", "field of study", "energy source", "energy technology", "physical phenomenon", "scientific principle", "publication", "textbook", "report", "regulation", "policy", "infrastructure", "system"],
        "event": ["event", "historical event", "scientific discovery", "award", "milestone"],
        "location": ["location", "country", "city", "region", "geographic feature"],
    },
    # Strategy 4: Generic + light specifics (fewer labels, less noise)
    "hybrid_light": {
        "person": ["person", "scientist", "author"],
        "organization": ["organization", "company", "government agency"],
        "concept": ["concept", "technology", "scientific theory", "energy source", "publication"],
        "event": ["event", "historical event"],
        "location": ["location", "country", "city", "region"],
    },
}

# Build KT_TYPE_MAP per category for each strategy
def _build_type_map(strategy: dict[str, list[str]]) -> dict[str, tuple[str, str | None]]:
    """Build a label -> (kt_type, subtype) mapping for a strategy."""
    tm: dict[str, tuple[str, str | None]] = {}
    for cat, labels in strategy.items():
        for label in labels:
            if label in KT_TYPE_MAP:
                tm[label] = KT_TYPE_MAP[label]
            else:
                # Infer from category
                if cat == "person":
                    tm[label] = ("entity", "person")
                elif cat == "organization":
                    tm[label] = ("entity", "organization")
                elif cat == "concept":
                    tm[label] = ("concept", None)
                elif cat == "event":
                    tm[label] = ("event", None)
                elif cat == "location":
                    tm[label] = ("location", None)
    return tm


def run_extraction(text: str, labels: list[str], type_map: dict, extractor, threshold: float) -> list[dict]:
    """Run extraction with given labels and return KT-style nodes."""
    raw = extractor.extract_entities(text, labels, threshold=threshold)
    entities = raw.get("entities", {})
    seen: dict[str, dict] = {}

    for label, names in entities.items():
        node_type, entity_subtype = type_map.get(label, ("concept", None))
        for name in names:
            key = name.strip().lower()
            if key in seen:
                existing = seen[key]
                if entity_subtype and not existing.get("entity_subtype"):
                    existing["entity_subtype"] = entity_subtype
                    existing["node_type"] = node_type
            else:
                node: dict = {
                    "name": name.strip(),
                    "node_type": node_type,
                }
                if entity_subtype:
                    node["entity_subtype"] = entity_subtype
                seen[key] = node

    return list(seen.values())


def score_category(extracted: list[dict], expected: list[dict], category: str) -> dict:
    """Score recall for a specific KT node_type category."""
    exp_in_cat = [e for e in expected if e["node_type"] == category
                  or (category == "entity" and e["node_type"] == "entity")]
    ext_in_cat = [e for e in extracted if e["node_type"] == category]

    if not exp_in_cat:
        return {"expected": 0, "found": 0, "recall": 0.0, "extracted_total": len(ext_in_cat), "missed": [], "false_positives": len(ext_in_cat)}

    ext_names = {n["name"].strip().lower() for n in ext_in_cat}
    found = 0
    missed = []
    for exp in exp_in_cat:
        key = exp["name"].strip().lower()
        match = key in ext_names or any(key in en or en in key for en in ext_names)
        if match:
            found += 1
        else:
            missed.append(exp["name"])

    recall = found / len(exp_in_cat) * 100 if exp_in_cat else 0
    return {
        "expected": len(exp_in_cat),
        "found": found,
        "recall": recall,
        "extracted_total": len(ext_in_cat),
        "missed": missed,
        "false_positives": len(ext_in_cat) - found,
    }


def main() -> None:
    from gliner2 import GLiNER2

    text = SAMPLE_PATH.read_text(encoding="utf-8").strip()
    threshold = 0.25

    print("Loading model ...")
    extractor = GLiNER2.from_pretrained("fastino/gliner2-base-v1")
    print()

    categories = ["entity", "concept", "event", "location"]
    all_results: dict[str, dict] = {}

    for strat_name, strategy in STRATEGIES.items():
        labels = []
        for cat_labels in strategy.values():
            labels.extend(cat_labels)
        type_map = _build_type_map(strategy)

        t0 = time.time()
        extracted = run_extraction(text, labels, type_map, extractor, threshold)
        elapsed = time.time() - t0

        cat_scores = {}
        total_found = 0
        total_expected = 0
        for cat in categories:
            sc = score_category(extracted, EXPECTED_ENTITIES, cat)
            cat_scores[cat] = sc
            total_found += sc["found"]
            total_expected += sc["expected"]

        overall_recall = total_found / total_expected * 100 if total_expected else 0
        all_results[strat_name] = {
            "labels_count": len(labels),
            "time": elapsed,
            "total_extracted": len(extracted),
            "overall_recall": overall_recall,
            "categories": cat_scores,
        }

    # ── Print comparison table ──────────────────────────────────────
    print("=" * 90)
    print("ABLATION RESULTS — Per-Category Recall (%) by Strategy")
    print("=" * 90)

    header = f"{'Strategy':<16} {'Labels':>6} {'Time':>6} | {'Entity':>12} {'Concept':>12} {'Event':>12} {'Location':>12} | {'Overall':>8}"
    print(header)
    print("-" * 90)

    for strat_name, res in all_results.items():
        cats = res["categories"]
        e = cats["entity"]
        c = cats["concept"]
        ev = cats["event"]
        loc = cats["location"]

        e_str = f"{e['found']}/{e['expected']} {e['recall']:4.0f}%"
        c_str = f"{c['found']}/{c['expected']} {c['recall']:4.0f}%"
        ev_str = f"{ev['found']}/{ev['expected']} {ev['recall']:4.0f}%"
        loc_str = f"{loc['found']}/{loc['expected']} {loc['recall']:4.0f}%"

        print(f"{strat_name:<16} {res['labels_count']:>6} {res['time']:>5.2f}s | {e_str:>12} {c_str:>12} {ev_str:>12} {loc_str:>12} | {res['overall_recall']:>6.1f}%")

    # ── Detailed missed entities per strategy per category ──────────
    print()
    print("=" * 90)
    print("DETAILED MISSES PER STRATEGY")
    print("=" * 90)

    for strat_name, res in all_results.items():
        print(f"\n── {strat_name} (overall {res['overall_recall']:.1f}%, {res['total_extracted']} extracted) ──")
        for cat in categories:
            sc = res["categories"][cat]
            if sc["missed"]:
                print(f"  {cat}: missed {sc['missed']}")
            fp = sc["false_positives"]
            if fp > 0:
                print(f"  {cat}: +{fp} extra (not in expected)")

    # ── Best strategy per category ──────────────────────────────────
    print()
    print("=" * 90)
    print("BEST STRATEGY PER CATEGORY")
    print("=" * 90)
    for cat in categories:
        best_strat = None
        best_recall = -1.0
        for strat_name, res in all_results.items():
            r = res["categories"][cat]["recall"]
            fp = res["categories"][cat]["false_positives"]
            # Prefer higher recall, break ties by fewer false positives
            if r > best_recall or (r == best_recall and best_strat and fp < all_results[best_strat]["categories"][cat]["false_positives"]):
                best_recall = r
                best_strat = strat_name
        sc = all_results[best_strat]["categories"][cat]
        print(f"  {cat:<10}: {best_strat:<16} — {sc['found']}/{sc['expected']} ({best_recall:.0f}%) recall, {sc['false_positives']} false positives")
        labels_used = STRATEGIES[best_strat].get(
            "person" if cat == "entity" else cat,
            STRATEGIES[best_strat].get(cat, [])
        )
        if cat == "entity":
            org_labels = STRATEGIES[best_strat].get("organization", [])
            labels_used = STRATEGIES[best_strat].get("person", []) + org_labels
        print(f"             labels: {labels_used}")


if __name__ == "__main__":
    main()
