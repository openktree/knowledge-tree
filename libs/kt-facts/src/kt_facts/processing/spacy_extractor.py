"""spaCy-based entity/concept extractor using NER + grammatical noun chunks.

Extracts exhaustively at zero LLM cost. Every named entity and noun chunk
becomes a candidate, filtered for filler words. The graph self-organizes
through co-occurrence — even generic concepts like "tasks" create useful
navigation paths when linked to specific facts.
"""

from __future__ import annotations

import logging

from kt_facts.processing.extractor_base import EntityExtractor, ExtractedEntity

logger = logging.getLogger(__name__)

# POS tags to strip from the leading edge of noun chunks
_STRIP_POS = frozenset({"DET", "PRON", "ADP", "CCONJ", "SCONJ", "PART", "AUX", "PUNCT"})

_FILLER_WORDS = frozenset(
    {
        # Pronouns
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
        # Question/relative
        "how",
        "what",
        "why",
        "when",
        "where",
        "which",
        "who",
        "whom",
        # Determiners
        "the",
        "a",
        "an",
        "this",
        "that",
        "these",
        "those",
        # Common filler
        "some",
        "most",
        "many",
        "several",
        "various",
        "other",
        "one",
        # Verbs that slip through
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


def _is_filler(name: str) -> bool:
    """Return True if the candidate is pure filler / too short / pure numbers."""
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
        import spacy

        self._nlp = spacy.load(model_name)
        logger.info("Loaded spaCy model: %s", model_name)

    async def extract(
        self,
        facts: list,
        *,
        scope: str = "",
    ) -> list[ExtractedEntity] | None:
        if not facts:
            return None

        # name (lowered) -> {canonical_name, fact_indices set}
        merged: dict[str, dict] = {}

        for i, fact in enumerate(facts, 1):
            content = getattr(fact, "content", "")
            if not content:
                continue

            doc = self._nlp(content)
            candidates: dict[str, str] = {}  # name -> name (preserves first casing)

            # Named entities from NER
            for ent in doc.ents:
                name = ent.text.strip()
                if name:
                    candidates.setdefault(name, name)

            # Noun chunks — strip leading determiners/pronouns
            for chunk in doc.noun_chunks:
                tokens = [t for t in chunk if t.pos_ not in _STRIP_POS]
                if tokens:
                    name = " ".join(t.text for t in tokens).strip()
                    if name:
                        candidates.setdefault(name, name)

            # Filter and merge
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
