---
sidebar_position: 4
title: Seeds & Routing
---

# Seeds & Routing

Seeds are **lightweight proto-nodes** — containers that accumulate facts about an entity or concept before being promoted to full graph nodes. They are the bridge between raw fact extraction and the structured knowledge graph.

## What seeds are

When [entity and concept extraction](/how-it-works/entity-concept-extraction) identifies a name within a fact, the system creates or updates a **seed** for that name. Seeds track:

- The entity/concept name and type
- How many facts reference this seed
- Which facts are linked to it
- A contextual embedding (updated as more facts arrive)
- Status information for disambiguation and merging

Seeds solve an important problem: not every mention deserves a full graph node. By accumulating evidence in seeds first, the system ensures that only well-supported topics become permanent nodes.

## Seed statuses

Each seed has one of four statuses:

| Status | Meaning |
|--------|---------|
| **active** | Normal seed, accumulating facts |
| **ambiguous** | Has been split into disambiguated children (e.g., "Mars" split into "Mars (planet)" and "Mars (Roman god)") |
| **promoted** | Has been converted to a full node in the knowledge graph |
| **merged** | Has been consolidated into another seed (with a pointer to the target) |

## Fact routing

When a new fact mentions an entity or concept, the system must route that fact to the correct seed. This is straightforward for active seeds but requires disambiguation logic for ambiguous ones.

### Routing algorithm

1. **Look up the seed** by its deterministic key (based on name + type)
2. If **active or promoted** — link the fact directly (normal path)
3. If **merged** — follow the merge chain (up to 5 hops) to find the target seed
4. If **ambiguous** — route to the correct child using disambiguation
5. If **not found** — check phonetic matches for potential typos, then create a new seed

### Disambiguation

When a seed has been split into disambiguated children (e.g., "Mars" -> "Mars (planet)" + "Mars (Roman god)"), new facts mentioning "Mars" need to be routed to the correct child.

The system uses a multi-strategy approach:

1. **Embedding similarity** — Compare the new fact's embedding against each child seed's embedding in Qdrant. The closest match usually wins.
2. **Keyword heuristics** — For cases where embeddings are ambiguous, text-based matching provides additional signal.
3. **LLM fallback** — When the top two children have too-close scores, an LLM is asked to pick the correct disambiguation based on the fact's full context.

## Seed deduplication

The system detects and merges duplicate seeds that refer to the same entity/concept but with different spellings or names:

- **Phonetic codes** — Seeds are assigned phonetic codes (similar to Soundex) to detect names that sound alike
- **Trigram similarity** — Character-level trigram comparison catches typos and minor spelling variations
- **Embedding comparison** — Semantic similarity between seed embeddings identifies conceptual duplicates

When duplicates are found, they are merged: one seed absorbs the other's facts, and the merged seed gets a `merged_into_key` pointer.

## Re-embedding

Seeds are re-embedded at configurable fact-count thresholds (e.g., 5, 10, 25, 50 facts). As a seed accumulates more facts, its contextual embedding becomes richer and more representative:

1. Gather the top facts for context
2. Build context text: name + type + top facts + aliases
3. Compute a hash of the context text
4. If the hash changed since last embedding — re-embed and update Qdrant
5. Update the context hash on the seed

This progressive re-embedding means that seeds become better at disambiguation over time.

## Promotion to nodes

When a seed accumulates sufficient facts (a configurable threshold), it is **promoted** to a full node in the knowledge graph:

1. The seed's status changes to `promoted`
2. A new node is created with the seed's name, type, and linked facts
3. The node enters the [node pipeline](/contributing/services) for dimension generation, definition synthesis, and parent selection
4. Edge candidates from the seed's fact co-occurrences are resolved into [graph edges](/how-it-works/relations-and-edges)

Promotion is the moment when accumulated evidence becomes structured knowledge in the graph.
