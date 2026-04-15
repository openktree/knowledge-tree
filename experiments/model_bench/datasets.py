"""Bench datasets. Two sources:
  - curated: hand-picked with ground truth in ALIAS_CASES/SHELL_CASES/DISAMBIG_CASES
  - random: names from fixtures/random_seeds.json, empty ground truth.
    Scoring on random items is emission-count based (no correctness
    judgment until whitelist is curated in a later iteration).

Select via config.yaml `dataset: curated | random | both`.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class BenchItem:
    name: str
    expected: dict
    notes: str = ""
    is_curated: bool = True


# ── Alias-gen ──────────────────────────────────────────────────────
# expected: {"must_include": [...], "must_exclude": [...]}
# must_include — aliases the model should emit (partial list, not exhaustive)
# must_exclude — aliases the model should NOT emit
ALIAS_CASES: list[BenchItem] = [
    # `aliases` is the full whitelist of equally-valid aliases (all have
    # the same epistemological weight — acronym, expansion, spelling
    # variant, etc.). Each model scores = count of emitted ∈ aliases.
    # `must_exclude` fails the item if any are emitted (bad aliases).
    BenchItem("FBI",
              {"aliases": ["Federal Bureau of Investigation", "F.B.I."],
               "must_exclude": ["CIA", "police"]}),
    BenchItem("United Nations",
              {"aliases": ["UN", "United Nations Organization", "U.N.", "UNO"],
               "must_exclude": ["WHO", "UNICEF"]}),
    BenchItem("homeopathy",
              {"aliases": ["homoeopathy"],
               "must_exclude": ["homeopath", "homeopathic medicine"]}),
    BenchItem("neural network",
              {"aliases": ["neural networks", "neural net"],
               "must_exclude": ["artificial intelligence", "machine learning"]}),
    BenchItem("iPhone",
              {"aliases": [],
               "must_exclude": ["smartphone", "phone"]}),
    BenchItem("Trends",
              {"aliases": [],
               "must_exclude": ["Trends in Cell Biology", "Trends in Neurosciences"]}),
    BenchItem("JFK",
              {"aliases": ["John F. Kennedy", "John Fitzgerald Kennedy", "J.F.K."],
               "must_exclude": ["John F. Kennedy Jr.", "Kennedy"]}),
    BenchItem("method",
              {"aliases": ["methods"],
               "must_exclude": ["approach", "way"]}),
    BenchItem("CRISPR",
              {"aliases": ["Clustered Regularly Interspaced Short Palindromic Repeats"],
               "must_exclude": ["gene editing", "Cas9"]}),
    BenchItem("Einstein",
              {"aliases": [],
               "must_exclude": ["Albert Einstein Jr.", "physicist"]}),
]


# ── Shell classify ─────────────────────────────────────────────────
# expected: {"is_shell": bool}
SHELL_CASES: list[BenchItem] = [
    BenchItem("method",         {"is_shell": True}),
    BenchItem("approach",       {"is_shell": True}),
    BenchItem("way",            {"is_shell": True}),
    BenchItem("aspect",         {"is_shell": True}),
    BenchItem("issue",          {"is_shell": True}),
    BenchItem("consciousness",  {"is_shell": False}),
    BenchItem("anxiety",        {"is_shell": False}),
    BenchItem("leadership",     {"is_shell": False}),
    BenchItem("life",           {"is_shell": False}),
    BenchItem("democracy",      {"is_shell": False}),
]


# ── Suggest disambig ──────────────────────────────────────────────
# expected: {"ambiguous": bool, "must_include_any": [list of path-label substrings]}
DISAMBIG_CASES: list[BenchItem] = [
    # must_include_any: substrings at least one emitted path-label must
    # contain (case-insensitive) for the item to score correct.
    # acceptable_extra: additional substrings that are equally valid if
    # a model emits a third/fourth sense beyond the core needles.
    BenchItem("Mercury",
              {"ambiguous": True,
               "must_include_any": ["planet", "element", "god"]}),
    BenchItem("Apollo",
              {"ambiguous": True,
               "must_include_any": ["NASA", "god", "program"],
               "acceptable_extra": ["theatre", "theater"]}),
    BenchItem("Java",
              {"ambiguous": True,
               "must_include_any": ["programming", "island"],
               "acceptable_extra": ["coffee"]}),
    BenchItem("Jaguar",
              {"ambiguous": True,
               "must_include_any": ["animal", "car"]}),
    BenchItem("Python",
              {"ambiguous": True,
               "must_include_any": ["programming", "snake"],
               "acceptable_extra": ["genus", "animal", "mythology"]}),
    BenchItem("Einstein",
              {"ambiguous": False, "must_include_any": []}),
    BenchItem("homeopathy",
              {"ambiguous": False, "must_include_any": []}),
    BenchItem("CRISPR",
              {"ambiguous": False, "must_include_any": []}),
    BenchItem("NATO",
              {"ambiguous": False, "must_include_any": []}),
    BenchItem("photosynthesis",
              {"ambiguous": False, "must_include_any": []}),
]


TASKS = {
    "alias_gen": ALIAS_CASES,
    "shell_classify": SHELL_CASES,
    "suggest_disambig": DISAMBIG_CASES,
}


# ── Permanent 100-seed pool ──
# Snapshotted from local write-db once. Acts as a stable test set.
# Ground truth starts empty per item; expand via iterative curation —
# the user/researcher pair adds aliases / is_shell / disambig paths as
# each round surfaces valid model emissions. Results become deterministic
# because the seed list never changes.

_POOL_PATH = Path(__file__).resolve().parent / "fixtures" / "bench_seeds_100.json"


def _load_pool() -> list[str]:
    if not _POOL_PATH.exists():
        return []
    doc = json.loads(_POOL_PATH.read_text(encoding="utf-8"))
    return [s["name"] for s in doc.get("seeds", []) if isinstance(s, dict) and s.get("name")]


# Per-task ground-truth overrides for pool items. Start empty; curators
# add entries here as iterations surface valid aliases / shell verdicts /
# disambig path labels. Keys are lowercased names for case-insensitive lookup.
POOL_GROUND_TRUTH: dict[str, dict[str, dict]] = {
    # example (post-curation):
    # "large language model agents": {
    #   "alias_gen": {"aliases": ["LLM agents"], "must_exclude": []},
    #   "shell_classify": {"is_shell": False},
    #   "suggest_disambig": {"ambiguous": False, "must_include_any": []},
    # },
}


def _pool_expected(name: str, task: str) -> dict:
    """Ground truth for a pool seed. Empty until curated."""
    entry = POOL_GROUND_TRUTH.get(name.strip().lower(), {})
    gt = entry.get(task)
    if gt is not None:
        return gt
    # Default per-task skeletons so scoring has shape to branch on
    if task == "alias_gen":
        return {"aliases": [], "must_exclude": []}
    if task == "shell_classify":
        return {}
    if task == "suggest_disambig":
        return {}
    return {}


def build_task_items(task: str, dataset: str) -> list[BenchItem]:
    """dataset: "curated" | "pool" | "both"."""
    curated = TASKS.get(task, [])
    if dataset == "curated":
        return list(curated)
    pool_items = [
        BenchItem(name=n, expected=_pool_expected(n, task), notes="bench_seeds_100",
                  is_curated=True)  # now "curated" in the iterative sense — stable, with growing GT
        for n in _load_pool()
    ]
    if dataset == "pool":
        return pool_items
    if dataset == "both":
        return list(curated) + pool_items
    return list(curated)
