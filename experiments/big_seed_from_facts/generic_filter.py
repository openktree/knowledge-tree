"""Generic-term filters for spaCy-extracted candidates.

Layered, deterministic, no-LLM pre-filter. Each check returns either
`None` (keep) or a `FilterDecision` with rejection reason + details
that the report can render for fine-tuning.

Filters in pipeline order:
  1. NER-label drop — spaCy label in {DATE,TIME,CARDINAL,ORDINAL,
     PERCENT,MONEY,QUANTITY}.
  2. Regex — pure numeric / date-like strings ("1915", "2020s",
     "Q3 2019", "page 42").
  3. Brysbaert concreteness — single-token NOUN with Brysbaert
     concreteness < CONCRETENESS_THRESHOLD. Multi-token phrases pass
     through (specificity preserved).
"""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from pathlib import Path

CONCRETENESS_THRESHOLD = 2.5
CONCRETENESS_BORDER_LOW = 2.0
CONCRETENESS_BORDER_HIGH = 2.8

_SKIP_NER_LABELS = {"DATE", "TIME", "CARDINAL", "ORDINAL", "PERCENT", "MONEY", "QUANTITY"}


# Regex patterns — pure numeric / date-like surface forms that made it
# through NER (either because NER missed them or they come from noun
# chunks). Single combined pattern list with names for report detail.
_REGEX_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("pure_digits", re.compile(r"^\d+(?:[.,]\d+)*$")),
    ("year_like", re.compile(r"^(?:19|20|21)\d{2}s?$")),
    ("iso_date", re.compile(r"^\d{4}-\d{1,2}(?:-\d{1,2})?$")),
    ("quarter", re.compile(r"^[Qq][1-4]\s*\d{2,4}$")),
    ("page_num", re.compile(r"^(?:p\.?|pg\.?|page)\s*\d+$", re.IGNORECASE)),
    ("numbered_item", re.compile(r"^(?:no\.?|#)\s*\d+$", re.IGNORECASE)),
    ("percent", re.compile(r"^\d+(?:\.\d+)?\s*%$")),
    ("money_like", re.compile(r"^[$€£¥]\s*\d+(?:[.,]\d+)*$")),
]


@dataclass
class FilterDecision:
    reason: str          # "ner_label" | "regex" | "concreteness"
    detail: str          # e.g. "CARDINAL", "year_like", "concreteness=1.47"
    extra: dict | None = None


class GenericFilter:
    """Stateful filter. Brysbaert CSV loaded lazily on first concreteness
    check. Pass `enabled=False` to disable all checks (report still
    records what WOULD have been rejected)."""

    def __init__(self, concreteness_path: Path | None = None, enabled: bool = True) -> None:
        self._path = concreteness_path or (
            Path(__file__).resolve().parent / "data" / "concreteness.tsv"
        )
        self._lookup: dict[str, float] | None = None
        self.enabled = enabled

    def _load(self) -> dict[str, float]:
        if self._lookup is not None:
            return self._lookup
        lookup: dict[str, float] = {}
        with self._path.open(encoding="utf-8") as f:
            reader = csv.reader(f, delimiter="\t")
            header = next(reader, None)
            if header is None:
                self._lookup = {}
                return self._lookup
            for row in reader:
                if len(row) < 3:
                    continue
                try:
                    lookup[row[0].strip().lower()] = float(row[2])
                except ValueError:
                    continue
        self._lookup = lookup
        return self._lookup

    def concreteness(self, word: str) -> float | None:
        """Return concreteness rating (1-5) or None if unrated."""
        return self._load().get(word.strip().lower())

    def check_ner_label(self, label: str | None) -> FilterDecision | None:
        if not label or label not in _SKIP_NER_LABELS:
            return None
        return FilterDecision(reason="ner_label", detail=label)

    def check_regex(self, name: str) -> FilterDecision | None:
        stripped = name.strip()
        if not stripped:
            return FilterDecision(reason="regex", detail="empty")
        for pat_name, pat in _REGEX_PATTERNS:
            if pat.match(stripped):
                return FilterDecision(reason="regex", detail=pat_name)
        return None

    def check_concreteness(
        self,
        name: str,
        head_lemma: str | None,
        token_count: int,
    ) -> FilterDecision | None:
        """Apply concreteness gate only to single-token NOUN candidates.

        Multi-token phrases are passed through untouched — compound names
        like 'theory of everything' contain an abstract head but carry
        specificity via the modifier.
        """
        if token_count > 1:
            return None
        if not head_lemma:
            return None
        score = self.concreteness(head_lemma)
        if score is None:
            # Not in the Brysbaert dict → proper noun, foreign word,
            # alphanumeric, or technical term. Default: keep.
            return None
        if score < CONCRETENESS_THRESHOLD:
            return FilterDecision(
                reason="concreteness",
                detail=f"{score:.2f}",
                extra={"score": score, "head_lemma": head_lemma},
            )
        return None

    def is_borderline(self, head_lemma: str | None) -> tuple[bool, float | None]:
        """Concreteness in [BORDER_LOW, BORDER_HIGH]. Useful for tuning."""
        if not head_lemma:
            return False, None
        score = self.concreteness(head_lemma)
        if score is None:
            return False, None
        if CONCRETENESS_BORDER_LOW <= score <= CONCRETENESS_BORDER_HIGH:
            return True, score
        return False, score


__all__ = [
    "GenericFilter",
    "FilterDecision",
    "CONCRETENESS_THRESHOLD",
    "CONCRETENESS_BORDER_LOW",
    "CONCRETENESS_BORDER_HIGH",
]
