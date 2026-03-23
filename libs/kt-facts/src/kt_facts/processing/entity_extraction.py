"""Entity extraction — exhaustively extracts nodes from facts via LLM.

Moved from worker-nodes gathering pipeline to kt-facts so that
entity extraction can run as a mandatory phase of decompose().

The LLM returns a **per-fact** mapping: for each fact number, it lists the
entities/concepts/events mentioned in that fact.  This structure forces the
LLM to re-state entity names per fact rather than listing long index arrays,
which dramatically reduces hallucinated cross-fact associations.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

from kt_models.gateway import ModelGateway

logger = logging.getLogger(__name__)


# ── Name validation ───────────────────────────────────────────────


def _is_valid_entity_name(name: str) -> bool:
    """Reject corrupted or hallucinated entity names.

    Returns False for:
    - Names too short (< 2 chars) or too long (> 150 chars)
    - Pure initials patterns (all tokens are single letters, e.g. "K. M. A.")
    - Repeated substring patterns (e.g. "K. M. A. M. H." repeated 3+ times)
    - Citation artifacts containing "et al."
    - Names where < 40% of characters are alphabetic
    """
    if not name or len(name) < 2 or len(name) > 150:
        return False

    # Reject "et al." citation artifacts
    if "et al" in name.lower():
        return False

    # Check alphabetic ratio — reject mostly punctuation/whitespace
    alpha_count = sum(1 for c in name if c.isalpha())
    if len(name) > 0 and alpha_count / len(name) < 0.4:
        return False

    # Reject pure initials: all tokens are single letters (e.g. "K. M. A.")
    # Strip periods and split on whitespace
    tokens = name.replace(".", "").split()
    if tokens and all(len(t) == 1 for t in tokens):
        return False

    # Reject repeated substring patterns (e.g. "K. M. A. K. M. A. K. M. A.")
    # Normalize to lowercase, remove extra whitespace
    normalized = re.sub(r"\s+", " ", name.lower().strip())
    if len(normalized) >= 10:
        # Check for repeated patterns of length 3-20
        for pattern_len in range(3, min(21, len(normalized) // 2 + 1)):
            pattern = normalized[:pattern_len]
            count = 0
            pos = 0
            while pos <= len(normalized) - pattern_len:
                if normalized[pos:pos + pattern_len] == pattern:
                    count += 1
                    pos += pattern_len
                else:
                    pos += 1
            if count >= 3 and count * pattern_len >= len(normalized) * 0.7:
                return False

    return True


# ── Schema models ─────────────────────────────────────────────────


class _NodeType(str, Enum):
    concept = "concept"
    entity = "entity"
    event = "event"
    location = "location"


class _EntitySubtype(str, Enum):
    person = "person"
    organization = "organization"
    other = "other"


class _FactEntity(BaseModel):
    """An entity/concept/event extracted from a single fact."""
    name: str = Field(description="Canonical full name")
    node_type: _NodeType = Field(description="Classification of the node")
    entity_subtype: _EntitySubtype | None = Field(
        default=None,
        description="Required for entity nodes only: person, organization, or other",
    )
    aliases: list[str] = Field(
        default_factory=list,
        description="Known aliases, acronyms, or alternate names",
    )


class _PerFactExtractionResult(BaseModel):
    """Maps each fact number (as string key) to extracted entities."""
    facts: dict[str, list[_FactEntity]]


_EXTRACTION_SCHEMA = json.dumps(
    _PerFactExtractionResult.model_json_schema(), indent=2,
)

_NODE_EXTRACTION_SYSTEM = """\
You are a precision entity extractor for a knowledge graph. You will receive \
a numbered list of facts. For EACH fact, extract the distinct nodes \
(concepts, entities, events, locations) that the fact explicitly mentions.

Your response MUST be a JSON object mapping each fact number to its entities.

Example output for 3 facts:
```json
{{"facts": {{"1": [{{"name": "Albert Einstein", "node_type": "entity", "entity_subtype": "person", "aliases": []}}, {{"name": "general relativity", "node_type": "concept", "aliases": []}}], "2": [{{"name": "NASA", "node_type": "entity", "entity_subtype": "organization", "aliases": []}}], "3": []}}}}
```

Full schema:

{schema}


## GROUNDING RULE (most important)

For entities (persons, organizations), locations, and events: extract ONLY \
if the name (or a clear abbreviation/alias) appears as a SUBSTRING in that \
specific fact's text. If the name is not literally written in the fact, do \
NOT list it — even if the fact is topically related.

- Fact: "NASA launched Apollo 11 in 1969" → NASA, Apollo 11 ✓
- Fact: "The mission landed on the Moon" → [] ✓ (no named entity in text)
- Fact: "The mission landed on the Moon" → NASA ✗ WRONG (not in text)

For concepts: you may extract abstract topics that the fact discusses even \
if not named verbatim (e.g. a fact about "editing genes with CRISPR" implies \
"gene editing" as a concept). But do NOT tag a concept on a fact that has no \
topical relationship to it.

Each fact is INDEPENDENT. Do NOT let entities from one fact bleed into another.

You MUST use the "facts" key at the top level — NOT "nodes" or "extracted_nodes".


## Node Types

**concept** = abstract topic, idea, theory, phenomenon, field of study, technique, \
procedure, object, technology, publication, or any general knowledge subject.
  Examples: "pyramid construction techniques", "vaccine safety", "quantum entanglement", \
"gradient descent", "CRISPR-Cas9 mechanism", "gene therapy", "germline editing"
  Publications, objects, and technologies are concepts — NOT entities or locations.

**entity** = a subject capable of intent — ONLY persons or organizations.
  Examples: "Albert Einstein", "World Health Organization", "NASA", "Jennifer Doudna"
  NOT entities: locations, publications, objects, technologies, scientific instruments.
  For entity nodes, set entity_subtype:
  - "person" = an individual human
  - "organization" = company, institution, government body, NGO, team, etc.
  - "other" = entity that doesn't fit person or organization

**event** = something that happened at a specific time or period.
  Examples: "2008 financial crisis", "Apollo 11 moon landing", "Chernobyl disaster"
  CRITICAL — Event names must be UNAMBIGUOUS. Include specific subjects and dates: \
"SEC lawsuit against Ripple Labs December 2020" not "the lawsuit".

**location** = a physical place — countries, cities, regions, landmarks.
  Examples: "Silicon Valley", "Chernobyl exclusion zone", "Tokyo"

**Classification rules (apply in order):**
1. If it is a person or organization (capable of intent/agency) → **entity**
2. If it happened at a specific time or period → **event**
3. If it is a physical place, country, city, or geographic feature → **location**
4. Everything else → **concept**


## Naming Guidelines

- Use canonical full names: "Albert Einstein" not "Einstein"
- Include both specific and general: "CRISPR-Cas9" AND "gene editing" as separate nodes
- Do NOT extract perspectives or opinions — only concrete entities, events, and concepts

## Alias Guidelines
- Provide known aliases, acronyms, abbreviations
- Organizations: "Federal Bureau of Investigation" → aliases: ["FBI"]
- Only include aliases you are confident about

## What NOT to Extract
- Do NOT extract author names or initials from citations (e.g., "Smith et al. (2020)", \
"K.M.A. et al.", "J. Doe", "Ana R. S. Silva"). These are bibliographic references, not \
entities the fact is ABOUT.
- Do NOT extract DOIs, journal names from citations, or publication metadata.
- Do NOT extract initials-only patterns (e.g., "K. M. A.", "J. R. R.") unless they are \
well-known acronyms (e.g., "FBI", "NASA", "WHO").
- Distinguish between an entity the fact is ABOUT versus an author who WROTE or is CITED \
BY the fact. Only extract the former.

Respond with ONLY the JSON object. No markdown fences, no commentary.""".format(
    schema=_EXTRACTION_SCHEMA,
)

_NODE_EXTRACTION_USER = """\
Here are {fact_count} facts gathered for the scope "{scope}":

{fact_list}

For EACH fact, list the entities/concepts/events/locations it mentions."""


async def extract_entities_from_facts(
    facts: list,
    gateway: ModelGateway,
    *,
    scope: str = "",
    batch_size: int | None = None,
) -> list[dict[str, Any]] | None:
    """Exhaustively extract all nodes (concepts, entities, events) from facts.

    When the fact list exceeds *batch_size*, it is split into batches and
    each batch is extracted independently (in parallel).  Results are merged
    and deduplicated by normalised name, with fact_indices combined.

    Returns a list of dicts with ``name``, ``node_type``, ``fact_indices``,
    and optionally ``extraction_role`` — or ``None`` on failure.
    """
    if not facts:
        return None

    # Resolve batch size + concurrency from settings if not provided
    if batch_size is None:
        from kt_config.settings import get_settings
        _settings = get_settings()
        batch_size = _settings.entity_extraction_batch_size
        max_concurrent = _settings.entity_extraction_concurrency
    else:
        from kt_config.settings import get_settings
        max_concurrent = get_settings().entity_extraction_concurrency

    # Split into batches
    batches: list[list] = []
    for i in range(0, len(facts), batch_size):
        batches.append(facts[i : i + batch_size])

    logger.info(
        "Node extraction: %d facts → %d batches of ≤%d (concurrency=%d)",
        len(facts), len(batches), batch_size, max_concurrent,
    )

    # Run batches with bounded concurrency to avoid rate limits
    sem = asyncio.Semaphore(max_concurrent)

    completed = 0

    async def _limited(batch: list, offset: int) -> list[dict[str, Any]] | None:
        async with sem:
            result = await _extract_entity_batch(
                batch, offset=offset, gateway=gateway, scope=scope,
            )
            nonlocal completed
            completed += 1
            logger.info(
                "Entity extraction batch %d/%d done (%d facts)",
                completed, len(batches), len(batch),
            )
            return result

    tasks = [
        _limited(batch, offset=i * batch_size)
        for i, batch in enumerate(batches)
    ]
    batch_results = await asyncio.gather(*tasks, return_exceptions=True)

    # Merge results across batches, deduplicating by normalised name
    merged: dict[str, dict[str, Any]] = {}
    for br in batch_results:
        if isinstance(br, BaseException):
            logger.warning("Extraction batch failed: %s", br)
            continue
        if not br:
            continue
        for node in br:
            key = node["name"].strip().lower()
            if key in merged:
                existing_indices = set(merged[key].get("fact_indices", []))
                new_indices = set(node.get("fact_indices", []))
                merged[key]["fact_indices"] = sorted(existing_indices | new_indices)
                # Merge aliases from duplicate mentions
                existing_aliases = set(merged[key].get("aliases", []))
                new_aliases = set(node.get("aliases", []))
                merged[key]["aliases"] = sorted(existing_aliases | new_aliases)
            else:
                merged[key] = node

    valid = list(merged.values())
    return valid if valid else None


async def _extract_entity_batch(
    batch: list,
    *,
    offset: int,
    gateway: ModelGateway,
    scope: str,
) -> list[dict[str, Any]] | None:
    """Extract nodes from a single batch of facts.

    *offset* is the 0-based index of the first fact in this batch within the
    full fact list, used to produce globally correct fact_indices.
    """
    lines: list[str] = []
    for i, f in enumerate(batch, offset + 1):
        lines.append(f"{i}. [{f.fact_type}] {f.content}")
    fact_list = "\n".join(lines)

    user_msg = _NODE_EXTRACTION_USER.format(
        fact_count=len(batch),
        scope=scope or "general",
        fact_list=fact_list,
    )

    try:
        result = await gateway.generate_json(
            model_id=gateway.entity_extraction_model,
            messages=[{"role": "user", "content": user_msg}],
            system_prompt=_NODE_EXTRACTION_SYSTEM,
            temperature=0.0,
            max_tokens=16000,
            reasoning_effort=gateway.entity_extraction_thinking_level or None,
        )
        if not result:
            return None

        return _parse_per_fact_result(result, offset=offset, batch_size=len(batch))

    except Exception:
        logger.warning(
            "Failed to extract nodes from fact batch (offset=%d, size=%d)",
            offset, len(batch), exc_info=True,
        )
        return None


def _parse_per_fact_result(
    result: dict[str, Any],
    *,
    offset: int,
    batch_size: int,
) -> list[dict[str, Any]] | None:
    """Parse the per-fact LLM response into the standard node list format.

    Converts ``{"facts": {"1": [...], "2": [...]}}`` into
    ``[{"name": ..., "fact_indices": [...], ...}]`` grouped by entity name.
    """
    facts_data = result.get("facts")
    if not isinstance(facts_data, dict):
        logger.warning(
            "LLM did not return expected per-fact format (got keys: %s); "
            "discarding batch",
            list(result.keys()),
        )
        return None

    # Map: normalised name -> accumulated node dict
    merged: dict[str, dict[str, Any]] = {}

    for fact_key, entities in facts_data.items():
        # Validate fact key is a number within range
        try:
            fact_idx = int(fact_key)
        except (ValueError, TypeError):
            continue
        # fact_idx is already a global 1-indexed number (we sent it that way)
        if fact_idx < offset + 1 or fact_idx > offset + batch_size:
            continue

        if not isinstance(entities, list):
            continue

        for entry in entities:
            if not isinstance(entry, dict):
                continue
            name = entry.get("name")
            if not name or not isinstance(name, str):
                continue

            name = name.strip()
            if not _is_valid_entity_name(name):
                continue
            norm_key = name.lower()

            node_type = entry.get("node_type", "concept")
            if node_type not in ("concept", "entity", "event", "location"):
                node_type = "concept"

            if norm_key in merged:
                merged[norm_key]["fact_indices"].append(fact_idx)
                # Merge aliases
                for alias in entry.get("aliases", []):
                    if isinstance(alias, str) and alias.strip():
                        a = alias.strip()
                        if a not in merged[norm_key]["aliases"]:
                            merged[norm_key]["aliases"].append(a)
            else:
                node_dict: dict[str, Any] = {
                    "name": name,
                    "node_type": node_type,
                    "fact_indices": [fact_idx],
                    "aliases": [
                        a.strip()
                        for a in entry.get("aliases", [])
                        if isinstance(a, str) and a.strip()
                    ],
                }
                if node_type == "entity":
                    subtype = entry.get("entity_subtype", "other")
                    if subtype not in ("person", "organization", "other"):
                        subtype = "other"
                    node_dict["entity_subtype"] = subtype
                merged[norm_key] = node_dict

    valid = list(merged.values())
    return valid if valid else None
