"""Birth-time alias generation + shell-noun classifier.

The same LLM call that emits aliases also decides whether the incoming
name is a "shell noun" (Schmid 2000) — a bare abstract noun used as a
propositional container with no topic-specific content (e.g. "method",
"approach", "way", "issue"). Real phenomena / subjects / entities like
"consciousness", "anxiety", "capitalism", "Einstein" are NOT shell nouns
and must not be classified as such.

Shell seeds are never promoted to nodes and are short-circuited out of
the embedding / multiplex pipeline downstream.
"""

from __future__ import annotations

import asyncio

from .big_seed import Fact, Usage
from .llm import LLMRunner

MAX_FACTS = 10
MAX_FACT_CHARS = 300

_EPISTEMOLOGY = """\
ALIAS RULE — a string X is an alias of Y iff X and Y refer to the
IDENTICAL real-world referent and X can replace Y in any factual
sentence about Y without shifting meaning, part of speech, or
referent class.

Include: acronym ↔ expansion, alternate spellings / transliterations,
singular ↔ plural of the same concept (emit both when countable),
capitalization / stylization variants.

Exclude: noun ↔ derived adjective, practitioner ↔ practice, tool ↔
user, derivative product ↔ parent discipline, part ↔ whole, instance
↔ category, organization ↔ member, parent concept ↔ specialization,
pronouns, generic titles.

SHELL RULE (Schmid 2000 "conceptual shell nouns"): a name is SHELL iff
it is a bare abstract noun whose meaning is a propositional container
— i.e. its content is supplied by what it refers to in context, not by
any specific real-world phenomenon it names.

  - Shell test: could the name be replaced by "thing", "piece",
    "matter", "item" without semantic loss? If yes → SHELL.
  - Shell examples (empty containers): method, approach, way, manner,
    kind, sort, type, form, aspect, issue, matter, case, point, fact,
    thing, factor, role, item, respect.
  - NOT shell (real phenomena / subjects / entities): consciousness,
    anxiety, depression, memory, democracy, capitalism, philosophy,
    psychology, belief (as a cognitive phenomenon), justice,
    homeopathy, Einstein, NASA, CRISPR, quantum entanglement.

Multi-token phrases ("string theory", "theory of everything",
"general theory of relativity") are NEVER shell — they carry
topic-specific content via their modifiers. is_shell must be false
for any name that contains more than one content token.

Return an empty alias list when unsure. Prefer silence over a wrong
alias. Set is_shell cautiously — false by default; only true when the
name is clearly an empty container.
"""

_SYSTEM = (
    "You classify knowledge-graph candidate names along two axes: (1) known aliases, "
    "(2) whether the name is a SHELL NOUN that should not become a graph node.\n\n"
    + _EPISTEMOLOGY
    + "\nOutput JSON exactly:\n"
    '{"aliases": ["..."], "is_shell": bool, "shell_reason": "brief justification or empty"}\n'
)

_BATCH_SYSTEM = (
    "You classify multiple knowledge-graph candidate names along two axes: (1) known aliases, "
    "(2) whether each name is a SHELL NOUN that should not become a graph node.\n\n"
    + _EPISTEMOLOGY
    + "\nBATCH MODE: user message lists multiple entries. Respond with an entry per input index, "
    "every entry included.\n\nOutput JSON exactly:\n"
    '{"results": [{"index": N, "aliases": ["..."], "is_shell": bool, "shell_reason": "..."}]}\n'
)


def _build_user(name: str, facts: list[Fact]) -> str:
    sample = facts[:MAX_FACTS]
    fact_block = "\n".join(
        f"- {f.content[:MAX_FACT_CHARS]}" for f in sample if f.content.strip()
    )
    if not fact_block:
        fact_block = "(no facts available)"
    return (
        f'Name: "{name}"\n\n'
        f"Sample facts:\n{fact_block}\n\n"
        'Return JSON: {"aliases": [...], "is_shell": bool, "shell_reason": "..."}. Only the JSON.'
    )


def _build_batch_user(entries: list[tuple[str, list[Fact]]]) -> str:
    parts: list[str] = []
    for idx, (name, facts) in enumerate(entries, start=1):
        sample = facts[:MAX_FACTS]
        fact_lines = "\n".join(
            f"    - {f.content[:MAX_FACT_CHARS]}" for f in sample if f.content.strip()
        ) or "    (no facts)"
        parts.append(f'[{idx}] "{name}"\n{fact_lines}')
    body = "\n\n".join(parts)
    return (
        f"Classify and alias each of the {len(entries)} entries below.\n\n{body}\n\n"
        'Return JSON: {"results": [{"index": N, "aliases": [...], "is_shell": bool, "shell_reason": "..."}]}. '
        "Only the JSON."
    )


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


async def generate_aliases(
    name: str,
    facts: list[Fact],
    *,
    runner: LLMRunner,
) -> tuple[list[str], bool, str, Usage, dict]:
    """Single-seed alias_gen + shell classification.

    Returns (aliases, is_shell, shell_reason, usage, raw_response).
    """
    user = _build_user(name, facts)
    response, usage = await runner.call_json(
        kind="alias_gen",
        system_prompt=_SYSTEM,
        user_content=user,
        max_tokens=500,
    )
    raw = response.get("aliases", []) if isinstance(response, dict) else []
    aliases = _clean_aliases(raw, name)
    is_shell = bool(response.get("is_shell", False)) if isinstance(response, dict) else False
    shell_reason = str(response.get("shell_reason", "")) if isinstance(response, dict) else ""
    return aliases, is_shell, shell_reason, usage, response if isinstance(response, dict) else {}


async def generate_aliases_batch(
    entries: list[tuple[str, list[Fact]]],
    *,
    runner: LLMRunner,
    chunk_size: int = 20,
    concurrency: int = 5,
) -> dict[str, tuple[list[str], bool, str, Usage, dict]]:
    """Batch alias_gen + shell classification. Returns dict
    name -> (aliases, is_shell, shell_reason, usage_share, raw_entry)."""
    sem = asyncio.Semaphore(concurrency)
    chunks: list[list[tuple[str, list[Fact]]]] = [
        entries[i : i + chunk_size] for i in range(0, len(entries), chunk_size)
    ]

    async def run_chunk(chunk: list[tuple[str, list[Fact]]]):
        async with sem:
            user = _build_batch_user(chunk)
            response, usage = await runner.call_json(
                kind="alias_gen_batch",
                system_prompt=_BATCH_SYSTEM,
                user_content=user,
                max_tokens=min(4000, 400 * len(chunk)),
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

        n = max(1, len(chunk))
        share = Usage(
            kind="alias_gen_batch",
            model=usage.model,
            prompt_tokens=usage.prompt_tokens // n,
            completion_tokens=usage.completion_tokens // n,
            cost_usd=usage.cost_usd / n,
            latency_ms=usage.latency_ms,
        )

        out: dict[str, tuple[list[str], bool, str, Usage, dict]] = {}
        for idx, (name, _facts) in enumerate(chunk, start=1):
            entry = by_idx.get(idx) or {}
            raw_aliases = entry.get("aliases", []) if isinstance(entry.get("aliases", []), list) else []
            aliases = _clean_aliases(raw_aliases, name)
            is_shell = bool(entry.get("is_shell", False))
            shell_reason = str(entry.get("shell_reason", ""))
            entry_resp = {
                "index": idx,
                "aliases": aliases,
                "is_shell": is_shell,
                "shell_reason": shell_reason,
            }
            out[name] = (aliases, is_shell, shell_reason, share, entry_resp)
        return out

    merged: dict[str, tuple[list[str], bool, str, Usage, dict]] = {}
    for coro in asyncio.as_completed([run_chunk(c) for c in chunks]):
        result = await coro
        merged.update(result)
    return merged
