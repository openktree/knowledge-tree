"""Entity name validator shared across extractors and seed dedup.

Pure heuristic — no LLM, no spaCy, no gateway dependency.
"""

from __future__ import annotations

import re


def is_valid_entity_name(name: str) -> bool:
    """Reject corrupted or hallucinated entity names.

    Returns ``False`` for:
    - Names shorter than 2 or longer than 150 characters
    - Pure initials patterns (all tokens are single letters, e.g. ``"K. M. A."``)
    - Repeated substring patterns (e.g. ``"K. M. A. K. M. A. K. M. A."``)
    - Citation artifacts containing ``"et al."``
    - Names where less than 40% of characters are alphabetic
    """
    if not name or len(name) < 2 or len(name) > 150:
        return False

    if "et al" in name.lower():
        return False

    alpha_count = sum(1 for c in name if c.isalpha())
    if len(name) > 0 and alpha_count / len(name) < 0.4:
        return False

    tokens = name.replace(".", "").split()
    if tokens and all(len(t) == 1 for t in tokens):
        return False

    normalized = re.sub(r"\s+", " ", name.lower().strip())
    if len(normalized) >= 10:
        for pattern_len in range(3, min(21, len(normalized) // 2 + 1)):
            pattern = normalized[:pattern_len]
            count = 0
            pos = 0
            while pos <= len(normalized) - pattern_len:
                if normalized[pos : pos + pattern_len] == pattern:
                    count += 1
                    pos += pattern_len
                else:
                    pos += 1
            if count >= 3 and count * pattern_len >= len(normalized) * 0.7:
                return False

    return True
