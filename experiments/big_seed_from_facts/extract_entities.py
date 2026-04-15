"""spaCy NER + noun-chunk entity extraction, with optional generic-term filter.

Returns (extracted, ignored, stats):
  - extracted: kept candidates
  - ignored: candidates rejected by the filter, with reason + details
    so the report can audit them and inform fine-tuning
  - stats: pipeline counts

Adapted from experiments/spacy_vs_llm_extraction.py.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

import spacy

from .generic_filter import FilterDecision, GenericFilter


# Paired-quote strippers — leave contractions intact by requiring 3+ chars
# between quotes. Unicode smart quotes are normalized to ASCII first (NFKC).
_PAIRED_SINGLE = re.compile(r"'([^'\n]{3,}?)'")
_PAIRED_DOUBLE = re.compile(r'"([^"\n]{3,}?)"')


def _normalize_text(text: str) -> str:
    """NFKC normalize + strip paired quotes around 3+-char phrases.

    Run before spaCy so its tokenizer doesn't fragment quoted titles
    like `'Trends in Cell Biology'` into `Trends` + leftover tokens.
    Contractions (don't, it's) survive because they don't match the
    3-char-minimum paired pattern.
    """
    if not text:
        return text
    text = unicodedata.normalize("NFKC", text)
    text = _PAIRED_SINGLE.sub(r"\1", text)
    text = _PAIRED_DOUBLE.sub(r"\1", text)
    return text

_nlp = None


def _load() -> None:
    global _nlp
    if _nlp is None:
        _nlp = spacy.load("en_core_web_lg")


FILLER_WORDS = {
    "i", "me", "my", "we", "us", "our", "you", "your",
    "he", "him", "his", "she", "her", "they", "them", "their", "it", "its",
    "how", "what", "why", "when", "where", "which", "who", "whom",
    "the", "a", "an", "this", "that", "these", "those",
    "some", "most", "many", "several", "various", "other", "one",
    "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did",
}
STRIP_POS = {"DET", "PRON", "ADP", "CCONJ", "SCONJ", "PART", "AUX", "PUNCT"}

_NER_TYPE_MAP = {
    "PERSON": "entity",
    "ORG": "entity",
    "GPE": "location",
    "LOC": "location",
    "FAC": "location",
    "EVENT": "event",
    # DATE/TIME intentionally absent — handled by generic_filter
    "NORP": "concept",
    "PRODUCT": "concept",
    "WORK_OF_ART": "concept",
    "LAW": "concept",
    "LANGUAGE": "concept",
}


@dataclass
class Extracted:
    name: str
    node_type: str
    source: str               # 'ner' | 'chunk'
    ner_label: str | None     # None for chunk sources
    head_lemma: str | None    # lemma of head noun (single-token candidates)
    token_count: int
    fact_ids: list[str] = field(default_factory=list)


@dataclass
class Ignored:
    name: str
    node_type: str
    source: str
    ner_label: str | None
    head_lemma: str | None
    token_count: int
    reason: str               # ner_label | regex | concreteness
    detail: str
    fact_ids: list[str] = field(default_factory=list)


def _content_tokens(doc_span) -> list:
    """Content tokens — strip determiners/pronouns/prepositions and filler words."""
    tokens = [t for t in doc_span if t.pos_ not in STRIP_POS]
    return [t for t in tokens if t.text.strip().lower() not in FILLER_WORDS]


def _extract_mentions(text: str) -> list[tuple[str, str, str, str | None, str | None, int]]:
    """Return list of (name, node_type, source, ner_label, head_lemma, token_count)."""
    _load()
    doc = _nlp(text)

    out: dict[str, tuple[str, str, str | None, str | None, int]] = {}

    # 1. NER entities — preserve the spaCy label for downstream filtering
    for ent in doc.ents:
        name = ent.text.strip()
        if not name:
            continue
        content = _content_tokens(ent)
        token_count = max(1, len(content))
        head_lemma = content[-1].lemma_.lower() if content else None
        ntype = _NER_TYPE_MAP.get(ent.label_, "concept")
        out[name] = (ntype, "ner", ent.label_, head_lemma, token_count)

    # 2. Noun chunks (stripped)
    for chunk in doc.noun_chunks:
        content = _content_tokens(chunk)
        if not content:
            continue
        name = " ".join(t.text for t in content).strip()
        if not name or name in out:
            continue
        head_lemma = content[-1].lemma_.lower()
        out[name] = ("concept", "chunk", None, head_lemma, len(content))

    # 3. Strip obvious junk (short/filler-only/pure digits still checked later by regex filter)
    filtered: list[tuple[str, str, str, str | None, str | None, int]] = []
    for name, (ntype, source, label, head_lemma, tok_count) in out.items():
        words = name.lower().split()
        if not words:
            continue
        if all(w in FILLER_WORDS for w in words):
            continue
        if len(words) == 1 and words[0] in FILLER_WORDS:
            continue
        if len(name) < 2:
            continue
        filtered.append((name, ntype, source, label, head_lemma, tok_count))

    return filtered


def extract_from_facts(
    facts: list[dict],
    *,
    apply_generic_filter: bool = True,
    min_mentions: int = 1,
    generic_filter: GenericFilter | None = None,
) -> tuple[list[Extracted], list[Ignored], dict[str, int]]:
    """Run spaCy over each fact; group mentions by normalized name.

    When apply_generic_filter=True, candidates rejected by the filter
    are returned in `ignored` with reason+detail.
    """
    gf = generic_filter or GenericFilter(enabled=apply_generic_filter)

    kept: dict[str, Extracted] = {}
    rejected: dict[str, Ignored] = {}
    stats: dict[str, int] = {
        "facts": 0, "mentions": 0,
        "unique_kept": 0, "unique_ignored": 0,
        "ner": 0, "chunk": 0,
        "ignored_ner_label": 0, "ignored_regex": 0,
        "ignored_concreteness": 0,
    }

    for f in facts:
        fid = str(f.get("id", ""))
        content = str(f.get("content", ""))
        if not content.strip():
            continue
        stats["facts"] += 1
        mentions = _extract_mentions(_normalize_text(content))

        for name, ntype, source, ner_label, head_lemma, token_count in mentions:
            stats["mentions"] += 1
            stats[source] += 1
            key = name.strip().lower()

            # Filter decision pipeline — NER label + regex only.
            # Concreteness dropped: too many false positives on real
            # phenomena (consciousness, anxiety, belief). Shell-noun
            # filtering is handled by the LLM alias_gen classifier
            # downstream, which is context-aware.
            decision: FilterDecision | None = None
            if apply_generic_filter:
                decision = gf.check_ner_label(ner_label)
                if decision is None:
                    decision = gf.check_regex(name)

            if decision is not None:
                stats[f"ignored_{decision.reason}"] = stats.get(f"ignored_{decision.reason}", 0) + 1
                ig = rejected.get(key)
                if ig is None:
                    rejected[key] = Ignored(
                        name=name,
                        node_type=ntype,
                        source=source,
                        ner_label=ner_label,
                        head_lemma=head_lemma,
                        token_count=token_count,
                        reason=decision.reason,
                        detail=decision.detail,
                        fact_ids=[fid],
                    )
                else:
                    if fid not in ig.fact_ids:
                        ig.fact_ids.append(fid)
                continue

            g = kept.get(key)
            if g is None:
                kept[key] = Extracted(
                    name=name,
                    node_type=ntype,
                    source=source,
                    ner_label=ner_label,
                    head_lemma=head_lemma,
                    token_count=token_count,
                    fact_ids=[fid],
                )
            else:
                if fid not in g.fact_ids:
                    g.fact_ids.append(fid)

    extracted = [e for e in kept.values() if len(e.fact_ids) >= min_mentions]
    ignored = list(rejected.values())
    stats["unique_kept"] = len(extracted)
    stats["unique_ignored"] = len(ignored)
    return extracted, ignored, stats
