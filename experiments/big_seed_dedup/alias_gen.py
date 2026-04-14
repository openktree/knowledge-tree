"""Alias generation + shell-noun classifier — two independent LLM calls.

- generate_aliases_batch: takes (name, facts), emits known aliases.
- classify_shell_batch: takes name only (no facts), emits is_shell.

Separated so each call gets the full attention budget + a focused
system prompt. Shell classifier is deliberately context-free to force
universal judgment and eliminate context-driven false positives.
"""

from __future__ import annotations

import asyncio

from .big_seed import Fact, Usage
from .llm import LLMRunner

MAX_FACTS = 10
MAX_FACT_CHARS = 300


# ── Alias epistemology ─────────────────────────────────────────────

_ALIAS_SYSTEM = """\
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

Output JSON exactly: {"aliases": ["..."]}
"""

_ALIAS_BATCH_SYSTEM = _ALIAS_SYSTEM + """\

BATCH MODE: user message lists multiple names. Return aliases per
entry. Include every entry, empty list included when none.

Output JSON exactly:
{"results": [{"index": N, "aliases": ["..."]}, ...]}
"""


def _build_alias_user(name: str) -> str:
    return (
        f'Name: "{name}"\n\n'
        'Return JSON: {"aliases": [...]}. Only the JSON.'
    )


def _build_alias_batch_user(names: list[str]) -> str:
    parts = "\n".join(f'[{i}] "{n}"' for i, n in enumerate(names, start=1))
    return (
        f"List aliases for each of the {len(names)} names below.\n\n"
        f"{parts}\n\n"
        'Return JSON: {"results": [{"index": N, "aliases": [...]}, ...]}. '
        "Only the JSON."
    )


# ── Shell rule (context-free, name-only input) ─────────────────────

_SHELL_SYSTEM = """\
You classify whether a bare noun is a SHELL NOUN.

SHELL RULE — a noun is SHELL only when it cannot, in any domain
anywhere, serve as a legitimate topic of study, policy, or
substantive discourse.

Universal topic test: ask yourself "in any domain whatsoever —
philosophy, science, economics, sociology, biology, psychology,
self-help, business, politics, everyday life — could a book,
article, or research project meaningfully have this noun as its
subject?" If yes → is_shell=false. If no → is_shell=true.

Shell words are pure propositional slots — they only acquire
meaning through a complement ("the METHOD of X", "the ASPECTS of
Y"). They are grammatical containers, not substantive concepts.

Examples — shell: method, methods, approach, approaches, way,
ways, kind, sort, type, form, aspect, aspects, issue, issues,
matter, case, point, fact, thing, item, respect, regard, role,
roles, lack.

NOT shell — always a legitimate topic somewhere:
consciousness, anxiety, depression, memory, emotion, belief,
democracy, capitalism, socialism, philosophy, psychology,
ethics, justice, freedom, liberty, autonomy, life, leadership,
income, global powers, knowledge, education, technology,
religion, poverty, wealth, equality, inequality, sustainability,
innovation, creativity, resilience, motivation, productivity,
health, entrepreneurship.

Default: is_shell=FALSE. Flip true only when confident. When
uncertain, is_shell=false. Multi-token names are NEVER shell.

Output the boolean only, no explanation. JSON exactly:
{"is_shell": bool}
"""

_SHELL_BATCH_SYSTEM = _SHELL_SYSTEM + """\

BATCH MODE: user message lists multiple names. Classify each one.
Include every entry. Boolean-only output, no reasoning.

Output JSON exactly:
{"results": [{"index": N, "is_shell": bool}, ...]}
"""


def _build_shell_user(name: str) -> str:
    return (
        f'Name: "{name}"\n\n'
        'Return JSON: {"is_shell": bool}. Only the JSON.'
    )


def _build_shell_batch_user(names: list[str]) -> str:
    parts = "\n".join(f'[{i}] "{n}"' for i, n in enumerate(names, start=1))
    return (
        f"Classify each of the {len(names)} names below as shell or not.\n\n"
        f"{parts}\n\n"
        'Return JSON: {"results": [{"index": N, "is_shell": bool}, ...]}. '
        "Only the JSON."
    )


# ── Helpers ────────────────────────────────────────────────────────

def _clean_aliases(raw: list, canonical: str) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    low_canon = canonical.strip().lower()
    for item in raw or []:
        if not isinstance(item, str):
            continue
        c = item.strip()
        if not c:
            continue
        k = c.lower()
        if k == low_canon or k in seen:
            continue
        seen.add(k)
        out.append(c)
    return out


def _share_usage(total: Usage, n: int, kind: str) -> Usage:
    n = max(1, n)
    return Usage(
        kind=kind,
        model=total.model,
        prompt_tokens=total.prompt_tokens // n,
        completion_tokens=total.completion_tokens // n,
        cost_usd=total.cost_usd / n,
        latency_ms=total.latency_ms,
    )


# ── Single-seed entry points (kept for parity / tests) ─────────────

async def generate_aliases(
    name: str,
    facts: list[Fact] | None = None,  # kept for signature parity, ignored
    *,
    runner: LLMRunner,
) -> tuple[list[str], Usage, dict]:
    """Fact-free alias generation. `facts` accepted but ignored — the
    LLM sees the bare name only, producing universal aliases based on
    world knowledge, not on one specific context."""
    response, usage = await runner.call_json(
        kind="alias_gen",
        system_prompt=_ALIAS_SYSTEM,
        user_content=_build_alias_user(name),
        max_tokens=200,
    )
    raw = response.get("aliases", []) if isinstance(response, dict) else []
    return _clean_aliases(raw, name), usage, response if isinstance(response, dict) else {}


async def classify_shell(
    name: str,
    *,
    runner: LLMRunner,
) -> tuple[bool, str, Usage, dict]:
    """Returns (is_shell, reason, usage, response). reason is always ''
    now — the prompt no longer asks for one (saves ~34% cost)."""
    response, usage = await runner.call_json(
        kind="shell_classify",
        system_prompt=_SHELL_SYSTEM,
        user_content=_build_shell_user(name),
        max_tokens=30,
    )
    is_shell = bool(response.get("is_shell", False)) if isinstance(response, dict) else False
    return is_shell, "", usage, response if isinstance(response, dict) else {}


# ── Batch entry points ─────────────────────────────────────────────

async def generate_aliases_batch(
    names: list[str],
    *,
    runner: LLMRunner,
    chunk_size: int = 40,
    concurrency: int = 5,
) -> dict[str, tuple[list[str], Usage, dict]]:
    """Fact-free alias batch. Returns dict name -> (aliases, usage_share, raw_entry).

    Input is name-only: LLM emits aliases based on world knowledge, not
    fact context, so coreferences and context-specific rescues cannot
    leak into the alias graph.
    """
    sem = asyncio.Semaphore(concurrency)
    chunks = [names[i : i + chunk_size] for i in range(0, len(names), chunk_size)]

    async def run_chunk(chunk: list[str]):
        async with sem:
            response, usage = await runner.call_json(
                kind="alias_gen_batch",
                system_prompt=_ALIAS_BATCH_SYSTEM,
                user_content=_build_alias_batch_user(chunk),
                max_tokens=min(2500, 80 * len(chunk)),
            )
        by_idx: dict[int, list] = {}
        for r in (response.get("results", []) if isinstance(response, dict) else []):
            if not isinstance(r, dict):
                continue
            try:
                idx = int(r.get("index"))
            except (TypeError, ValueError):
                continue
            by_idx[idx] = r.get("aliases", []) if isinstance(r.get("aliases", []), list) else []

        share = _share_usage(usage, len(chunk), "alias_gen_batch")
        out: dict[str, tuple[list[str], Usage, dict]] = {}
        for idx, name in enumerate(chunk, start=1):
            aliases = _clean_aliases(by_idx.get(idx, []), name)
            out[name] = (aliases, share, {"index": idx, "aliases": aliases})
        return out

    merged: dict[str, tuple[list[str], Usage, dict]] = {}
    for coro in asyncio.as_completed([run_chunk(c) for c in chunks]):
        merged.update(await coro)
    return merged


async def classify_shell_batch(
    names: list[str],
    *,
    runner: LLMRunner,
    chunk_size: int = 40,   # smaller prompts → larger chunks OK
    concurrency: int = 5,
) -> dict[str, tuple[bool, str, Usage, dict]]:
    """Shell-only batch, name-only input (no facts). Returns
    dict name -> (is_shell, reason, usage_share, raw_entry)."""
    sem = asyncio.Semaphore(concurrency)
    chunks = [names[i : i + chunk_size] for i in range(0, len(names), chunk_size)]

    async def run_chunk(chunk: list[str]):
        async with sem:
            response, usage = await runner.call_json(
                kind="shell_classify_batch",
                system_prompt=_SHELL_BATCH_SYSTEM,
                user_content=_build_shell_batch_user(chunk),
                max_tokens=min(1200, 40 * len(chunk)),
            )
        by_idx: dict[int, dict] = {}
        for r in (response.get("results", []) if isinstance(response, dict) else []):
            if not isinstance(r, dict):
                continue
            try:
                idx = int(r.get("index"))
            except (TypeError, ValueError):
                continue
            by_idx[idx] = r

        share = _share_usage(usage, len(chunk), "shell_classify_batch")
        out: dict[str, tuple[bool, str, Usage, dict]] = {}
        for idx, name in enumerate(chunk, start=1):
            entry = by_idx.get(idx) or {}
            is_shell = bool(entry.get("is_shell", False))
            out[name] = (is_shell, "", share, {"index": idx, "is_shell": is_shell})
        return out

    merged: dict[str, tuple[bool, str, Usage, dict]] = {}
    for coro in asyncio.as_completed([run_chunk(c) for c in chunks]):
        merged.update(await coro)
    return merged
