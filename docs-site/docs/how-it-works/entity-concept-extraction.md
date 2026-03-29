---
sidebar_position: 3
title: Entity & Concept Extraction
---

# Entity & Concept Extraction

After facts are extracted from sources, the system identifies the **entities** and **concepts** mentioned within each fact. This per-fact extraction is what connects raw evidence to the knowledge graph's structural nodes.

## Entities vs. concepts

The system distinguishes two fundamental categories:

### Entities

Entities are **specific, named real-world things**:

- **People** — "Albert Einstein", "Marie Curie"
- **Organizations** — "NASA", "World Health Organization"
- **Locations** — "Paris", "Mount Everest"
- **Publications** — "Nature", "The Lancet"

Entities have proper names and refer to unique, identifiable things in the world.

### Concepts

Concepts are **abstract topics, ideas, techniques, or phenomena**:

- "photosynthesis", "machine learning", "democratic governance"
- "quantum entanglement", "supply chain management"
- "cognitive behavioral therapy", "pyramid construction techniques"

Concepts describe categories, processes, theories, or subjects that can be explored and discussed.

## Per-fact extraction

Entity and concept extraction happens **per-fact** — for each individual fact, the LLM lists which entities and concepts are mentioned in that specific fact. This per-fact granularity is a deliberate design choice that **dramatically reduces hallucinated cross-fact associations**.

If extraction were done at the document level, the LLM might associate entities and concepts that appear in different paragraphs but have no actual relationship. By extracting per-fact, every entity-fact and concept-fact link is grounded in a specific, verifiable statement.

### Extraction schema

For each extracted entity or concept, the system records:

| Field | Description |
|-------|-------------|
| **name** | The entity or concept name (2-150 characters) |
| **node_type** | `entity` or `concept` |
| **entity_subtype** | For entities: `person`, `organization`, `location`, or `publication` |
| **fact_indices** | Which facts (by index) mention this entity/concept |
| **aliases** | Alternative names provided by the LLM |
| **extraction_role** | How the entity appears: `mentioned`, `subject`, or `actor` |

## Validation rules

Extracted names go through validation to filter out noise:

- **Length**: Must be 2-150 characters
- **No pure initials**: Rejects patterns like "K. M. A."
- **No citation artifacts**: Filters out "et al." and similar academic citation fragments
- **Alphabetic content**: Must be >40% alphabetic characters (rejects mostly punctuation/numbers)
- **No excessive repetition**: Rejects names with repeated substrings

These rules ensure that only meaningful, well-formed entity and concept names enter the graph.

## From extraction to seeds

Extracted entities and concepts don't become graph nodes directly. Instead, they become [**seeds**](/how-it-works/seeds-and-routing) — lightweight proto-nodes that accumulate facts over time. When a seed gathers enough evidence, it gets promoted to a full node in the knowledge graph.

This two-step process (extraction -> seed -> node) prevents the graph from being cluttered with nodes backed by only a single mention. It ensures every node in the graph has a meaningful factual base.
