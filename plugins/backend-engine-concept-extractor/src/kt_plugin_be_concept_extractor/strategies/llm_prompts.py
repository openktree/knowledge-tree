"""Prompts and Pydantic schemas used by the LLM entity extractor.

Kept public (no leading underscores) so downstream tooling like the
``prompt_transparency`` endpoint can import them cleanly.
"""

from __future__ import annotations

import json
from enum import Enum

from pydantic import BaseModel, Field


class NodeType(str, Enum):
    concept = "concept"
    entity = "entity"
    event = "event"
    location = "location"


class EntitySubtype(str, Enum):
    person = "person"
    organization = "organization"
    other = "other"


class FactEntity(BaseModel):
    """An entity/concept/event extracted from a single fact."""

    name: str = Field(description="Canonical full name")
    node_type: NodeType = Field(description="Classification of the node")
    entity_subtype: EntitySubtype | None = Field(
        default=None,
        description="Required for entity nodes only: person, organization, or other",
    )
    aliases: list[str] = Field(
        default_factory=list,
        description="Known aliases, acronyms, or alternate names",
    )


class PerFactExtractionResult(BaseModel):
    """Maps each fact number (as string key) to extracted entities."""

    facts: dict[str, list[FactEntity]]


EXTRACTION_SCHEMA = json.dumps(
    PerFactExtractionResult.model_json_schema(),
    indent=2,
)

NODE_EXTRACTION_SYSTEM = """\
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
    schema=EXTRACTION_SCHEMA,
)

NODE_EXTRACTION_USER = """\
Here are {fact_count} facts gathered for the scope "{scope}":

{fact_list}

For EACH fact, list the entities/concepts/events/locations it mentions."""
