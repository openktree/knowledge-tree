"""Experiment: spaCy NER + noun chunks vs LLM entity extraction.

Compares exhaustive spaCy extraction (NER + grammatical noun chunks)
against the current LLM approach. Measures precision, recall, F1,
and token usage/cost.

Run:
    uv run --project libs/kt-facts python experiments/spacy_vs_llm_extraction.py

Requires: OPENROUTER_API_KEY in .env, spacy + en_core_web_lg installed,
          write-db running (for DB facts).
"""

from __future__ import annotations

import asyncio
import html as html_module
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

import spacy

from kt_models.gateway import ModelGateway
from kt_models.usage import UsageAccumulator, start_usage_tracking, stop_usage_tracking

# ── Load spaCy model ─────────────────────────────────────────────────

nlp = spacy.load("en_core_web_lg")

# ── Test data: hardcoded facts with ground truth ─────────────────────
# Same set as entity_extraction_strategies.py for comparability.


@dataclass
class TestFact:
    idx: int
    content: str
    expected_entities: list[str]  # ground truth entity names
    expected_types: list[str]  # node_type per expected entity


TEST_FACTS = [
    TestFact(1, "Albert Einstein developed the theory of general relativity in 1915.", ["Albert Einstein"], ["entity"]),
    TestFact(
        2,
        "NASA launched Apollo 11 in 1969, landing humans on the Moon for the first time.",
        ["NASA", "Apollo 11"],
        ["entity", "event"],
    ),
    TestFact(
        3,
        "Randomized controlled trials are generally regarded as the gold standard of study designs to determine causality.",
        [],
        [],
    ),
    TestFact(
        4,
        "Adams KE, Cohen MH, Eisenberg D, and Jonsen AR authored 'Ethical considerations of complementary and alternative medical therapies in conventional medical settings', published in the Annals of Internal Medicine in 2002.",
        ["Annals of Internal Medicine"],
        ["entity"],
    ),
    TestFact(5, "The placebo effect is one of the most misunderstood phenomena in modern medicine.", [], []),
    TestFact(
        6,
        "The World Health Organization published the WHO Traditional Medicine Strategy 2014-2023 to support member states.",
        ["World Health Organization"],
        ["entity"],
    ),
    TestFact(
        7, "Acupuncture involves the insertion of thin needles through the skin at specific points on the body.", [], []
    ),
    TestFact(
        8,
        "A study published in Nature found that placebo response rates varied significantly across psychiatric conditions.",
        ["Nature"],
        ["entity"],
    ),
    TestFact(
        9,
        "Chelation therapy is categorized as having medium risk and medium cost in the cost-risk assessment for CAM therapies.",
        [],
        [],
    ),
    TestFact(
        10,
        "Jennifer Doudna and Emmanuelle Charpentier developed the CRISPR-Cas9 gene editing technology.",
        ["Jennifer Doudna", "Emmanuelle Charpentier"],
        ["entity", "entity"],
    ),
    TestFact(
        11,
        "The 2008 financial crisis led to widespread reforms in banking regulation across the European Union.",
        ["European Union", "2008 financial crisis"],
        ["entity", "event"],
    ),
    TestFact(
        12, "Some medical treatments improve clinical outcomes but operate primarily through placebo responses.", [], []
    ),
    TestFact(
        13,
        "Harvard University conducted a comprehensive study on integrative medicine approaches.",
        ["Harvard University"],
        ["entity"],
    ),
    TestFact(
        14,
        "Most modern osteopaths do not use manipulation as the primary method of treatment, instead relying on the same drugs and surgery used by medical doctors.",
        [],
        [],
    ),
    TestFact(
        15,
        "The T-cell receptor is a heterodimer composed of an alpha chain and a beta chain, each containing a constant region and a variable region.",
        [],
        [],
    ),
]


# ── Result / scoring data classes ────────────────────────────────────


@dataclass
class TokenUsage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float = 0.0

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


@dataclass
class ExtractionResult:
    strategy_name: str
    # entity_name -> list of fact indices it was linked to
    entity_facts: dict[str, list[int]] = field(default_factory=dict)
    # entity_name -> node_type
    entity_types: dict[str, str] = field(default_factory=dict)
    elapsed_seconds: float = 0.0
    llm_calls: int = 0
    tokens: TokenUsage = field(default_factory=TokenUsage)


@dataclass
class ScoreCard:
    strategy_name: str
    true_positives: int = 0
    false_positives: int = 0
    false_negatives: int = 0
    total_entities: int = 0
    llm_calls: int = 0
    elapsed_seconds: float = 0.0
    tokens: TokenUsage = field(default_factory=TokenUsage)

    @property
    def precision(self) -> float:
        total = self.true_positives + self.false_positives
        return self.true_positives / total if total > 0 else 0.0

    @property
    def recall(self) -> float:
        total = self.true_positives + self.false_negatives
        return self.true_positives / total if total > 0 else 0.0

    @property
    def f1(self) -> float:
        p, r = self.precision, self.recall
        return 2 * p * r / (p + r) if (p + r) > 0 else 0.0


# ── Helpers ──────────────────────────────────────────────────────────


def _is_entity_in_fact(entity_name: str, fact_content: str) -> bool:
    """Check if entity is genuinely mentioned in the fact text."""
    content_lower = fact_content.lower()
    if entity_name.lower() in content_lower:
        return True
    tokens = entity_name.split()
    if len(tokens) >= 2:
        surname = tokens[-1].lower()
        if len(surname) >= 3 and surname in content_lower:
            return True
    return False


def _score_result(result: ExtractionResult) -> ScoreCard:
    card = ScoreCard(
        strategy_name=result.strategy_name,
        total_entities=len(result.entity_facts),
        llm_calls=result.llm_calls,
        elapsed_seconds=result.elapsed_seconds,
        tokens=result.tokens,
    )

    fact_map = {f.idx: f for f in TEST_FACTS}

    for entity_name, fact_indices in result.entity_facts.items():
        etype = result.entity_types.get(entity_name, "concept")
        if etype != "entity":
            continue
        for fact_idx in fact_indices:
            fact = fact_map.get(fact_idx)
            if fact is None:
                continue
            if _is_entity_in_fact(entity_name, fact.content):
                card.true_positives += 1
            else:
                card.false_positives += 1

    for fact in TEST_FACTS:
        for expected_ent, expected_type in zip(fact.expected_entities, fact.expected_types):
            if expected_type != "entity":
                continue
            found = False
            for entity_name, fact_indices in result.entity_facts.items():
                etype = result.entity_types.get(entity_name, "concept")
                if etype != "entity":
                    continue
                if fact.idx in fact_indices and _is_entity_in_fact(entity_name, fact.content):
                    found = True
                    break
            if not found:
                card.false_negatives += 1

    return card


def _usage_from_accumulator(acc: UsageAccumulator | None) -> TokenUsage:
    if acc is None:
        return TokenUsage()
    return TokenUsage(
        prompt_tokens=acc.total_prompt_tokens,
        completion_tokens=acc.total_completion_tokens,
        cost_usd=acc.total_cost_usd,
    )


def _print_sep(title: str) -> None:
    print(f"\n{'=' * 80}")
    print(f"  {title}")
    print(f"{'=' * 80}")


# ── Filler word filter for spaCy ─────────────────────────────────────

FILLER_WORDS = {
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
    # Question/relative words
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
    # Common verbs that slip through
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

# POS tags to strip from the start of noun chunks
STRIP_POS = {"DET", "PRON", "ADP", "CCONJ", "SCONJ", "PART", "AUX", "PUNCT"}


def _extract_spacy_entities(text: str) -> list[tuple[str, str]]:
    """Extract entities from text using spaCy NER + noun chunks.

    Returns list of (name, source) where source is 'ner' or 'chunk'.
    Filters out filler words and short junk.
    """
    doc = nlp(text)
    candidates: dict[str, str] = {}  # name -> source

    # 1. Named entities from NER
    for ent in doc.ents:
        name = ent.text.strip()
        if name:
            candidates[name] = "ner"

    # 2. Noun chunks — strip leading determiners/pronouns/prepositions
    for chunk in doc.noun_chunks:
        tokens = [t for t in chunk if t.pos_ not in STRIP_POS]
        if tokens:
            name = " ".join(t.text for t in tokens).strip()
            if name and name not in candidates:
                candidates[name] = "chunk"

    # 3. Filter out filler
    filtered: list[tuple[str, str]] = []
    for name, source in candidates.items():
        words = name.lower().split()
        if not words:
            continue
        # Skip if all words are filler
        if all(w in FILLER_WORDS for w in words):
            continue
        # Skip single-word filler
        if len(words) == 1 and words[0] in FILLER_WORDS:
            continue
        # Skip very short
        if len(name) < 2:
            continue
        # Skip pure numbers
        if name.replace(" ", "").replace(".", "").replace(",", "").isdigit():
            continue
        filtered.append((name, source))

    return filtered


# spaCy NER label -> our node_type mapping
_NER_TYPE_MAP = {
    "PERSON": "entity",
    "ORG": "entity",
    "GPE": "location",
    "LOC": "location",
    "FAC": "location",
    "EVENT": "event",
    "DATE": "event",
    "NORP": "concept",  # nationalities, religious/political groups
    "PRODUCT": "concept",
    "WORK_OF_ART": "concept",
    "LAW": "concept",
    "LANGUAGE": "concept",
}


def _spacy_node_type(name: str, text: str) -> str:
    """Determine node_type for a spaCy-extracted entity by re-checking NER labels."""
    doc = nlp(text)
    for ent in doc.ents:
        if ent.text.strip().lower() == name.lower():
            return _NER_TYPE_MAP.get(ent.label_, "concept")
    return "concept"  # noun chunks default to concept


# ── Strategy 1: LLM (current approach) ──────────────────────────────

LLM_SYSTEM = """\
You are a precision entity extractor for a knowledge graph. You receive numbered facts.

For EACH fact, extract entities ONLY if their name (or a clear abbreviation/alias) \
appears as a SUBSTRING in the fact's text.

STRICT RULE: If you cannot find the entity's name literally written in the fact \
text, do NOT list it for that fact.

Node types:
- "entity" = persons or organizations (set entity_subtype: person/organization/other)
- "concept" = abstract topics, ideas, techniques
- "event" = time-bound occurrences
- "location" = physical places

Do NOT extract: author names from citations, journal names from citations, DOIs.

Return JSON: {{"facts": {{"1": [{{"name": "...", "node_type": "...", "entity_subtype": "person|organization|other"}}], ...}}}}
Only the JSON, no fences."""

LLM_USER = """\
Here are {count} facts:

{fact_list}

For EACH fact, list the entities/concepts/events/locations it mentions."""


def _parse_batch_result(raw: dict | None, strategy_name: str) -> ExtractionResult:
    result = ExtractionResult(strategy_name=strategy_name)
    if not raw or not isinstance(raw, dict):
        return result
    facts_data = raw.get("facts", {})
    if not isinstance(facts_data, dict):
        return result
    for fact_key, entities in facts_data.items():
        try:
            fact_idx = int(fact_key)
        except (ValueError, TypeError):
            continue
        if not isinstance(entities, list):
            continue
        for ent in entities:
            if not isinstance(ent, dict):
                continue
            name = ent.get("name", "").strip()
            if not name:
                continue
            ntype = ent.get("node_type", "concept")
            if ntype not in ("concept", "entity", "event", "location"):
                ntype = "concept"
            result.entity_facts.setdefault(name, []).append(fact_idx)
            result.entity_types[name] = ntype
    return result


async def strategy_llm(facts: list[TestFact], gateway: ModelGateway) -> ExtractionResult:
    """Strategy 1: LLM batch extraction (current approach)."""
    lines = [f"{f.idx}. {f.content}" for f in facts]
    user_msg = LLM_USER.format(count=len(facts), fact_list="\n".join(lines))

    start_usage_tracking()
    t0 = time.time()
    raw = await gateway.generate_json(
        model_id=gateway.decomposition_model,
        messages=[{"role": "user", "content": user_msg}],
        system_prompt=LLM_SYSTEM,
        temperature=0.0,
        max_tokens=16000,
    )
    elapsed = time.time() - t0
    acc = stop_usage_tracking()

    result = _parse_batch_result(raw, "1_llm_current")
    result.elapsed_seconds = elapsed
    result.llm_calls = 1
    result.tokens = _usage_from_accumulator(acc)
    return result


# ── Strategy 2: spaCy NER + noun chunks ─────────────────────────────


async def strategy_spacy(facts: list[TestFact], _gateway: ModelGateway) -> ExtractionResult:
    """Strategy 2: spaCy NER + grammatical noun chunks, zero LLM cost."""
    result = ExtractionResult(strategy_name="2_spacy_ner_chunks")
    t0 = time.time()

    for fact in facts:
        extracted = _extract_spacy_entities(fact.content)
        for name, _source in extracted:
            ntype = _spacy_node_type(name, fact.content)
            result.entity_facts.setdefault(name, []).append(fact.idx)
            result.entity_types[name] = ntype

    result.elapsed_seconds = time.time() - t0
    result.llm_calls = 0
    # tokens stay at 0
    return result


# ── Strategy 3: spaCy candidates -> LLM classify/filter ─────────────

HYBRID_SYSTEM = """\
You are an entity classifier for a knowledge graph. You will receive a fact and \
a list of candidate entity names extracted from that fact by NLP.

For each candidate, decide:
1. Is it a meaningful entity/concept worth tracking in a knowledge graph? (yes/no)
2. What is its node_type: "entity" (person/org), "concept", "event", or "location"?

Filter OUT: generic nouns, filler phrases, overly broad terms, author citation names.
Keep: specific named entities, meaningful concepts, events, locations.

Return JSON: {{"entities": [{{"name": "...", "keep": true/false, "node_type": "..."}}]}}
Only the JSON, no fences."""


async def strategy_hybrid(facts: list[TestFact], gateway: ModelGateway) -> ExtractionResult:
    """Strategy 3: spaCy for candidate generation, LLM for classification."""
    result = ExtractionResult(strategy_name="3_spacy_then_llm")
    t0 = time.time()
    calls = 0

    start_usage_tracking()

    for fact in facts:
        # Phase 1: spaCy extracts candidates (free)
        extracted = _extract_spacy_entities(fact.content)
        if not extracted:
            continue

        candidate_names = [name for name, _ in extracted]

        # Phase 2: LLM classifies/filters
        user_msg = (
            f'Fact: "{fact.content}"\n\n'
            f"Candidates: {candidate_names}\n\n"
            "For each candidate, decide if it's worth keeping and classify its type."
        )
        try:
            raw = await gateway.generate_json(
                model_id=gateway.decomposition_model,
                messages=[{"role": "user", "content": user_msg}],
                system_prompt=HYBRID_SYSTEM,
                temperature=0.0,
                max_tokens=4000,
            )
            calls += 1

            if isinstance(raw, dict):
                for ent in raw.get("entities", []):
                    if not isinstance(ent, dict):
                        continue
                    if not ent.get("keep", False):
                        continue
                    name = ent.get("name", "").strip()
                    if not name:
                        continue
                    ntype = ent.get("node_type", "concept")
                    if ntype not in ("concept", "entity", "event", "location"):
                        ntype = "concept"
                    result.entity_facts.setdefault(name, []).append(fact.idx)
                    result.entity_types[name] = ntype
        except Exception as e:
            print(f"    Hybrid LLM call failed for fact {fact.idx}: {e}")

    acc = stop_usage_tracking()
    result.elapsed_seconds = time.time() - t0
    result.llm_calls = calls
    result.tokens = _usage_from_accumulator(acc)
    return result


# ── DB fact loader ───────────────────────────────────────────────────


async def load_db_facts(n: int = 50) -> list[TestFact]:
    """Pull N random ready facts from write-db and wrap as TestFact (no ground truth)."""
    from sqlalchemy import func, select

    from kt_db.session import get_write_session_factory
    from kt_db.write_models import WriteFact

    factory = get_write_session_factory(application_name="spacy_experiment")
    async with factory() as session:
        stmt = select(WriteFact).where(WriteFact.dedup_status == "ready").order_by(func.random()).limit(n)
        result = await session.execute(stmt)
        rows = list(result.scalars().all())

    # Wrap as TestFact with empty ground truth (can't score, but can compare counts)
    return [
        TestFact(idx=i + 1, content=row.content, expected_entities=[], expected_types=[]) for i, row in enumerate(rows)
    ]


# ── HTML report generator ────────────────────────────────────────────

_TYPE_COLORS = {
    "entity": "#2563eb",
    "concept": "#7c3aed",
    "event": "#dc2626",
    "location": "#059669",
}


def _esc(s: str) -> str:
    return html_module.escape(s)


def _badge(name: str, ntype: str, matched: bool | None = None) -> str:
    """Render a colored badge for an extracted entity.

    matched=True  -> green border (correct match)
    matched=False -> red border (false positive)
    matched=None  -> no border (no ground truth)
    """
    bg = _TYPE_COLORS.get(ntype, "#6b7280")
    border = ""
    if matched is True:
        border = "border:2px solid #22c55e;"
    elif matched is False:
        border = "border:2px solid #ef4444;"
    return (
        f'<span style="display:inline-block;padding:2px 8px;margin:2px;'
        f"border-radius:12px;font-size:0.8em;color:#fff;"
        f'background:{bg};{border}">'
        f"{_esc(name)}"
        f'<span style="opacity:0.7;font-size:0.8em;margin-left:4px">{ntype}</span>'
        f"</span>"
    )


def generate_html_report(
    annotated_facts: list[TestFact],
    annotated_results: list[ExtractionResult],
    annotated_scores: list[ScoreCard],
    db_facts: list[TestFact] | None,
    db_results: list[ExtractionResult] | None,
    model_name: str,
    output_path: Path,
) -> None:
    """Generate a self-contained HTML report with per-fact strategy comparison."""

    strategy_names = [r.strategy_name for r in annotated_results]
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    parts: list[str] = []
    parts.append(f"""\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>spaCy vs LLM Entity Extraction Report</title>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
         max-width: 1400px; margin: 0 auto; padding: 20px; background: #f8fafc; color: #1e293b; }}
  h1 {{ font-size: 1.6em; margin-bottom: 4px; }}
  h2 {{ font-size: 1.3em; margin: 30px 0 12px; border-bottom: 2px solid #e2e8f0; padding-bottom: 6px; }}
  h3 {{ font-size: 1.1em; margin: 20px 0 8px; }}
  .meta {{ color: #64748b; font-size: 0.9em; margin-bottom: 20px; }}
  table {{ border-collapse: collapse; width: 100%; margin: 12px 0; }}
  th, td {{ border: 1px solid #e2e8f0; padding: 8px 12px; text-align: left; font-size: 0.9em; }}
  th {{ background: #f1f5f9; font-weight: 600; }}
  tr:hover {{ background: #f8fafc; }}
  .num {{ text-align: right; font-variant-numeric: tabular-nums; }}
  .good {{ color: #16a34a; font-weight: 600; }}
  .bad {{ color: #dc2626; font-weight: 600; }}
  .neutral {{ color: #64748b; }}
  .fact-row {{ background: #fff; }}
  .fact-text {{ font-size: 0.85em; color: #334155; max-width: 500px; }}
  .badges {{ min-width: 180px; }}
  .legend {{ display: flex; gap: 16px; flex-wrap: wrap; margin: 10px 0; }}
  .legend-item {{ display: flex; align-items: center; gap: 4px; font-size: 0.85em; }}
  .legend-dot {{ width: 12px; height: 12px; border-radius: 50%; display: inline-block; }}
  .section {{ background: #fff; border-radius: 8px; padding: 20px; margin: 16px 0; box-shadow: 0 1px 3px rgba(0,0,0,0.1); }}
  .highlight {{ background: #fefce8; }}
</style>
</head>
<body>
<h1>spaCy vs LLM Entity Extraction</h1>
<div class="meta">Generated {now} | Model: {_esc(model_name)} | {len(annotated_facts)} annotated facts</div>
""")

    # Legend
    parts.append('<div class="legend">')
    for ntype, color in _TYPE_COLORS.items():
        parts.append(
            f'<div class="legend-item"><span class="legend-dot" style="background:{color}"></span> {ntype}</div>'
        )
    parts.append(
        '<div class="legend-item"><span class="legend-dot" style="background:#fff;border:2px solid #22c55e"></span> correct match</div>'
    )
    parts.append(
        '<div class="legend-item"><span class="legend-dot" style="background:#fff;border:2px solid #ef4444"></span> false positive</div>'
    )
    parts.append("</div>")

    # ── Summary table ────────────────────────────────────────────────
    parts.append('<div class="section">')
    parts.append("<h2>Summary</h2>")
    parts.append("<table>")
    parts.append(
        "<tr><th>Strategy</th><th class='num'>Precision</th><th class='num'>Recall</th>"
        "<th class='num'>F1</th><th class='num'>TP</th><th class='num'>FP</th>"
        "<th class='num'>FN</th><th class='num'>Items</th><th class='num'>LLM Calls</th>"
        "<th class='num'>Time</th><th class='num'>Prompt Tok</th>"
        "<th class='num'>Compl Tok</th><th class='num'>Cost</th></tr>"
    )
    for card in annotated_scores:
        f1_cls = "good" if card.f1 >= 0.95 else ("neutral" if card.f1 >= 0.80 else "bad")
        fp_cls = "good" if card.false_positives == 0 else "bad"
        cost_cls = "good" if card.tokens.cost_usd == 0 else "neutral"
        parts.append(
            f"<tr>"
            f"<td>{_esc(card.strategy_name)}</td>"
            f"<td class='num'>{card.precision:.1%}</td>"
            f"<td class='num'>{card.recall:.1%}</td>"
            f"<td class='num {f1_cls}'>{card.f1:.1%}</td>"
            f"<td class='num good'>{card.true_positives}</td>"
            f"<td class='num {fp_cls}'>{card.false_positives}</td>"
            f"<td class='num'>{card.false_negatives}</td>"
            f"<td class='num'>{card.total_entities}</td>"
            f"<td class='num'>{card.llm_calls}</td>"
            f"<td class='num'>{card.elapsed_seconds:.1f}s</td>"
            f"<td class='num'>{card.tokens.prompt_tokens:,}</td>"
            f"<td class='num'>{card.tokens.completion_tokens:,}</td>"
            f"<td class='num {cost_cls}'>${card.tokens.cost_usd:.5f}</td>"
            f"</tr>"
        )
    parts.append("</table></div>")

    # ── Per-fact detail table (annotated) ────────────────────────────
    parts.append('<div class="section">')
    parts.append("<h2>Per-Fact Breakdown (Annotated Facts)</h2>")
    parts.append("<table>")
    header = "<tr><th>#</th><th>Fact</th><th>Expected</th>"
    for sname in strategy_names:
        header += f"<th>{_esc(sname)}</th>"
    header += "</tr>"
    parts.append(header)

    for fact in annotated_facts:
        parts.append('<tr class="fact-row">')
        parts.append(f"<td class='num'>{fact.idx}</td>")
        parts.append(f"<td class='fact-text'>{_esc(fact.content)}</td>")

        # Expected column
        exp_badges = ""
        for ename, etype in zip(fact.expected_entities, fact.expected_types):
            exp_badges += _badge(ename, etype, matched=True)
        if not exp_badges:
            exp_badges = '<span class="neutral">-</span>'
        parts.append(f"<td class='badges'>{exp_badges}</td>")

        # Each strategy column
        for result in annotated_results:
            cell_badges = ""
            for ename, indices in sorted(result.entity_facts.items()):
                if fact.idx not in indices:
                    continue
                ntype = result.entity_types.get(ename, "concept")
                # Determine if this is a correct match for entity-type nodes
                if ntype == "entity":
                    matched = _is_entity_in_fact(ename, fact.content)
                else:
                    matched = None  # no ground truth scoring for non-entities
                cell_badges += _badge(ename, ntype, matched)
            if not cell_badges:
                cell_badges = '<span class="neutral">-</span>'
            parts.append(f"<td class='badges'>{cell_badges}</td>")

        parts.append("</tr>")

    parts.append("</table></div>")

    # ── Per-fact detail table (DB facts) ─────────────────────────────
    if db_facts and db_results:
        db_strategy_names = [r.strategy_name for r in db_results]

        # DB summary table
        parts.append('<div class="section">')
        parts.append(f"<h2>DB Facts Summary ({len(db_facts)} facts)</h2>")
        parts.append("<table>")
        parts.append(
            "<tr><th>Strategy</th><th class='num'>Items</th><th class='num'>Entities</th>"
            "<th class='num'>Concepts</th><th class='num'>Events/Loc</th>"
            "<th class='num'>LLM Calls</th><th class='num'>Time</th>"
            "<th class='num'>Tokens</th><th class='num'>Cost</th></tr>"
        )
        for r in db_results:
            entity_count = sum(1 for t in r.entity_types.values() if t == "entity")
            concept_count = sum(1 for t in r.entity_types.values() if t == "concept")
            event_count = sum(1 for t in r.entity_types.values() if t in ("event", "location"))
            parts.append(
                f"<tr>"
                f"<td>{_esc(r.strategy_name)}</td>"
                f"<td class='num'>{len(r.entity_facts)}</td>"
                f"<td class='num'>{entity_count}</td>"
                f"<td class='num'>{concept_count}</td>"
                f"<td class='num'>{event_count}</td>"
                f"<td class='num'>{r.llm_calls}</td>"
                f"<td class='num'>{r.elapsed_seconds:.1f}s</td>"
                f"<td class='num'>{r.tokens.total_tokens:,}</td>"
                f"<td class='num'>${r.tokens.cost_usd:.5f}</td>"
                f"</tr>"
            )
        parts.append("</table></div>")

        # Per-fact DB breakdown
        parts.append('<div class="section">')
        parts.append("<h2>Per-Fact Breakdown (DB Facts)</h2>")
        parts.append("<table>")
        header = "<tr><th>#</th><th>Fact</th>"
        for sname in db_strategy_names:
            header += f"<th>{_esc(sname)}</th>"
        header += "</tr>"
        parts.append(header)

        for fact in db_facts:
            parts.append('<tr class="fact-row">')
            parts.append(f"<td class='num'>{fact.idx}</td>")
            parts.append(f"<td class='fact-text'>{_esc(fact.content)}</td>")

            for result in db_results:
                cell_badges = ""
                for ename, indices in sorted(result.entity_facts.items()):
                    if fact.idx not in indices:
                        continue
                    ntype = result.entity_types.get(ename, "concept")
                    cell_badges += _badge(ename, ntype)
                if not cell_badges:
                    cell_badges = '<span class="neutral">-</span>'
                parts.append(f"<td class='badges'>{cell_badges}</td>")

            parts.append("</tr>")

        parts.append("</table></div>")

    parts.append("</body></html>")

    output_path.write_text("\n".join(parts), encoding="utf-8")
    print(f"\nHTML report written to: {output_path}")


# ── Main ─────────────────────────────────────────────────────────────


def _print_entity_details(result: ExtractionResult, facts: list[TestFact]) -> None:
    """Print extracted entities with correctness markers."""
    fact_map = {f.idx: f for f in facts}
    for ent_name, indices in sorted(result.entity_facts.items()):
        etype = result.entity_types.get(ent_name, "?")
        if etype != "entity":
            continue
        correct = sum(
            1 for i in indices if _is_entity_in_fact(ent_name, fact_map.get(i, TestFact(0, "", [], [])).content)
        )
        marker = "ok" if correct == len(indices) else f"{correct}/{len(indices)} correct"
        print(f"  [{etype}] {ent_name}: facts {indices} ({marker})")


def _print_all_extractions(result: ExtractionResult) -> None:
    """Print all extracted items (entities, concepts, events, locations)."""
    by_type: dict[str, list[str]] = {}
    for name, ntype in sorted(result.entity_types.items()):
        by_type.setdefault(ntype, []).append(name)
    for ntype in ("entity", "event", "location", "concept"):
        names = by_type.get(ntype, [])
        if names:
            print(f"  [{ntype}] ({len(names)}): {', '.join(sorted(names))}")


async def main() -> None:
    _print_sep("SPACY vs LLM ENTITY EXTRACTION EXPERIMENT")
    print(f"\nAnnotated test: {len(TEST_FACTS)} facts with ground truth")

    gateway = ModelGateway()
    model = gateway.decomposition_model
    print(f"LLM model: {model}")

    strategies: list[tuple[str, Any]] = [
        ("1. LLM (current)", strategy_llm),
        ("2. spaCy NER + noun chunks", strategy_spacy),
        ("3. spaCy + LLM hybrid", strategy_hybrid),
    ]

    # Collect results for HTML report
    annotated_results: list[ExtractionResult] = []

    # ── Part 1: Annotated test facts (with scoring) ──────────────────
    _print_sep("PART 1: ANNOTATED FACTS (precision/recall scoring)")

    scores: list[ScoreCard] = []
    for name, fn in strategies:
        _print_sep(name)
        try:
            result = await fn(TEST_FACTS, gateway)
            annotated_results.append(result)
            card = _score_result(result)
            scores.append(card)

            _print_entity_details(result, TEST_FACTS)
            print()
            _print_all_extractions(result)

            print(f"\n  Precision: {card.precision:.1%}  Recall: {card.recall:.1%}  F1: {card.f1:.1%}")
            print(f"  TP={card.true_positives} FP={card.false_positives} FN={card.false_negatives}")
            print(f"  Entities: {card.total_entities}  LLM calls: {card.llm_calls}  Time: {card.elapsed_seconds:.1f}s")
            print(
                f"  Tokens: prompt={card.tokens.prompt_tokens:,} completion={card.tokens.completion_tokens:,} total={card.tokens.total_tokens:,}"
            )
            print(f"  Cost: ${card.tokens.cost_usd:.6f}")
        except Exception as e:
            print(f"  ERROR: {e}")
            import traceback

            traceback.print_exc()

    # Summary table
    _print_sep("ANNOTATED FACTS — SUMMARY")
    header = f"{'Strategy':<28} {'Prec':>6} {'Rec':>6} {'F1':>6} {'FP':>4} {'Calls':>5} {'Time':>6} {'Prompt':>8} {'Compl':>8} {'Cost':>10}"
    print(header)
    print("-" * len(header))
    for card in scores:
        print(
            f"{card.strategy_name:<28} "
            f"{card.precision:>5.1%} {card.recall:>5.1%} {card.f1:>5.1%} "
            f"{card.false_positives:>4} {card.llm_calls:>5} {card.elapsed_seconds:>5.1f}s "
            f"{card.tokens.prompt_tokens:>8,} {card.tokens.completion_tokens:>8,} "
            f"${card.tokens.cost_usd:>8.5f}"
        )

    # ── Part 2: Real DB facts (extraction count comparison) ──────────
    _print_sep("PART 2: REAL DB FACTS (no ground truth, count comparison)")

    db_facts: list[TestFact] | None = None
    db_results_list: list[ExtractionResult] | None = None

    try:
        db_facts = await load_db_facts(50)
        print(f"\nLoaded {len(db_facts)} facts from write-db")
        if not db_facts:
            print("  No facts found in write-db. Skipping Part 2.")
        else:
            # Show a few sample facts
            print("\nSample facts:")
            for f in db_facts[:3]:
                print(f"  {f.idx}. {f.content[:120]}...")

            db_results_list = []
            for name, fn in strategies:
                _print_sep(f"DB FACTS — {name}")
                try:
                    result = await fn(db_facts, gateway)
                    db_results_list.append(result)

                    _print_all_extractions(result)
                    print(f"\n  Total extracted: {len(result.entity_facts)} items")
                    print(f"  LLM calls: {result.llm_calls}  Time: {result.elapsed_seconds:.1f}s")
                    print(
                        f"  Tokens: prompt={result.tokens.prompt_tokens:,} completion={result.tokens.completion_tokens:,} total={result.tokens.total_tokens:,}"
                    )
                    print(f"  Cost: ${result.tokens.cost_usd:.6f}")
                except Exception as e:
                    print(f"  ERROR: {e}")
                    import traceback

                    traceback.print_exc()

            # DB facts summary
            _print_sep("DB FACTS — SUMMARY")
            header = f"{'Strategy':<28} {'Items':>6} {'Entities':>8} {'Concepts':>8} {'Events':>6} {'Calls':>5} {'Time':>6} {'Tokens':>10} {'Cost':>10}"
            print(header)
            print("-" * len(header))
            for r in db_results_list:
                entity_count = sum(1 for t in r.entity_types.values() if t == "entity")
                concept_count = sum(1 for t in r.entity_types.values() if t == "concept")
                event_count = sum(1 for t in r.entity_types.values() if t in ("event", "location"))
                print(
                    f"{r.strategy_name:<28} "
                    f"{len(r.entity_facts):>6} {entity_count:>8} {concept_count:>8} {event_count:>6} "
                    f"{r.llm_calls:>5} {r.elapsed_seconds:>5.1f}s "
                    f"{r.tokens.total_tokens:>10,} "
                    f"${r.tokens.cost_usd:>8.5f}"
                )
    except Exception as e:
        print(f"\n  Could not load DB facts: {e}")
        print("  (Is write-db running? docker compose up -d postgres-write)")

    # ── Generate HTML report ─────────────────────────────────────────
    if annotated_results and scores:
        report_path = Path(__file__).resolve().parent / "spacy_vs_llm_report.html"
        generate_html_report(
            annotated_facts=TEST_FACTS,
            annotated_results=annotated_results,
            annotated_scores=scores,
            db_facts=db_facts if db_facts else None,
            db_results=db_results_list,
            model_name=model,
            output_path=report_path,
        )


if __name__ == "__main__":
    asyncio.run(main())
