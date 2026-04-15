"""Hand-curated bench datasets with ground truth.

Each task is a list of BenchItem with expected outputs. Scoring is done
against these expected values.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class BenchItem:
    name: str
    expected: dict
    notes: str = ""


# ── Alias-gen ──────────────────────────────────────────────────────
# expected: {"must_include": [...], "must_exclude": [...]}
# must_include — aliases the model should emit (partial list, not exhaustive)
# must_exclude — aliases the model should NOT emit
ALIAS_CASES: list[BenchItem] = [
    BenchItem("FBI",
              {"must_include": ["Federal Bureau of Investigation"],
               "must_exclude": ["CIA", "police"]}),
    BenchItem("United Nations",
              {"must_include": ["UN"],
               "must_exclude": ["WHO", "UNICEF"]}),
    BenchItem("homeopathy",
              {"must_include": ["homoeopathy"],
               "must_exclude": ["homeopath", "homeopathic medicine"]}),
    BenchItem("neural network",
              {"must_include": ["neural networks"],
               "must_exclude": ["artificial intelligence", "machine learning"]}),
    BenchItem("iPhone",
              {"must_include": [],
               "must_exclude": ["smartphone", "phone"]}),
    BenchItem("Trends",
              {"must_include": [],
               "must_exclude": ["Trends in Cell Biology", "Trends in Neurosciences"]}),
    BenchItem("JFK",
              {"must_include": ["John F. Kennedy"],
               "must_exclude": ["John F. Kennedy Jr.", "Kennedy"]}),
    BenchItem("method",
              {"must_include": ["methods"],
               "must_exclude": ["approach", "way"]}),
    BenchItem("CRISPR",
              {"must_include": [],
               "must_exclude": ["gene editing", "Cas9"]}),
    BenchItem("Einstein",
              {"must_include": [],
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
    BenchItem("Mercury",
              {"ambiguous": True,
               "must_include_any": ["planet", "element", "god"]}),
    BenchItem("Apollo",
              {"ambiguous": True,
               "must_include_any": ["NASA", "god", "program"]}),
    BenchItem("Java",
              {"ambiguous": True,
               "must_include_any": ["programming", "island"]}),
    BenchItem("Jaguar",
              {"ambiguous": True,
               "must_include_any": ["animal", "car"]}),
    BenchItem("Python",
              {"ambiguous": True,
               "must_include_any": ["programming", "snake"]}),
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
