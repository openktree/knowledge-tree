"""System prompts for the hybrid extractor's two-call LLM pass.

Ported verbatim from ``experiments/big_seed_dedup/alias_gen.py`` — the
shapes and epistemology are what the bench validated. Keep them in sync if
the experiments evolve.
"""

from __future__ import annotations

# ── Shell-noun classifier (context-free, name-only input) ──────────────────

SHELL_BATCH_SYSTEM = """\
SHELL RULE

A noun is SHELL only when it cannot, in any domain anywhere, serve as
a legitimate topic of study, policy, or substantive discourse. Shell
words are pure propositional slots — only meaningful via a complement.

Default: false. When uncertain, false. Multi-token names NEVER shell.

BATCH MODE: user message lists multiple names. Boolean-only per entry,
no reasoning.

Output JSON exactly:
{"results": [{"index": N, "is_shell": bool}, ...]}
"""


def build_shell_batch_user(names: list[str]) -> str:
    parts = "\n".join(f'[{i}] "{n}"' for i, n in enumerate(names, start=1))
    return (
        f"Classify each of the {len(names)} names below as shell or not.\n\n"
        f"{parts}\n\n"
        'Return JSON: {"results": [{"index": N, "is_shell": bool}, ...]}. '
        "Only the JSON."
    )


# ── Alias generator (fact-free, name-only) ─────────────────────────────────

ALIAS_BATCH_SYSTEM = """\
ALIAS RULE

X is an alias of Y iff replacing Y with X, or X with Y, in any
sentence preserves what the sentence refers to. The test is
bidirectional and must hold in every possible sentence, not just
one you have in mind.

Two names are aliases when they are naming variants of the same
referent set — different ways of writing the same name, not
different names that happen to overlap.

Emit an alias only when the relationship is one of:
- acronym and its expansion (same entity)
- alternate spelling or transliteration
- singular and plural of one concept
- official short and long form of the same entity
- capitalization or stylization variant

Ambiguity in the referent set is irrelevant to the test. A name
with multiple senses is an alias of another name with the same
multiple senses. Downstream disambiguation is not your concern.

If substituting one for the other narrows, broadens, or shifts
the referent set in any context, they are not aliases. If you
need surrounding text to decide what a name points to, it is not
a universal alias.

Return [] whenever the relationship is anything other than a pure
naming variant. Empty output is correct when uncertain.

BATCH MODE: user message lists multiple names. Return aliases per
entry. Include every entry, empty list included when none.

Output JSON exactly:
{"results": [{"index": N, "aliases": ["..."]}, ...]}
"""


def build_alias_batch_user(names: list[str]) -> str:
    parts = "\n".join(f'[{i}] "{n}"' for i, n in enumerate(names, start=1))
    return (
        f"List aliases for each of the {len(names)} names below.\n\n"
        f"{parts}\n\n"
        'Return JSON: {"results": [{"index": N, "aliases": [...]}, ...]}. '
        "Only the JSON."
    )
