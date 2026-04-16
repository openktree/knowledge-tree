"""spaCy-based entity/concept extractor using NER + grammatical noun chunks.

Extracts exhaustively at zero LLM cost. Every named entity and noun chunk
becomes a candidate, filtered for filler words. The graph self-organizes
through co-occurrence — even generic concepts like "tasks" create useful
navigation paths when linked to specific facts.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from kt_core_engine_api.extractor import EntityExtractor, ExtractedEntity

logger = logging.getLogger(__name__)

# POS tags to strip from the leading edge of noun chunks
_STRIP_POS = frozenset({"DET", "PRON", "ADP", "CCONJ", "SCONJ", "PART", "AUX", "PUNCT"})

_FILLER_WORDS = frozenset(
    {
        "i",
        "me",
        "my",
        "we",
        "us",
        "our",
        "you",
        "your",
        "he",
        "him",
        "his",
        "she",
        "her",
        "they",
        "them",
        "their",
        "it",
        "its",
        "how",
        "what",
        "why",
        "when",
        "where",
        "which",
        "who",
        "whom",
        "the",
        "a",
        "an",
        "this",
        "that",
        "these",
        "those",
        "some",
        "most",
        "many",
        "several",
        "various",
        "other",
        "one",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "being",
        "have",
        "has",
        "had",
        "do",
        "does",
        "did",
    }
)

# NER labels that never produce useful knowledge-graph entities
_SKIP_NER_LABELS = frozenset(
    {
        "DATE",
        "TIME",
        "CARDINAL",
        "ORDINAL",
        "QUANTITY",
        "MONEY",
        "PERCENT",
    }
)

_VERSION_SUFFIX_RE = re.compile(r"\s+v?\d+(?:[.\-]\d+)+$", re.IGNORECASE)
_PURE_VERSION_RE = re.compile(r"^v?\d+(?:[.\-]\d+)+$", re.IGNORECASE)
_SUPP_REF_RE = re.compile(
    r"^(?:tables?|figures?|supplementary|supp\.?)\s+\S{1,6}$",
    re.IGNORECASE,
)


def _clean_name(name: str) -> str:
    """Strip version suffixes and reject pure-noise names."""
    name = _VERSION_SUFFIX_RE.sub("", name).strip()
    if not name:
        return ""
    if _PURE_VERSION_RE.match(name):
        return ""
    if _SUPP_REF_RE.match(name):
        return ""
    return name


_NLP: Any = None


def _get_nlp(model_name: str = "en_core_web_lg") -> Any:
    """Load spaCy model once per process."""
    global _NLP
    if _NLP is not None:
        return _NLP
    import spacy

    _NLP = spacy.load(model_name)
    logger.info("Loaded spaCy model: %s", getattr(_NLP, "meta", {}).get("name", model_name))
    return _NLP


def _is_filler(name: str) -> bool:
    if len(name) < 2:
        return True
    words = name.lower().split()
    if not words:
        return True
    if all(w in _FILLER_WORDS for w in words):
        return True
    if name.replace(" ", "").replace(".", "").replace(",", "").isdigit():
        return True
    return False


class SpacyEntityExtractor(EntityExtractor):
    """Extract entities using spaCy NER + noun chunks with filler filtering."""

    def __init__(self, model_name: str = "en_core_web_lg") -> None:
        self._nlp = _get_nlp(model_name)

    async def extract(
        self,
        facts: list,
        *,
        scope: str = "",
    ) -> list[ExtractedEntity] | None:
        if not facts:
            return None

        merged: dict[str, dict] = {}

        for i, fact in enumerate(facts, 1):
            content = getattr(fact, "content", "")
            if not content:
                continue

            doc = self._nlp(content)
            candidates: dict[str, str] = {}

            for ent in doc.ents:
                if ent.label_ in _SKIP_NER_LABELS:
                    continue
                name = _clean_name(ent.text.strip())
                if name:
                    candidates.setdefault(name, name)

            for chunk in doc.noun_chunks:
                tokens = [t for t in chunk if t.pos_ not in _STRIP_POS]
                if tokens:
                    name = _clean_name(" ".join(t.text for t in tokens).strip())
                    if name:
                        candidates.setdefault(name, name)

            for name in candidates.values():
                if _is_filler(name):
                    continue
                key = name.lower()
                if key in merged:
                    merged[key]["fact_indices"].add(i)
                else:
                    merged[key] = {"name": name, "fact_indices": {i}}

        if not merged:
            return None

        return [
            ExtractedEntity(
                name=data["name"],
                fact_indices=sorted(data["fact_indices"]),
            )
            for data in merged.values()
        ]
