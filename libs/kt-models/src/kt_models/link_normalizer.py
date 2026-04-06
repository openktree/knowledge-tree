"""Normalize AI-generated markdown links before database storage.

LLMs sometimes produce malformed or inconsistent link formats. This module
provides a single function that canonicalises all node/fact links so that
both the frontend (react-markdown) and wiki (parseRichText) render them
correctly.
"""

from __future__ import annotations

import re

# UUID pattern fragment (hex + hyphens, 36 chars typical but flexible)
_UUID = r"[0-9a-f][0-9a-f-]{7,}"

# ── Compiled patterns (order matters) ────────────────────────────────

# 1. {{fact:<uuid>|<label>}}  →  [<label>](/facts/<uuid>)
_DOUBLE_BRACE_FACT = re.compile(r"\{\{fact:(" + _UUID + r")\|([^}]+)\}\}", re.IGNORECASE)

# 2. {fact:<uuid>|<label>}  →  [<label>](/facts/<uuid>)
_SINGLE_BRACE_FACT_LABEL = re.compile(r"\{fact:(" + _UUID + r")\|([^}]+)\}", re.IGNORECASE)

# 3. {fact:<uuid>}  (bare, no label)
_BARE_FACT_TOKEN = re.compile(r"\{fact:(" + _UUID + r")\}", re.IGNORECASE)

# 4-5. [text](/facts:<uuid>...)  or  [text](/nodes:<uuid>...)
#      colon after path segment, optional wrong closing bracket
_COLON_LINK = re.compile(r"\[([^\]]+)\]\(/(facts|nodes):(" + _UUID + r")\s*[)\]]", re.IGNORECASE)

# 6. [text](/facts/<uuid>]  or  [text](/nodes/<uuid>]  — wrong closing bracket
_WRONG_BRACKET = re.compile(r"\[([^\]]+)\]\(/(facts|nodes)/(" + _UUID + r")\s*\]", re.IGNORECASE)

# 7-8. [/facts/<uuid>]  or  [/facts:<uuid>]  or  [/nodes/...]  — bare bracket, no link text
_BARE_BRACKET = re.compile(r"\[/(facts|nodes)[/:](" + _UUID + r")\]", re.IGNORECASE)

_BARE_LABEL = {"facts": "source", "nodes": "node"}


def normalize_ai_links(text: str, *, preserve_fact_tokens: bool = False) -> str:
    """Normalise AI-generated links to canonical markdown format.

    Args:
        text: Raw AI output (markdown string).
        preserve_fact_tokens: When *True*, keep bare ``{fact:<uuid>}``
            tokens as-is (used for edge justifications where the rendering
            pipeline expects that format).

    Returns:
        The text with all links in canonical ``[text](/path/<uuid>)`` form.
    """
    if not text:
        return text

    # 1. {{fact:uuid|label}}
    out = _DOUBLE_BRACE_FACT.sub(r"[\2](/facts/\1)", text)

    # 2. {fact:uuid|label}
    out = _SINGLE_BRACE_FACT_LABEL.sub(r"[\2](/facts/\1)", out)

    # 3. {fact:uuid} bare tokens
    if not preserve_fact_tokens:
        out = _BARE_FACT_TOKEN.sub(r"[source](/facts/\1)", out)

    # 4-5. colon after /facts or /nodes  +  optional wrong bracket
    out = _COLON_LINK.sub(r"[\1](/\2/\3)", out)

    # 6. wrong closing bracket  ]  instead of  )
    out = _WRONG_BRACKET.sub(r"[\1](/\2/\3)", out)

    # 7-8. bare bracket with no link text
    def _bare_replace(m: re.Match[str]) -> str:
        kind = m.group(1).lower()
        uid = m.group(2)
        label = _BARE_LABEL.get(kind, kind)
        return f"[{label}](/{kind}/{uid})"

    out = _BARE_BRACKET.sub(_bare_replace, out)

    return out
