"""Birth-time alias generation: LLM produces known aliases from name + facts."""

from __future__ import annotations

from .big_seed import Fact, Usage
from .llm import LLMRunner

MAX_FACTS = 10
MAX_FACT_CHARS = 300

_SYSTEM = """\
You are an alias extractor for a knowledge graph. Given an entity or concept name
plus a small sample of facts mentioning it, emit known aliases, acronyms,
abbreviations, alternate spellings, and common surface variants that refer to
the SAME real-world thing.

Rules:
- Only aliases that are actually used in the real world for this specific entity.
- Include acronym expansions, short/long forms, and stylized spellings.
- Do NOT include related-but-different entities, categories, or parent concepts.
- Do NOT include pronouns ("he", "she", "it") or generic titles ("the president").
- Return an empty list if unsure rather than guessing.

Output JSON exactly:
{"aliases": ["alias1", "alias2", ...]}
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


async def generate_aliases(
    name: str,
    facts: list[Fact],
    *,
    runner: LLMRunner,
) -> tuple[list[str], Usage, dict]:
    """Generate aliases for a newly created path/big-seed.

    Returns (aliases, usage, raw_response). raw_response preserved for the
    report so the reader can inspect what the LLM saw + emitted.
    """
    user = _build_user(name, facts)
    response, usage = await runner.call_json(
        kind="alias_gen",
        system_prompt=_SYSTEM,
        user_content=user,
        max_tokens=400,
    )

    raw = response.get("aliases", []) if isinstance(response, dict) else []
    aliases: list[str] = []
    seen: set[str] = set()
    lower_name = name.strip().lower()
    for item in raw:
        if not isinstance(item, str):
            continue
        cleaned = item.strip()
        if not cleaned:
            continue
        lower = cleaned.lower()
        if lower == lower_name or lower in seen:
            continue
        seen.add(lower)
        aliases.append(cleaned)
    return aliases, usage, response if isinstance(response, dict) else {}
