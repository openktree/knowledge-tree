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
