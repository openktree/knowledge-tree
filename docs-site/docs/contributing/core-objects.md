---
sidebar_position: 2
title: Core Objects
---

# Core Objects

The data model is defined in two files:
- **Graph-db models**: `libs/kt-db/src/kt_db/models.py`
- **Write-db models**: `libs/kt-db/src/kt_db/write_models.py`

## Node

The atomic unit of the knowledge graph. All nodes are flat peers — structure comes from edges.

```python
# Graph-db
Node:
  id:                 UUID          # Primary key (= key_to_uuid(write_key))
  concept:            str           # Human-readable label
  node_type:          str           # concept | entity | event | synthesis
  parent_id:          UUID | None   # FK to parent node
  definition:         str | None    # Synthesized from dimensions
  embedding:          float[]       # Averaged across dimension embeddings
  entity_subtype:     str | None    # For entities: person, org, location, publication
  metadata_:          JSONB         # Aliases, merged_from, dialectic info
  created_at:         timestamp
  updated_at:         timestamp
```

```python
# Write-db
WriteNode:
  key:                str           # Deterministic TEXT PK (make_node_key(type, concept))
  node_uuid:          UUID          # = key_to_uuid(key)
  name:               str           # Concept name
  node_type:          str
  fact_ids:           str[]         # Array of fact UUIDs linked to this node
  parent_key:         str | None    # Write-db FK to parent
  definition:         str | None
  metadata_:          JSONB
```

### Node types

| Type | Description |
|------|-------------|
| `concept` | Abstract topic, idea, technique, phenomenon |
| `entity` | Named real-world thing (person, organization, location) |
| `event` | Temporal occurrence (historical, scientific, ongoing) |
| `synthesis` | Composite document synthesizing multiple nodes |
| `supersynthesis` | Meta-synthesis combining multiple synthesis documents |

## Fact

The atomic unit of knowledge derived from raw sources.

```python
Fact:
  id:            UUID
  content:       str           # The factual claim as extracted
  fact_type:     str           # claim | account | measurement | formula | quote | ...
  embedding:     float[]       # In Qdrant (3072 dimensions)
  metadata_:     JSONB         # Type-specific metadata
  created_at:    timestamp
```

## Edge

Connects two nodes with evidence-grounded relationships.

```python
Edge:
  id:                 UUID
  source_node_id:     UUID          # Smaller UUID (canonical ordering)
  target_node_id:     UUID          # Larger UUID
  relationship_type:  str           # related | cross_type
  weight:             float         # Shared fact count
  justification:      str | None    # LLM reasoning with fact citations
  metadata_:          JSONB
  created_at:         timestamp
```

## Dimension

One AI model's independent analysis of a node's fact base.

```python
Dimension:
  id:                 UUID
  node_id:            UUID          # FK to Node
  model_id:           str           # e.g., "openrouter/anthropic/claude-opus-4-6"
  content:            str           # Analysis text with fact citations
  confidence:         float         # 0-1
  suggested_concepts: str[]         # Related topics to explore
  fact_count:         int           # Facts provided to the model
  model_metadata:     JSONB         # Token usage, parameters
  generated_at:       timestamp
```

## Convergence Report

Auto-generated comparison of all dimensions for a node.

```python
ConvergenceReport:
  id:                 UUID
  node_id:            UUID
  convergence_score:  float         # 0-1, agreement across models
  converged_claims:   str[]         # Claims all models agree on
  recommended_content: str          # Synthesized consensus view
  computed_at:        timestamp
```

## Seed (Write-db)

Lightweight proto-node that accumulates facts before promotion.

```python
WriteSeed:
  key:                str           # Deterministic (make_seed_key(type, name))
  seed_uuid:          UUID
  name:               str
  node_type:          str           # entity | concept
  status:             str           # active | ambiguous | promoted | merged
  fact_count:         int
  merged_into_key:    str | None    # If merged, points to parent seed
  phonetic_code:      str           # For typo detection
  context_hash:       str           # Tracks embedding staleness
  metadata_:          JSONB         # Aliases, disambiguation info
```

## Junction tables

| Table | Links | Additional Fields |
|-------|-------|-------------------|
| **NodeFact** | Node ↔ Fact | `relevance_score`, `stance` (supports/challenges/neutral) |
| **EdgeFact** | Edge ↔ Fact | `relevance_score` |
| **DimensionFact** | Dimension ↔ Fact | — |

## Provenance chain

```
Node → NodeFact → Fact → FactSource → RawSource
```

| Table | Purpose |
|-------|---------|
| **FactSource** | Links a fact to a specific raw source, with `context_snippet`, `attribution`, `author_person`, `author_org` |
| **RawSource** | Append-only storage: `uri`, `title`, `raw_content`, `content_hash`, `provider_id`, `provider_metadata` |
