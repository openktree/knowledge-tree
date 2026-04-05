---
sidebar_position: 5
title: Relations & Edges
---

# Relations & Edges

Edges are the structural connections of the knowledge graph. Every edge is **grounded in shared factual evidence** — not semantic similarity, not embedding proximity, but actual facts that mention both connected nodes.

## The key principle

:::info Evidence-grounded edges
Embedding proximity does **NOT** create edges. Two nodes can be semantically close (similar embeddings) yet have no edge if no facts mention both. An edge exists because facts explicitly reference both concepts.
:::

This is a fundamental architectural distinction. Semantic similarity is a **search tool** — it helps find related nodes. But edges represent a stronger claim: "these two concepts appear together in the same factual evidence."

## How edges are created

Edges arise from **seed co-occurrence** during fact extraction:

1. During [fact decomposition](/how-it-works/facts#the-extraction-pipeline), each fact's entities and concepts are extracted
2. When a single fact mentions **multiple seeds**, those seeds become **edge candidates**
3. Edge candidates accumulate in the `write_edge_candidates` table
4. When a seed is [promoted to a node](/how-it-works/seeds-and-routing#promotion-to-nodes), the **edge resolver** processes its candidates:
   - Loads all shared facts between node pairs
   - Calls an LLM to generate a justification citing specific facts
   - Creates the edge with weight = number of shared facts

### Weight semantics

An edge's **weight** equals the number of facts shared between its two nodes. Higher weight means stronger evidence for the relationship. This is always a positive number — there are no negative edges.

## Edge types

| Type | When used | Example |
|------|-----------|---------|
| **related** | Connects nodes of the **same type** (concept-concept, entity-entity) | "solar power" ↔ "wind power" |
| **cross_type** | Connects nodes of **different types** (entity-event, concept-entity) | "NASA" (entity) ↔ "Apollo 11" (event) |

The relationship type is determined automatically by comparing the node types of the two endpoints: same type produces `related`, different types produce `cross_type`.

## Edge properties

Each edge stores:

| Field | Description |
|-------|-------------|
| **source_node_id** | One endpoint (canonical: always the smaller UUID) |
| **target_node_id** | Other endpoint (canonical: always the larger UUID) |
| **relationship_type** | `related` or `cross_type` |
| **weight** | Shared fact count (positive float) |
| **justification** | LLM-generated reasoning with `{fact:uuid}` citation tokens |

### Canonical ordering

Edges use **canonical UUID ordering** — the smaller UUID is always stored as `source_node_id`. This ensures each node pair has exactly one edge per type, regardless of which direction the edge was discovered. A database unique constraint enforces this.

### Justifications

Each edge includes an LLM-generated justification explaining why the two nodes are related. Justifications cite specific facts using `{fact:uuid}` tokens, which are rendered as clickable links in the UI. This makes every connection in the graph auditable.

## Circular references

Circular references are valid and expected. "Water" links to "hydrogen" and "hydrogen" links to "water" — this reflects real conceptual structure. The graph is **flat** (all nodes are peers), and cycles are natural.

Navigation agents handle cycles through `visited_nodes` tracking, ensuring they don't get stuck in loops while traversing the graph.

## Edges vs. embedding similarity

| | Edges | Embedding Similarity |
|---|-------|---------------------|
| **Created by** | Seed co-occurrence + LLM justification | Computed from content |
| **Meaning** | "These concepts share factual evidence" | "These concepts have similar semantic content" |
| **Used for** | Graph traversal, synthesis, visualization | Node search, dedup detection |
| **Grows with use** | Yes — more queries discover more edges | No — determined by content |

Both are valuable, but they serve different purposes. Edges represent verified factual connections; similarity is a discovery heuristic.
