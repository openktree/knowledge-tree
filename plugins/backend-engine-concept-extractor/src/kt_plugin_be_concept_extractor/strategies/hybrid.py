"""Hybrid spaCy + LLM entity extractor.

Three-phase pipeline:
  Phase 1 — spaCy NER + noun chunks (exhaustive recall, zero LLM cost).
  Phase 2 — LLM shell classifier (OSS, minimal reasoning): rejects
             propositional-slot nouns so they never reach the seed pool.
  Phase 3 — LLM alias generator (Gemini flash, minimal reasoning): enriches
             the kept entities with universal naming variants.

Disambiguation is NOT part of this extractor — it lives downstream in
``kt_facts.processing.seed_dedup`` and is run per name at genesis time.

The spaCy model is loaded once per process and reused across calls.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any

from kt_core_engine_api.extractor import EntityExtractor, ExtractedEntity

from .hybrid_prompts import (
    ALIAS_BATCH_SYSTEM,
    SHELL_BATCH_SYSTEM,
    build_alias_batch_user,
    build_shell_batch_user,
)

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

# NER labels that never produce useful knowledge-graph entities
_SKIP_NER_LABELS = frozenset({
    "DATE", "TIME", "CARDINAL", "ORDINAL", "QUANTITY", "MONEY", "PERCENT",
})

import re

# Matches trailing version strings: "v1.2.3", "v1-2-3", "1.26.4", etc.
_VERSION_SUFFIX_RE = re.compile(
    r"\s+v?\d+(?:[.\-]\d+)+$",
    re.IGNORECASE,
)
# Matches standalone version strings: "v1.0.0", "v2-2-2", "1.26.4"
_PURE_VERSION_RE = re.compile(
    r"^v?\d+(?:[.\-]\d+)+$",
    re.IGNORECASE,
)
# Matches supplementary table/figure references: "Tables S28", "Figure 2A"
_SUPP_REF_RE = re.compile(
    r"^(?:tables?|figures?|supplementary|supp\.?)\s+\S{1,6}$",
    re.IGNORECASE,
)


def _clean_name(name: str) -> str:
    """Strip version suffixes and reject pure-noise names.

    Returns cleaned name or empty string if nothing useful remains.
    """
    name = _VERSION_SUFFIX_RE.sub("", name).strip()
    if not name:
        return ""
    if _PURE_VERSION_RE.match(name):
        return ""
    if _SUPP_REF_RE.match(name):
        return ""
    return name


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
    """An entity candidate rejected by the LLM shell classifier.

    Stored in the plugin DB schema for downstream disambiguation/analysis.
    """

    name: str
    ner_label: str | None
    source: str              # "ner" | "chunk"
    fact_ids: list[str]
    scope: str = field(default="")


# ── spaCy pass ────────────────────────────────────────────────────────────


_NLP: Any = None


def _get_nlp() -> Any:
    """Load spaCy model once per process."""
    global _NLP
    if _NLP is not None:
        return _NLP
    import spacy

    _NLP = spacy.load("en_core_web_lg")
    return _NLP


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

    nlp = _get_nlp()
    merged: dict[str, _RichCandidate] = {}

    for i, fact in enumerate(facts, 1):
        content: str = getattr(fact, "content", "") or ""
        fact_id: str = str(getattr(fact, "id", "")) or ""
        if not content.strip():
            continue

        doc = nlp(content)

        for ent in doc.ents:
            if ent.label_ in _SKIP_NER_LABELS:
                continue
            name = _clean_name(ent.text.strip())
            if not name or _is_filler(name):
                continue
            key = name.lower()
            cand = merged.get(key)
            if cand is None:
                merged[key] = _RichCandidate(
                    name=name,
                    ner_label=ent.label_,
                    source="ner",
                    fact_indices=[i],
                    fact_ids=[fact_id] if fact_id else [],
                )
            else:
                if i not in cand.fact_indices:
                    cand.fact_indices.append(i)
                if fact_id and fact_id not in cand.fact_ids:
                    cand.fact_ids.append(fact_id)

        for chunk in doc.noun_chunks:
            tokens = [t for t in chunk if t.pos_ not in _STRIP_POS]
            if not tokens:
                continue
            name = _clean_name(" ".join(t.text for t in tokens).strip())
            if not name or _is_filler(name):
                continue
            key = name.lower()
            cand = merged.get(key)
            if cand is None:
                merged[key] = _RichCandidate(
                    name=name,
                    ner_label=None,
                    source="chunk",
                    fact_indices=[i],
                    fact_ids=[fact_id] if fact_id else [],
                )
            else:
                if i not in cand.fact_indices:
                    cand.fact_indices.append(i)
                if fact_id and fact_id not in cand.fact_ids:
                    cand.fact_ids.append(fact_id)

    return list(merged.values())


# ── LLM helpers ───────────────────────────────────────────────────────────


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


async def _call_json(
    gateway: Any,
    *,
    model_id: str,
    thinking_level: str,
    system: str,
    user: str,
    max_tokens: int,
) -> dict[str, Any] | None:
    kwargs: dict[str, Any] = {
        "model_id": model_id,
        "messages": [{"role": "user", "content": user}],
        "system_prompt": system,
        "temperature": 0.0,
        "max_tokens": max_tokens,
    }
    if thinking_level:
        kwargs["reasoning_effort"] = thinking_level
    try:
        result = await gateway.generate_json(**kwargs)
    except Exception:
        logger.error("Hybrid extractor LLM call failed (model=%s)", model_id, exc_info=True)
        raise
    if not isinstance(result, dict):
        logger.error("Hybrid extractor LLM returned non-dict: %s (model=%s)", type(result), model_id)
        raise ValueError(f"Expected dict from LLM, got {type(result)}")
    return result


async def _shell_classify_batch(
    names: list[str],
    *,
    gateway: Any,
    model_id: str,
    thinking_level: str,
    batch_size: int,
    concurrency: int,
) -> dict[str, bool]:
    """Return ``{name: is_shell}``. Names not in the dict are treated as kept."""
    if not names:
        return {}
    sem = asyncio.Semaphore(max(1, concurrency))
    chunks = [names[i : i + batch_size] for i in range(0, len(names), batch_size)]

    async def _run(chunk: list[str]) -> dict[str, bool]:
        async with sem:
            resp = await _call_json(
                gateway,
                model_id=model_id,
                thinking_level=thinking_level,
                system=SHELL_BATCH_SYSTEM,
                user=build_shell_batch_user(chunk),
                max_tokens=min(1200, 40 * len(chunk) + 200),
            )
        by_idx: dict[int, bool] = {}
        for r in resp.get("results", []) or []:
            if not isinstance(r, dict):
                continue
            try:
                idx = int(r.get("index"))
            except (TypeError, ValueError):
                continue
            by_idx[idx] = bool(r.get("is_shell", False))
        out: dict[str, bool] = {}
        for i, name in enumerate(chunk, start=1):
            if i in by_idx:
                out[name] = by_idx[i]
        return out

    merged: dict[str, bool] = {}
    results = await asyncio.gather(*[_run(c) for c in chunks], return_exceptions=True)
    for result in results:
        if isinstance(result, BaseException):
            raise result
        merged.update(result)
    return merged


async def _alias_gen_batch(
    names: list[str],
    *,
    gateway: Any,
    model_id: str,
    thinking_level: str,
    batch_size: int,
    concurrency: int,
) -> dict[str, list[str]]:
    """Return ``{name: [aliases]}``. Missing entries get empty alias list."""
    if not names:
        return {}
    sem = asyncio.Semaphore(max(1, concurrency))
    chunks = [names[i : i + batch_size] for i in range(0, len(names), batch_size)]

    async def _run(chunk: list[str]) -> dict[str, list[str]]:
        async with sem:
            resp = await _call_json(
                gateway,
                model_id=model_id,
                thinking_level=thinking_level,
                system=ALIAS_BATCH_SYSTEM,
                user=build_alias_batch_user(chunk),
                max_tokens=min(2500, 80 * len(chunk) + 400),
            )
        if not resp:
            return {}
        by_idx: dict[int, list] = {}
        for r in resp.get("results", []) or []:
            if not isinstance(r, dict):
                continue
            try:
                idx = int(r.get("index"))
            except (TypeError, ValueError):
                continue
            aliases = r.get("aliases", [])
            if isinstance(aliases, list):
                by_idx[idx] = aliases
        out: dict[str, list[str]] = {}
        for i, name in enumerate(chunk, start=1):
            out[name] = _clean_aliases(by_idx.get(i, []), name)
        return out

    merged: dict[str, list[str]] = {}
    for result in await asyncio.gather(*[_run(c) for c in chunks], return_exceptions=True):
        if isinstance(result, BaseException):
            logger.warning("Alias gen batch raised: %s", result)
            continue
        merged.update(result)
    return merged


# ── HybridEntityExtractor ─────────────────────────────────────────────────


class HybridEntityExtractor(EntityExtractor):
    """spaCy recall → LLM shell classifier → LLM alias generator.

    Shell candidates are captured and exposed via ``get_last_side_outputs()``
    under the ``"shells"`` key. Downstream persistence is handled by the
    plugin's registered :class:`PostExtractionHook`. Fail-open: any LLM
    error keeps the candidate and produces no shell row for that batch.
    """

    def __init__(self, gateway: Any) -> None:
        self._gateway = gateway
        self._last_shells: list[ShellCandidate] = []

    def get_last_shells(self) -> list[ShellCandidate]:
        """Internal accessor — prefer ``get_last_side_outputs()``."""
        return list(self._last_shells)

    def get_last_side_outputs(self) -> dict[str, list]:
        return {"shells": list(self._last_shells)}

    async def extract(
        self,
        facts: list,
        *,
        scope: str = "",
    ) -> list[ExtractedEntity] | None:
        self._last_shells = []
        if not facts:
            return []

        candidates = await asyncio.to_thread(_spacy_pass, facts)
        if not candidates:
            return []

        from kt_plugin_be_concept_extractor.settings import get_concept_extractor_settings

        settings = get_concept_extractor_settings()

        logger.info(
            "Hybrid extractor: %d spaCy candidates from %d facts (scope=%r)",
            len(candidates),
            len(facts),
            scope,
        )

        names = [c.name for c in candidates]
        shell_verdicts = await _shell_classify_batch(
            names,
            gateway=self._gateway,
            model_id=settings.shell_model,
            thinking_level=settings.shell_thinking_level,
            batch_size=settings.shell_batch_size,
            concurrency=settings.shell_concurrency,
        )

        keep_names: list[str] = []
        kept_cands: list[_RichCandidate] = []
        shells: list[ShellCandidate] = []
        for cand in candidates:
            if shell_verdicts.get(cand.name, False):
                shells.append(
                    ShellCandidate(
                        name=cand.name,
                        ner_label=cand.ner_label,
                        source=cand.source,
                        fact_ids=cand.fact_ids,
                        scope=scope,
                    )
                )
            else:
                keep_names.append(cand.name)
                kept_cands.append(cand)

        alias_map = await _alias_gen_batch(
            keep_names,
            gateway=self._gateway,
            model_id=settings.alias_model,
            thinking_level=settings.alias_thinking_level,
            batch_size=settings.alias_batch_size,
            concurrency=settings.alias_concurrency,
        )

        kept: list[ExtractedEntity] = [
            ExtractedEntity(
                name=cand.name,
                fact_indices=cand.fact_indices,
                aliases=alias_map.get(cand.name, []),
            )
            for cand in kept_cands
        ]

        self._last_shells = shells
        logger.info(
            "Hybrid extractor: %d kept, %d shells (scope=%r)",
            len(kept),
            len(shells),
            scope,
        )
        return kept
