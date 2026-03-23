"""Experiment: Compare 5 entity extraction strategies to prevent cross-contamination.

Problem: The LLM entity extraction tags entities (orgs, persons) to facts that
don't mention them at all. E.g. "Nature" gets linked to 2735 facts but only ~15
actually mention it. The LLM cross-contaminates across facts in the same batch.

This experiment tests 5 strategies on the same set of facts and measures:
- Precision: % of entity-fact links where the entity is actually mentioned
- Recall: % of genuine entity mentions that are captured
- Entity count: how many distinct entities are extracted
- Cost: relative number of LLM calls

Run:
    uv run --project libs/kt-facts python experiments/entity_extraction_strategies.py

Requires: OPENROUTER_API_KEY in .env
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from kt_models.gateway import ModelGateway


# ── Test data: 15 real facts from the DB ────────────────────────────────
# Mix of facts that DO mention entities and ones that DON'T.
# Ground truth entities are annotated per fact.

@dataclass
class TestFact:
    idx: int
    content: str
    # Ground truth: entities that ARE mentioned in this fact
    expected_entities: list[str]
    # Ground truth: node_type for each expected entity
    expected_types: list[str]


TEST_FACTS = [
    TestFact(1,
        "Albert Einstein developed the theory of general relativity in 1915.",
        ["Albert Einstein"], ["entity"]),
    TestFact(2,
        "NASA launched Apollo 11 in 1969, landing humans on the Moon for the first time.",
        ["NASA", "Apollo 11"], ["entity", "event"]),
    TestFact(3,
        "Randomized controlled trials are generally regarded as the gold standard of study designs to determine causality.",
        [], []),
    TestFact(4,
        "Adams KE, Cohen MH, Eisenberg D, and Jonsen AR authored 'Ethical considerations of complementary and alternative medical therapies in conventional medical settings', published in the Annals of Internal Medicine in 2002.",
        ["Annals of Internal Medicine"], ["entity"]),
    TestFact(5,
        "The placebo effect is one of the most misunderstood phenomena in modern medicine.",
        [], []),
    TestFact(6,
        "The World Health Organization published the WHO Traditional Medicine Strategy 2014-2023 to support member states.",
        ["World Health Organization"], ["entity"]),
    TestFact(7,
        "Acupuncture involves the insertion of thin needles through the skin at specific points on the body.",
        [], []),
    TestFact(8,
        "A study published in Nature found that placebo response rates varied significantly across psychiatric conditions.",
        ["Nature"], ["entity"]),
    TestFact(9,
        "Chelation therapy is categorized as having medium risk and medium cost in the cost-risk assessment for CAM therapies.",
        [], []),
    TestFact(10,
        "Jennifer Doudna and Emmanuelle Charpentier developed the CRISPR-Cas9 gene editing technology.",
        ["Jennifer Doudna", "Emmanuelle Charpentier"], ["entity", "entity"]),
    TestFact(11,
        "The 2008 financial crisis led to widespread reforms in banking regulation across the European Union.",
        ["European Union", "2008 financial crisis"], ["entity", "event"]),
    TestFact(12,
        "Some medical treatments improve clinical outcomes but operate primarily through placebo responses.",
        [], []),
    TestFact(13,
        "Harvard University conducted a comprehensive study on integrative medicine approaches.",
        ["Harvard University"], ["entity"]),
    TestFact(14,
        "Most modern osteopaths do not use manipulation as the primary method of treatment, instead relying on the same drugs and surgery used by medical doctors.",
        [], []),
    TestFact(15,
        "The T-cell receptor is a heterodimer composed of an alpha chain and a beta chain, each containing a constant region and a variable region.",
        [], []),
]


@dataclass
class ExtractionResult:
    """Result of an entity extraction experiment."""
    strategy_name: str
    # entity_name -> list of fact indices it was linked to
    entity_facts: dict[str, list[int]] = field(default_factory=dict)
    # entity_name -> node_type
    entity_types: dict[str, str] = field(default_factory=dict)
    elapsed_seconds: float = 0.0
    llm_calls: int = 0


@dataclass
class ScoreCard:
    strategy_name: str
    true_positives: int = 0   # entity-fact link where entity IS in fact
    false_positives: int = 0  # entity-fact link where entity is NOT in fact
    false_negatives: int = 0  # entity IS in fact but not extracted
    total_entities: int = 0
    llm_calls: int = 0
    elapsed_seconds: float = 0.0

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


def _is_entity_in_fact(entity_name: str, fact_content: str) -> bool:
    """Check if entity is genuinely mentioned in the fact text."""
    content_lower = fact_content.lower()
    if entity_name.lower() in content_lower:
        return True
    # Check surname
    tokens = entity_name.split()
    if len(tokens) >= 2:
        surname = tokens[-1].lower()
        if len(surname) >= 3 and surname in content_lower:
            return True
    return False


def _score_result(result: ExtractionResult) -> ScoreCard:
    """Score an extraction result against ground truth."""
    card = ScoreCard(
        strategy_name=result.strategy_name,
        total_entities=len(result.entity_facts),
        llm_calls=result.llm_calls,
        elapsed_seconds=result.elapsed_seconds,
    )

    fact_map = {f.idx: f for f in TEST_FACTS}

    # Check each entity-fact link
    for entity_name, fact_indices in result.entity_facts.items():
        etype = result.entity_types.get(entity_name, "concept")
        if etype != "entity":
            continue  # only score entity-type nodes (the problem area)

        for fact_idx in fact_indices:
            fact = fact_map.get(fact_idx)
            if fact is None:
                continue
            if _is_entity_in_fact(entity_name, fact.content):
                card.true_positives += 1
            else:
                card.false_positives += 1

    # Count false negatives (expected entities not extracted)
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


# ── Strategy 1: Current approach (batch all facts, per-fact JSON) ───────

CURRENT_SYSTEM = """\
You are a knowledge-graph entity extractor. You will receive a numbered list \
of facts. For EACH fact, identify the distinct entities mentioned in that \
specific fact.

Your response MUST be a JSON object mapping each fact number to its entities.

Node types:
- "entity" = persons or organizations capable of intent
- "concept" = abstract topics, ideas, techniques, publications
- "event" = something that happened at a specific time
- "location" = physical places

CRITICAL: For each fact, list ONLY entities that the fact's text explicitly \
mentions. Do NOT include an entity for a fact unless that fact's content \
references it.

Do NOT extract author names from citations (e.g. "Smith et al."). \
Do NOT extract journal names unless the fact is ABOUT the journal.

Respond with ONLY the JSON object. No markdown fences."""

CURRENT_USER = """\
Here are {count} facts:

{fact_list}

For EACH fact, list the entities/concepts it mentions. Use format:
{{"facts": {{"1": [{{"name": "...", "node_type": "...", "entity_subtype": "person|organization|other"}}], ...}}}}"""


async def strategy_current(gateway: ModelGateway) -> ExtractionResult:
    """Strategy 1: Current batch approach."""
    lines = [f"{f.idx}. {f.content}" for f in TEST_FACTS]
    user_msg = CURRENT_USER.format(count=len(TEST_FACTS), fact_list="\n".join(lines))

    t0 = time.time()
    result = await gateway.generate_json(
        model_id=gateway.decomposition_model,
        messages=[{"role": "user", "content": user_msg}],
        system_prompt=CURRENT_SYSTEM,
        temperature=0.0,
        max_tokens=16000,
    )
    elapsed = time.time() - t0

    return _parse_batch_result(result, "1_current_batch", elapsed, 1)


# ── Strategy 2: Individual fact extraction (one call per fact) ──────────

INDIVIDUAL_SYSTEM = """\
You are an entity extractor. You will receive ONE fact. Extract all entities \
(persons, organizations), concepts, events, and locations mentioned in it.

Do NOT extract author names from citations. Do NOT extract journal names \
unless the fact is explicitly about that journal.

Return JSON: {{"entities": [{{"name": "...", "node_type": "entity|concept|event|location", "entity_subtype": "person|organization|other"}}]}}

Respond with ONLY the JSON. No fences."""


async def strategy_individual(gateway: ModelGateway) -> ExtractionResult:
    """Strategy 2: One LLM call per fact — no cross-contamination possible."""
    result = ExtractionResult(strategy_name="2_individual_per_fact")
    t0 = time.time()

    async def extract_one(fact: TestFact) -> tuple[int, list[dict]]:
        r = await gateway.generate_json(
            model_id=gateway.decomposition_model,
            messages=[{"role": "user", "content": f"Fact: {fact.content}"}],
            system_prompt=INDIVIDUAL_SYSTEM,
            temperature=0.0,
            max_tokens=2000,
        )
        entities = r.get("entities", []) if isinstance(r, dict) else []
        return fact.idx, entities

    tasks = [extract_one(f) for f in TEST_FACTS]
    responses = await asyncio.gather(*tasks, return_exceptions=True)

    for resp in responses:
        if isinstance(resp, BaseException):
            continue
        fact_idx, entities = resp
        for ent in entities:
            if not isinstance(ent, dict):
                continue
            name = ent.get("name", "").strip()
            if not name:
                continue
            ntype = ent.get("node_type", "concept")
            result.entity_facts.setdefault(name, []).append(fact_idx)
            result.entity_types[name] = ntype

    result.elapsed_seconds = time.time() - t0
    result.llm_calls = len(TEST_FACTS)
    return result


# ── Strategy 3: Batch extract + post-validation pass ───────────────────

VALIDATE_SYSTEM = """\
You will receive an entity name and a fact. Answer: does this fact explicitly \
mention or reference this entity? Answer ONLY with JSON: {{"mentioned": true}} \
or {{"mentioned": false}}. Do NOT consider general topic relevance — only \
explicit mention."""


async def strategy_batch_then_validate(gateway: ModelGateway) -> ExtractionResult:
    """Strategy 3: Batch extract, then validate each entity-fact link."""
    # Phase 1: batch extract (same as current)
    lines = [f"{f.idx}. {f.content}" for f in TEST_FACTS]
    user_msg = CURRENT_USER.format(count=len(TEST_FACTS), fact_list="\n".join(lines))

    t0 = time.time()
    raw = await gateway.generate_json(
        model_id=gateway.decomposition_model,
        messages=[{"role": "user", "content": user_msg}],
        system_prompt=CURRENT_SYSTEM,
        temperature=0.0,
        max_tokens=16000,
    )
    calls = 1

    # Parse initial results
    initial = _parse_batch_result(raw, "3_batch_then_validate", 0, 1)

    # Phase 2: validate entity-type links where entity not obviously in text
    fact_map = {f.idx: f for f in TEST_FACTS}
    validated = ExtractionResult(strategy_name="3_batch_then_validate")

    for entity_name, fact_indices in initial.entity_facts.items():
        etype = initial.entity_types.get(entity_name, "concept")
        validated.entity_types[entity_name] = etype

        if etype != "entity":
            # Keep non-entity links as-is (concepts are safe)
            validated.entity_facts[entity_name] = fact_indices
            continue

        kept = []
        for fidx in fact_indices:
            fact = fact_map.get(fidx)
            if fact is None:
                continue
            # Quick text check first
            if _is_entity_in_fact(entity_name, fact.content):
                kept.append(fidx)
                continue
            # LLM validation for ambiguous cases
            try:
                vr = await gateway.generate_json(
                    model_id=gateway.decomposition_model,
                    messages=[{"role": "user", "content": f'Entity: "{entity_name}"\nFact: "{fact.content}"'}],
                    system_prompt=VALIDATE_SYSTEM,
                    temperature=0.0,
                    max_tokens=100,
                )
                calls += 1
                if isinstance(vr, dict) and vr.get("mentioned", False):
                    kept.append(fidx)
            except Exception:
                pass

        if kept:
            validated.entity_facts[entity_name] = kept

    validated.elapsed_seconds = time.time() - t0
    validated.llm_calls = calls
    return validated


# ── Strategy 4: Smaller batches (5 facts each) ─────────────────────────

async def strategy_small_batches(gateway: ModelGateway) -> ExtractionResult:
    """Strategy 4: Small batches of 5 facts to reduce cross-contamination window."""
    result = ExtractionResult(strategy_name="4_small_batches")
    t0 = time.time()
    calls = 0

    batch_size = 5
    for i in range(0, len(TEST_FACTS), batch_size):
        batch = TEST_FACTS[i:i + batch_size]
        lines = [f"{f.idx}. {f.content}" for f in batch]
        user_msg = CURRENT_USER.format(count=len(batch), fact_list="\n".join(lines))

        raw = await gateway.generate_json(
            model_id=gateway.decomposition_model,
            messages=[{"role": "user", "content": user_msg}],
            system_prompt=CURRENT_SYSTEM,
            temperature=0.0,
            max_tokens=8000,
        )
        calls += 1

        batch_result = _parse_batch_result(raw, "", 0, 0)
        for name, indices in batch_result.entity_facts.items():
            result.entity_facts.setdefault(name, []).extend(indices)
            result.entity_types[name] = batch_result.entity_types.get(name, "concept")

    result.elapsed_seconds = time.time() - t0
    result.llm_calls = calls
    return result


# ── Strategy 5: Strict grounding prompt ─────────────────────────────────

STRICT_SYSTEM = """\
You are a precision entity extractor for a knowledge graph. You receive numbered facts.

For EACH fact, extract entities ONLY if their name (or a clear abbreviation/alias) \
appears as a SUBSTRING in the fact's text.

STRICT RULE: If you cannot find the entity's name or a known abbreviation literally \
written in the fact text, do NOT list it for that fact. "Relevant to the topic" is \
NOT sufficient — the name must be PRESENT IN THE TEXT.

Example:
- Fact: "NASA launched Apollo 11 in 1969" → entities: NASA, Apollo 11 ✓
- Fact: "The mission landed on the Moon" → entities: [] (no entity NAME appears) ✓
- Fact: "The mission landed on the Moon" → entities: NASA ✗ WRONG (NASA not in text)

Node types:
- "entity" = persons or organizations (set entity_subtype: person/organization/other)
- "concept" = abstract topics, ideas, techniques
- "event" = time-bound occurrences
- "location" = physical places

Do NOT extract: author names from citations, journal names from citations, DOIs.

Return JSON: {{"facts": {{"1": [...], "2": [...], ...}}}}
Only the JSON, no fences."""


async def strategy_strict_grounding(gateway: ModelGateway) -> ExtractionResult:
    """Strategy 5: Strict grounding prompt — entity name must be substring of fact."""
    lines = [f"{f.idx}. {f.content}" for f in TEST_FACTS]
    user_msg = CURRENT_USER.format(count=len(TEST_FACTS), fact_list="\n".join(lines))

    t0 = time.time()
    result = await gateway.generate_json(
        model_id=gateway.decomposition_model,
        messages=[{"role": "user", "content": user_msg}],
        system_prompt=STRICT_SYSTEM,
        temperature=0.0,
        max_tokens=16000,
    )
    elapsed = time.time() - t0

    return _parse_batch_result(result, "5_strict_grounding", elapsed, 1)


# ── Helpers ─────────────────────────────────────────────────────────────

def _parse_batch_result(
    raw: dict | None,
    strategy_name: str,
    elapsed: float,
    calls: int,
) -> ExtractionResult:
    result = ExtractionResult(
        strategy_name=strategy_name,
        elapsed_seconds=elapsed,
        llm_calls=calls,
    )
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
            result.entity_facts.setdefault(name, []).append(fact_idx)
            result.entity_types[name] = ntype

    return result


def _print_sep(title: str) -> None:
    print(f"\n{'='*70}")
    print(f"  {title}")
    print(f"{'='*70}")


async def main():
    _print_sep("ENTITY EXTRACTION STRATEGY COMPARISON")
    print(f"\nTest: {len(TEST_FACTS)} facts, ground truth annotated")
    print("Measuring: precision (no false links), recall (find real entities)")

    gateway = ModelGateway()
    model = gateway.decomposition_model
    print(f"Model: {model}\n")

    strategies = [
        ("1. Current batch approach", strategy_current),
        ("2. Individual per-fact", strategy_individual),
        ("3. Batch + LLM validation", strategy_batch_then_validate),
        ("4. Small batches (5 facts)", strategy_small_batches),
        ("5. Strict grounding prompt", strategy_strict_grounding),
    ]

    scores: list[ScoreCard] = []
    for name, fn in strategies:
        _print_sep(name)
        try:
            result = await fn(gateway)
            card = _score_result(result)
            scores.append(card)

            # Print entity details
            for ent_name, indices in sorted(result.entity_facts.items()):
                etype = result.entity_types.get(ent_name, "?")
                if etype != "entity":
                    continue
                fact_map = {f.idx: f for f in TEST_FACTS}
                correct = sum(1 for i in indices if _is_entity_in_fact(ent_name, fact_map.get(i, TestFact(0, "", [], [])).content))
                marker = "✓" if correct == len(indices) else f"✗ ({correct}/{len(indices)} correct)"
                print(f"  [{etype}] {ent_name}: facts {indices} {marker}")

            print(f"\n  Precision: {card.precision:.1%}  Recall: {card.recall:.1%}  F1: {card.f1:.1%}")
            print(f"  TP={card.true_positives} FP={card.false_positives} FN={card.false_negatives}")
            print(f"  Entities: {card.total_entities}  LLM calls: {card.llm_calls}  Time: {card.elapsed_seconds:.1f}s")
        except Exception as e:
            print(f"  ERROR: {e}")

    # ── Summary table ───────────────────────────────────────────────
    _print_sep("SUMMARY")
    print(f"{'Strategy':<30} {'Prec':>6} {'Recall':>6} {'F1':>6} {'FP':>4} {'Calls':>5} {'Time':>6}")
    print("-" * 70)
    for card in scores:
        print(f"{card.strategy_name:<30} {card.precision:>5.1%} {card.recall:>5.1%} {card.f1:>5.1%} {card.false_positives:>4} {card.llm_calls:>5} {card.elapsed_seconds:>5.1f}s")

    # Recommend best
    best = max(scores, key=lambda c: (c.f1, -c.llm_calls)) if scores else None
    if best:
        print(f"\nBest strategy: {best.strategy_name} (F1={best.f1:.1%}, {best.llm_calls} calls)")


if __name__ == "__main__":
    asyncio.run(main())
