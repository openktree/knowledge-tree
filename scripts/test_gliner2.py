"""GLiNER2 entity extraction test — mirrors Knowledge Tree's node types.

Usage:
    pip install gliner2
    python scripts/test_gliner2.py                          # uses sample_facts.txt
    python scripts/test_gliner2.py path/to/your/file.txt    # custom input
    python scripts/test_gliner2.py --model fastino/gliner2-large-v1  # large model
    python scripts/test_gliner2.py --compare                # compare against expected KT entities

Extracts the same entity categories as KT's _NODE_EXTRACTION_SYSTEM prompt:
  - person          (entity subtype)
  - organization    (entity subtype)
  - concept         (abstract topic, theory, technique, technology, publication)
  - event           (dated occurrence, discovery, award, ruling)
  - location        (physical place, country, city, region)

Ablation results (base model, threshold=0.25, 27 real KT facts):
  ┌────────────┬───────────────────────────────────────────────────────┬──────────┐
  │ Category   │ Best strategy                                        │ Recall   │
  ├────────────┼───────────────────────────────────────────────────────┼──────────┤
  │ Entity     │ Generic: "person", "organization"                    │ 17/18 94%│
  │ Concept    │ Hybrid:  generic + domain-specific                   │ 14/20 70%│
  │ Event      │ Targeted: "report publication", "scientific discovery"│  2/2 100%│
  │ Location   │ Both:    generic + geo-specific                      │ 10/10 100│
  ├────────────┼───────────────────────────────────────────────────────┼──────────┤
  │ Overall    │ Mixed optimal (14 labels)                            │ 43/50 86%│
  └────────────┴───────────────────────────────────────────────────────┴──────────┘

Key findings:
  - base model (205M) >> large model (340M) for person extraction (18 vs 7)
  - Generic "person"/"organization" beat role-specific labels (scientist, etc.)
  - Concepts need domain hints ("technology", "energy source") to reach 70%
  - Generic "event" is useless — captures Feynman quotes, misses actual events
  - "report publication" precisely catches report releases as events
  - Concept recall ceiling ~70% — GLiNER2 can't infer implicit concepts
  - Persistent misses: acronyms (PJM), compound terms (DC microgrids),
    implicit concepts (firm power, nuclear energy when not named as such)
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

# ── Optimal mixed label strategy ─────────────────────────────────────
# Determined by ablation testing across 4 strategies (generic, specific,
# both, hybrid_light) + targeted event label experiments.
#
# Entity:   generic labels win (100% recall base, 94% optimized)
# Concept:  hybrid — generic "concept" + domain-specific boosts
# Event:    targeted phrases ("report publication", "scientific discovery")
# Location: generic + geo subtypes (100% recall, 0 false positives)

ENTITY_LABELS = [
    # ── entity: generic wins ──
    "person",
    "organization",
    # ── concept: hybrid (generic + domain hints) ──
    "concept",
    "technology",
    "scientific theory",
    "energy source",
    "publication",
    # ── event: targeted phrases ──
    "report publication",
    "scientific discovery",
    # ── location: generic + geo subtypes ──
    "location",
    "country",
    "city",
    "region",
    "geographic feature",
]

# Map every label → (kt_node_type, entity_subtype | None)
KT_TYPE_MAP: dict[str, tuple[str, str | None]] = {
    # entity
    "person": ("entity", "person"),
    "organization": ("entity", "organization"),
    # concept
    "concept": ("concept", None),
    "technology": ("concept", None),
    "scientific theory": ("concept", None),
    "energy source": ("concept", None),
    "publication": ("concept", None),
    # event
    "report publication": ("event", None),
    "scientific discovery": ("event", None),
    # location
    "location": ("location", None),
    "country": ("location", None),
    "city": ("location", None),
    "region": ("location", None),
    "geographic feature": ("location", None),
}

# ── Expected KT entities for the default sample_facts.txt ─────────
# What our LLM extraction prompt would produce from these facts.
# Used with --compare to measure GLiNER2 recall.

EXPECTED_ENTITIES: list[dict] = [
    # persons
    {"name": "James Clerk Maxwell", "node_type": "entity", "entity_subtype": "person"},
    {"name": "Michael Faraday", "node_type": "entity", "entity_subtype": "person"},
    {"name": "Richard Feynman", "node_type": "entity", "entity_subtype": "person"},
    {"name": "John D. Jackson", "node_type": "entity", "entity_subtype": "person"},
    {"name": "Charles Fritts", "node_type": "entity", "entity_subtype": "person"},
    {"name": "John D. Kinsman", "node_type": "entity", "entity_subtype": "person"},
    # organizations
    {"name": "Royal Institution of Great Britain", "node_type": "entity", "entity_subtype": "organization"},
    {"name": "Repsol", "node_type": "entity", "entity_subtype": "organization"},
    {"name": "ITC Holdings", "node_type": "entity", "entity_subtype": "organization"},
    {"name": "Arizona Public Service", "node_type": "entity", "entity_subtype": "organization"},
    {"name": "U.S. Energy Information Administration", "node_type": "entity", "entity_subtype": "organization"},
    {"name": "International Energy Agency", "node_type": "entity", "entity_subtype": "organization"},
    {"name": "World Bank", "node_type": "entity", "entity_subtype": "organization"},
    {"name": "Environmental Protection Agency", "node_type": "entity", "entity_subtype": "organization"},
    {"name": "Department of Energy", "node_type": "entity", "entity_subtype": "organization"},
    {"name": "Federal Energy Regulatory Commission", "node_type": "entity", "entity_subtype": "organization"},
    {"name": "Trump Administration", "node_type": "entity", "entity_subtype": "organization"},
    {"name": "PJM", "node_type": "entity", "entity_subtype": "organization"},
    # concepts
    {"name": "electromagnetic field theory", "node_type": "concept"},
    {"name": "Maxwell's equations", "node_type": "concept"},
    {"name": "displacement current", "node_type": "concept"},
    {"name": "electromagnetism", "node_type": "concept"},
    {"name": "renewable energy", "node_type": "concept"},
    {"name": "wind energy", "node_type": "concept"},
    {"name": "solar energy", "node_type": "concept"},
    {"name": "energy storage systems", "node_type": "concept"},
    {"name": "smart grids", "node_type": "concept"},
    {"name": "pumped-storage hydropower", "node_type": "concept"},
    {"name": "DC microgrids", "node_type": "concept"},
    {"name": "reconductoring", "node_type": "concept"},
    {"name": "electric vehicle infrastructure", "node_type": "concept"},
    {"name": "wireless power transmission", "node_type": "concept"},
    {"name": "battery storage", "node_type": "concept"},
    {"name": "hydrogen-based energy systems", "node_type": "concept"},
    {"name": "photovoltaic plants", "node_type": "concept"},
    {"name": "nuclear energy", "node_type": "concept"},
    {"name": "fossil fuels", "node_type": "concept"},
    {"name": "firm power", "node_type": "concept"},
    # events
    {"name": "Global Energy Trends 2023 report", "node_type": "event"},
    {"name": "Energy Progress Report 2023", "node_type": "event"},
    # locations
    {"name": "Spain", "node_type": "location"},
    {"name": "Chile", "node_type": "location"},
    {"name": "Jerez de la Frontera", "node_type": "location"},
    {"name": "Andalusia", "node_type": "location"},
    {"name": "United States", "node_type": "location"},
    {"name": "Belgium", "node_type": "location"},
    {"name": "Netherlands", "node_type": "location"},
    {"name": "Ontario", "node_type": "location"},
    {"name": "North America", "node_type": "location"},
    {"name": "Scotland", "node_type": "location"},
]


def load_text(path: str) -> str:
    return Path(path).read_text(encoding="utf-8").strip()


def extract(text: str, model_id: str, threshold: float) -> list[dict]:
    from gliner2 import GLiNER2

    print(f"Loading model: {model_id} ...")
    t0 = time.time()
    extractor = GLiNER2.from_pretrained(model_id)
    print(f"Model loaded in {time.time() - t0:.1f}s\n")

    print(f"Extracting with {len(ENTITY_LABELS)} labels, threshold={threshold} ...")
    t0 = time.time()
    raw = extractor.extract_entities(text, ENTITY_LABELS, threshold=threshold)
    elapsed = time.time() - t0
    print(f"Extraction done in {elapsed:.2f}s\n")

    # Normalize into KT-style dicts
    entities = raw.get("entities", {})
    seen: dict[str, dict] = {}  # lowercase name -> node dict

    for label, names in entities.items():
        node_type, entity_subtype = KT_TYPE_MAP.get(label, ("concept", None))
        for name in names:
            key = name.strip().lower()
            if key in seen:
                existing = seen[key]
                # Upgrade to more specific type if needed
                if entity_subtype and not existing.get("entity_subtype"):
                    existing["entity_subtype"] = entity_subtype
                    existing["node_type"] = node_type
                if existing.get("gliner_labels"):
                    existing["gliner_labels"].add(label)
            else:
                node: dict = {
                    "name": name.strip(),
                    "node_type": node_type,
                    "gliner_labels": {label},
                }
                if entity_subtype:
                    node["entity_subtype"] = entity_subtype
                seen[key] = node

    # Convert sets to lists for JSON serialization
    results = []
    for node in seen.values():
        node["gliner_labels"] = sorted(node.pop("gliner_labels"))
        results.append(node)

    return results


def print_results(nodes: list[dict]) -> None:
    by_type: dict[str, list[dict]] = {}
    for n in nodes:
        by_type.setdefault(n["node_type"], []).append(n)

    type_order = ["entity", "concept", "event", "location"]
    for t in type_order:
        group = by_type.get(t, [])
        if not group:
            continue
        print(f"── {t.upper()} ({len(group)}) ──")
        for n in sorted(group, key=lambda x: x["name"]):
            subtype = f" [{n['entity_subtype']}]" if n.get("entity_subtype") else ""
            labels = ", ".join(n["gliner_labels"])
            print(f"  {n['name']}{subtype}  (labels: {labels})")
        print()

    print(f"Total: {len(nodes)} nodes extracted")


def compare_results(extracted: list[dict], expected: list[dict]) -> None:
    """Compare GLiNER2 output against expected KT entities."""
    extracted_by_name = {n["name"].strip().lower(): n for n in extracted}

    found = 0
    missed: list[dict] = []
    type_mismatches: list[tuple[dict, dict]] = []

    for exp in expected:
        key = exp["name"].strip().lower()
        match = None
        if key in extracted_by_name:
            match = extracted_by_name[key]
        else:
            for ek, ev in extracted_by_name.items():
                if key in ek or ek in key:
                    match = ev
                    break

        if match:
            found += 1
            if match["node_type"] != exp["node_type"]:
                type_mismatches.append((exp, match))
        else:
            missed.append(exp)

    expected_names = set()
    for exp in expected:
        expected_names.add(exp["name"].strip().lower())

    extras: list[dict] = []
    for ext in extracted:
        key = ext["name"].strip().lower()
        is_expected = key in expected_names
        if not is_expected:
            is_expected = any(key in en or en in key for en in expected_names)
        if not is_expected:
            extras.append(ext)

    total_expected = len(expected)
    recall = found / total_expected * 100 if total_expected else 0

    print("=" * 60)
    print("COMPARISON REPORT")
    print("=" * 60)
    print(f"\nRecall: {found}/{total_expected} ({recall:.1f}%)")
    print(f"Extra entities found: {len(extras)}")
    print(f"Type mismatches: {len(type_mismatches)}")

    if missed:
        print(f"\n── MISSED ({len(missed)}) ──")
        for m in missed:
            sub = f" [{m.get('entity_subtype', '')}]" if m.get("entity_subtype") else ""
            print(f"  {m['name']} ({m['node_type']}{sub})")

    if type_mismatches:
        print(f"\n── TYPE MISMATCHES ({len(type_mismatches)}) ──")
        for exp, got in type_mismatches:
            exp_sub = f"/{exp.get('entity_subtype', '')}" if exp.get("entity_subtype") else ""
            got_sub = f"/{got.get('entity_subtype', '')}" if got.get("entity_subtype") else ""
            print(f"  {exp['name']}: expected {exp['node_type']}{exp_sub} → got {got['node_type']}{got_sub}")
            print(f"    gliner labels: {', '.join(got.get('gliner_labels', []))}")

    if extras:
        print(f"\n── EXTRA (not in expected list) ({len(extras)}) ──")
        for e in sorted(extras, key=lambda x: x["node_type"]):
            sub = f" [{e.get('entity_subtype', '')}]" if e.get("entity_subtype") else ""
            labels = ", ".join(e.get("gliner_labels", []))
            print(f"  {e['name']} ({e['node_type']}{sub})  labels: {labels}")

    print()


def main() -> None:
    parser = argparse.ArgumentParser(description="GLiNER2 entity extraction (KT-aligned)")
    parser.add_argument("input", nargs="?", default=None, help="Path to text file (default: scripts/sample_facts.txt)")
    parser.add_argument("--model", default="fastino/gliner2-base-v1", help="GLiNER2 model ID")
    parser.add_argument("--threshold", type=float, default=0.25, help="Confidence threshold (default: 0.25)")
    parser.add_argument("--json", action="store_true", help="Output raw JSON instead of formatted table")
    parser.add_argument(
        "--compare", action="store_true", help="Compare against expected KT entities for sample_facts.txt"
    )
    args = parser.parse_args()

    if args.input:
        input_path = args.input
    else:
        script_dir = Path(__file__).parent
        input_path = str(script_dir / "sample_facts.txt")

    text = load_text(input_path)
    print(f"Input: {input_path} ({len(text)} chars)\n")

    nodes = extract(text, args.model, args.threshold)

    if args.json:
        print(json.dumps(nodes, indent=2))
    else:
        print_results(nodes)

    if args.compare:
        compare_results(nodes, EXPECTED_ENTITIES)


if __name__ == "__main__":
    main()
