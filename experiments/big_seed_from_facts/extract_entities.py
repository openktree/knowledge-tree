"""spaCy NER + noun-chunk entity extraction from raw facts.

Extracted names are normalized (trim + lowercase key for grouping) and
each unique name collects the list of facts that mention it. Output
feeds directly into the big-seed multiplex pipeline.

Adapted from experiments/spacy_vs_llm_extraction.py.
"""

from __future__ import annotations

from dataclasses import dataclass

import spacy

# Lazy-loaded spaCy model
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
    "DATE": "event",
    "NORP": "concept",
    "PRODUCT": "concept",
    "WORK_OF_ART": "concept",
    "LAW": "concept",
    "LANGUAGE": "concept",
}


@dataclass
class Extracted:
    name: str          # canonical form (first observed spelling)
    node_type: str
    source: str        # 'ner' | 'chunk'
    fact_ids: list[str]


def _extract_from_text(text: str) -> list[tuple[str, str, str]]:
    """Return list of (name, node_type, source) from one fact text."""
    _load()
    doc = _nlp(text)
    out: dict[str, tuple[str, str]] = {}  # name -> (node_type, source)

    # 1. NER
    for ent in doc.ents:
        name = ent.text.strip()
        if not name:
            continue
        ntype = _NER_TYPE_MAP.get(ent.label_, "concept")
        out[name] = (ntype, "ner")

    # 2. Noun chunks (stripped of determiners/pronouns)
    for chunk in doc.noun_chunks:
        tokens = [t for t in chunk if t.pos_ not in STRIP_POS]
        if not tokens:
            continue
        name = " ".join(t.text for t in tokens).strip()
        if name and name not in out:
            out[name] = ("concept", "chunk")

    # 3. Filter junk
    filtered: list[tuple[str, str, str]] = []
    for name, (ntype, source) in out.items():
        words = name.lower().split()
        if not words:
            continue
        if all(w in FILLER_WORDS for w in words):
            continue
        if len(words) == 1 and words[0] in FILLER_WORDS:
            continue
        if len(name) < 2:
            continue
        if name.replace(" ", "").replace(".", "").replace(",", "").isdigit():
            continue
        filtered.append((name, ntype, source))

    return filtered


def extract_from_facts(
    facts: list[dict],
    *,
    min_mentions: int = 1,
) -> tuple[list[Extracted], dict[str, int]]:
    """Run spaCy over each fact's content; group mentions by normalized name.

    Returns (extracted_list, stats).
    min_mentions: drop names that appear in fewer than this many facts.
    """
    groups: dict[str, Extracted] = {}  # lowercased -> Extracted
    counts = {"facts": 0, "mentions": 0, "unique_names": 0, "ner": 0, "chunk": 0}

    for f in facts:
        fid = str(f.get("id", ""))
        content = str(f.get("content", ""))
        if not content.strip():
            continue
        counts["facts"] += 1
        mentions = _extract_from_text(content)
        for name, ntype, source in mentions:
            key = name.strip().lower()
            counts["mentions"] += 1
            counts[source] += 1
            g = groups.get(key)
            if g is None:
                groups[key] = Extracted(
                    name=name,
                    node_type=ntype,
                    source=source,
                    fact_ids=[fid],
                )
            else:
                if fid not in g.fact_ids:
                    g.fact_ids.append(fid)

    extracted = [g for g in groups.values() if len(g.fact_ids) >= min_mentions]
    counts["unique_names"] = len(extracted)
    return extracted, counts
