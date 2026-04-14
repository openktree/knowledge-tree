"""Birth-time alias generation — single + batched."""

from __future__ import annotations

import asyncio

from .big_seed import Fact, Usage
from .llm import LLMRunner

MAX_FACTS = 10
MAX_FACT_CHARS = 300

_SYSTEM = """\
You extract aliases for entries in a knowledge graph. Emit ONLY aliases
that are epistemologically equivalent to the given name.

A string X is an alias of Y iff:
  - X and Y refer to the IDENTICAL real-world referent, AND
  - X can replace Y in any factual sentence about Y without shifting
    meaning, part of speech, or referent class.

Epistemological test: substitute X for Y in a concrete sentence about Y.
If the sentence now refers to something different (different thing,
different part of speech, different scope, different role) — it is NOT
an alias. Reject it.

Include: acronym ↔ expansion, alternate spellings / transliterations,
singular ↔ plural of the same concept (emit both when countable),
capitalization / stylization variants, common short form for the same
individual.

Exclude: noun ↔ derived adjective, practitioner ↔ practice, tool ↔ user,
derivative product ↔ parent discipline, part ↔ whole, instance ↔
category, organization ↔ member, parent concept ↔ specialization,
pronouns, generic titles.

Return an empty list when unsure. Prefer silence over a wrong alias.

Output JSON exactly:
{"aliases": ["alias1", "alias2", ...]}
"""

_BATCH_SYSTEM = _SYSTEM + """\

BATCH MODE: the user message lists multiple entries. Return aliases for
each entry, keyed by index. Output JSON exactly:
{"results": [{"index": 1, "aliases": [...]}, {"index": 2, "aliases": [...]}, ...]}
Include every entry even if its aliases list is empty.
"""


def _build_user(name: str, facts: list[Fact]) -> str:
    sample = facts[:MAX_FACTS]
    fact_block = "\n".join(
        f"- {f.content[:MAX_FACT_CHARS]}" for f in sample if f.content.strip()
    )
    if not fact_block:
        fact_block = "(no facts available)"
    return (
        f'Entity name: "{name}"\n\n'
        f"Sample facts:\n{fact_block}\n\n"
        'Return JSON: {"aliases": [...]}. Only the JSON.'
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
        f"Emit aliases for each of the {len(entries)} entries below.\n\n{body}\n\n"
        'Return JSON: {"results": [{"index": N, "aliases": [...]}, ...]}. Only the JSON.'
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
) -> tuple[list[str], Usage, dict]:
    """Single-seed alias generation."""
    user = _build_user(name, facts)
    response, usage = await runner.call_json(
        kind="alias_gen",
        system_prompt=_SYSTEM,
        user_content=user,
        max_tokens=400,
    )
    raw = response.get("aliases", []) if isinstance(response, dict) else []
    return _clean_aliases(raw, name), usage, response if isinstance(response, dict) else {}


async def generate_aliases_batch(
    entries: list[tuple[str, list[Fact]]],
    *,
    runner: LLMRunner,
    chunk_size: int = 20,
    concurrency: int = 5,
) -> dict[str, tuple[list[str], Usage, dict]]:
    """Batch alias gen. Splits into chunks of `chunk_size`, runs up to
    `concurrency` chunks in parallel. Returns dict name -> (aliases, usage_share, raw_entry).

    Each entry's usage is the chunk's total divided by chunk size (approx).
    Names must be unique within the input list.
    """
    sem = asyncio.Semaphore(concurrency)
    chunks: list[list[tuple[str, list[Fact]]]] = [
        entries[i : i + chunk_size] for i in range(0, len(entries), chunk_size)
    ]

    async def run_chunk(chunk: list[tuple[str, list[Fact]]]) -> dict[str, tuple[list[str], Usage, dict]]:
        async with sem:
            user = _build_batch_user(chunk)
            response, usage = await runner.call_json(
                kind="alias_gen_batch",
                system_prompt=_BATCH_SYSTEM,
                user_content=user,
                max_tokens=min(4000, 300 * len(chunk)),
            )
        results_raw = response.get("results", []) if isinstance(response, dict) else []
        by_idx: dict[int, list[str]] = {}
        for r in results_raw:
            if not isinstance(r, dict):
                continue
            try:
                idx = int(r.get("index"))
            except (TypeError, ValueError):
                continue
            by_idx[idx] = r.get("aliases", []) if isinstance(r.get("aliases", []), list) else []

        n = max(1, len(chunk))
        share = Usage(
            kind="alias_gen_batch",
            model=usage.model,
            prompt_tokens=usage.prompt_tokens // n,
            completion_tokens=usage.completion_tokens // n,
            cost_usd=usage.cost_usd / n,
            latency_ms=usage.latency_ms,
        )

        out: dict[str, tuple[list[str], Usage, dict]] = {}
        for idx, (name, _facts) in enumerate(chunk, start=1):
            raw = by_idx.get(idx, [])
            aliases = _clean_aliases(raw, name)
            entry_resp = {"index": idx, "aliases": aliases}
            out[name] = (aliases, share, entry_resp)
        return out

    merged: dict[str, tuple[list[str], Usage, dict]] = {}
    for coro in asyncio.as_completed([run_chunk(c) for c in chunks]):
        result = await coro
        merged.update(result)
    return merged
