"""Hybrid spaCy + LLM entity extractor.

Two-pass algorithm:
  Phase 1 — spaCy: exhaustive NER + noun-chunk candidates at zero LLM cost.
  Phase 2 — LLM validation: batched keep/reject + alias enrichment.

The LLM receives the spaCy candidates (not raw facts) so it does far less
work than the standalone LLM extractor. No disambiguation — that is a
separate downstream step.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any

from kt_facts.processing.extractor_base import EntityExtractor, ExtractedEntity

logger = logging.getLogger(__name__)

# POS tags stripped from noun chunk leading edge
_STRIP_POS = frozenset({"DET", "PRON", "ADP", "CCONJ", "SCONJ", "PART", "AUX", "PUNCT"})

_FILLER_WORDS = frozenset(
    {
        "i", "me", "my", "we", "us", "our", "you", "your",
        "he", "him", "his", "she", "her", "they", "them", "their", "it", "its",
        "how", "what", "why", "when", "where", "which", "who", "whom",
        "the", "a", "an", "this", "that", "these", "those",
        "some", "most", "many", "several", "various", "other", "one",
        "is", "are", "was", "were", "be", "been", "being",
        "have", "has", "had", "do", "does", "did",
    }
)


# ── Intermediate types ────────────────────────────────────────────────────


@dataclass
class _RichCandidate:
    """Internal candidate produced by the spaCy pass."""

    name: str
    ner_label: str | None    # spaCy NER label, or None for noun chunks
    source: str              # "ner" | "chunk"
    fact_indices: list[int]  # 1-indexed into the fact list
    fact_ids: list[str]      # UUID strings for the same facts


@dataclass
class ShellCandidate:
    """An entity candidate rejected by the LLM validation pass.

    Stored in the plugin DB schema for the future disambiguation step.
    """

    name: str
    ner_label: str | None
    source: str              # "ner" | "chunk"
    fact_ids: list[str]
    scope: str = field(default="")


# ── Validation prompt ─────────────────────────────────────────────────────

_VALIDATION_SYSTEM = """\
You are a precision validator for a knowledge graph entity extraction pipeline.

You will receive a numbered list of entity candidates extracted by an NLP tool \
(spaCy NER + noun chunks) from a set of source facts.

For EACH candidate, decide:
  - keep: true  → it is a meaningful knowledge graph node \
(specific named entity, concept, event, location)
  - keep: false → it is a shell noun or generic term that adds no value \
(e.g. "approach", "method", "study", "team", "result", "data", "work", \
"process", "system", "use", "analysis", "way", "issue", "area", "part")

For kept candidates, add known aliases, acronyms, or alternate names.

Rules:
- Do NOT merge or deduplicate candidates — judge each independently.
- Do NOT perform disambiguation — just keep or reject.
- Specific named entities (organizations, people, events) should almost always be kept.
- Generic noun phrases with no identity outside the context should be rejected.

Respond with ONLY a JSON object — no markdown, no commentary:
{"results": [{"name": "...", "keep": true, "aliases": [...]}, ...]}

The "results" array MUST have the same length and order as the input candidates."""

_VALIDATION_USER = """\
Scope: {scope}

Candidates ({count} total):
{candidates}

Validate each candidate."""


# ── spaCy pass ────────────────────────────────────────────────────────────


def _is_filler(name: str) -> bool:
    if len(name) < 2:
        return True
    words = name.lower().split()
    if not words or all(w in _FILLER_WORDS for w in words):
        return True
    if name.replace(" ", "").replace(".", "").replace(",", "").isdigit():
        return True
    return False


def _spacy_pass(facts: list) -> list[_RichCandidate]:
    """Run spaCy NER + noun chunks over all facts; return merged rich candidates."""
    if not facts:
        return []

    import spacy  # lazy — spaCy model load is expensive

    try:
        nlp = spacy.load("en_core_web_lg")
    except OSError:
        nlp = spacy.load("en_core_web_sm")

    # name_lower -> RichCandidate
    merged: dict[str, _RichCandidate] = {}

    for i, fact in enumerate(facts, 1):
        content: str = getattr(fact, "content", "") or ""
        fact_id: str = str(getattr(fact, "id", "")) or ""
        if not content.strip():
            continue

        doc = nlp(content)

        # NER entities
        for ent in doc.ents:
            name = ent.text.strip()
            if not name or _is_filler(name):
                continue
            key = name.lower()
            if key in merged:
                cand = merged[key]
                if i not in cand.fact_indices:
                    cand.fact_indices.append(i)
                if fact_id and fact_id not in cand.fact_ids:
                    cand.fact_ids.append(fact_id)
            else:
                merged[key] = _RichCandidate(
                    name=name,
                    ner_label=ent.label_,
                    source="ner",
                    fact_indices=[i],
                    fact_ids=[fact_id] if fact_id else [],
                )

        # Noun chunks — strip leading filler POS
        for chunk in doc.noun_chunks:
            tokens = [t for t in chunk if t.pos_ not in _STRIP_POS]
            if not tokens:
                continue
            name = " ".join(t.text for t in tokens).strip()
            if not name or _is_filler(name):
                continue
            key = name.lower()
            if key in merged:
                cand = merged[key]
                if i not in cand.fact_indices:
                    cand.fact_indices.append(i)
                if fact_id and fact_id not in cand.fact_ids:
                    cand.fact_ids.append(fact_id)
            else:
                merged[key] = _RichCandidate(
                    name=name,
                    ner_label=None,
                    source="chunk",
                    fact_indices=[i],
                    fact_ids=[fact_id] if fact_id else [],
                )

    return list(merged.values())


# ── LLM validation pass ───────────────────────────────────────────────────


def _format_candidates(candidates: list[_RichCandidate], facts: list) -> str:
    """Format candidate list for the LLM prompt."""
    lines: list[str] = []
    for idx, cand in enumerate(candidates, 1):
        label = cand.ner_label or "chunk"
        # Attach a short snippet from one source fact
        snippet = ""
        if cand.fact_indices:
            fi = cand.fact_indices[0] - 1  # convert to 0-indexed
            if 0 <= fi < len(facts):
                snippet = (getattr(facts[fi], "content", "") or "")[:80]
        lines.append(f'{idx}. [{label}] "{cand.name}" — from: "{snippet}"')
    return "\n".join(lines)


async def _validate_batch(
    candidates: list[_RichCandidate],
    facts: list,
    gateway: Any,
    model_id: str,
    scope: str,
) -> list[dict[str, Any]] | None:
    """Call LLM to validate a batch of candidates. Returns raw result list or None."""
    formatted = _format_candidates(candidates, facts)
    user_msg = _VALIDATION_USER.format(
        scope=scope or "general",
        count=len(candidates),
        candidates=formatted,
    )

    try:
        result = await gateway.generate_json(
            model_id=model_id,
            messages=[{"role": "user", "content": user_msg}],
            system_prompt=_VALIDATION_SYSTEM,
            temperature=0.0,
            max_tokens=8000,
        )
        if not result or "results" not in result:
            return None
        return result["results"]  # type: ignore[return-value]
    except Exception:
        logger.warning("Hybrid extractor LLM validation batch failed", exc_info=True)
        return None


# ── HybridEntityExtractor ─────────────────────────────────────────────────


class HybridEntityExtractor(EntityExtractor):
    """Two-pass extractor: spaCy recall → LLM validation + alias enrichment.

    Implements ``EntityExtractor`` ABC (``extract()`` method).
    Also provides ``extract_with_shells()`` for the pipeline to access
    rejected candidates for DB persistence.
    """

    def __init__(self, gateway: Any) -> None:
        self._gateway = gateway
        self._last_shells: list[ShellCandidate] = []

    async def extract(
        self,
        facts: list,
        *,
        scope: str = "",
    ) -> list[ExtractedEntity] | None:
        kept, shells = await self._run(facts, scope=scope)
        self._last_shells = shells
        return kept if kept else None

    async def extract_with_shells(
        self,
        facts: list,
        *,
        scope: str = "",
    ) -> tuple[list[ExtractedEntity], list[ShellCandidate]]:
        """Extended entry point used by pipeline.py to access shell candidates."""
        kept, shells = await self._run(facts, scope=scope)
        self._last_shells = shells
        return kept or [], shells

    async def _run(
        self,
        facts: list,
        *,
        scope: str,
    ) -> tuple[list[ExtractedEntity], list[ShellCandidate]]:
        if not facts:
            return [], []

        # Phase 1: spaCy pass
        candidates = await asyncio.to_thread(_spacy_pass, facts)
        if not candidates:
            return [], []

        logger.info(
            "Hybrid extractor: %d spaCy candidates from %d facts (scope=%r)",
            len(candidates),
            len(facts),
            scope,
        )

        # Resolve model + batch size from settings
        from kt_config.settings import get_settings

        settings = get_settings()
        model_id = settings.hybrid_extractor_validation_model
        batch_size = settings.hybrid_extractor_validation_batch_size

        # Phase 2: LLM validation in batches (parallel with bounded concurrency)
        batches: list[list[_RichCandidate]] = [
            candidates[i : i + batch_size] for i in range(0, len(candidates), batch_size)
        ]

        sem = asyncio.Semaphore(4)

        async def _limited(batch: list[_RichCandidate]) -> list[dict[str, Any]] | None:
            async with sem:
                return await _validate_batch(batch, facts, self._gateway, model_id, scope)

        batch_results = await asyncio.gather(*[_limited(b) for b in batches], return_exceptions=True)

        # Build kept + shells from results
        kept: list[ExtractedEntity] = []
        shells: list[ShellCandidate] = []

        for batch, result in zip(batches, batch_results):
            if isinstance(result, BaseException):
                logger.warning("Hybrid extractor batch exception: %s", result)
                # On error: keep all candidates in the batch (fail open for recall)
                for cand in batch:
                    kept.append(
                        ExtractedEntity(
                            name=cand.name,
                            fact_indices=cand.fact_indices,
                        )
                    )
                continue

            if result is None:
                # LLM failed for this batch — keep all (fail open)
                for cand in batch:
                    kept.append(
                        ExtractedEntity(
                            name=cand.name,
                            fact_indices=cand.fact_indices,
                        )
                    )
                continue

            for cand, verdict in zip(batch, result):
                if not isinstance(verdict, dict):
                    # Malformed — keep candidate
                    kept.append(ExtractedEntity(name=cand.name, fact_indices=cand.fact_indices))
                    continue

                if verdict.get("keep", True):
                    aliases = [
                        a.strip()
                        for a in verdict.get("aliases", [])
                        if isinstance(a, str) and a.strip()
                    ]
                    kept.append(
                        ExtractedEntity(
                            name=cand.name,
                            fact_indices=cand.fact_indices,
                            aliases=aliases,
                        )
                    )
                else:
                    shells.append(
                        ShellCandidate(
                            name=cand.name,
                            ner_label=cand.ner_label,
                            source=cand.source,
                            fact_ids=cand.fact_ids,
                            scope=scope,
                        )
                    )

        logger.info(
            "Hybrid extractor: %d kept, %d shells (scope=%r)",
            len(kept),
            len(shells),
            scope,
        )
        return kept, shells
