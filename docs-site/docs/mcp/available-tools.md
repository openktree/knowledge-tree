---
sidebar_position: 3
title: Available Tools
---

# Available Tools

The Knowledge Tree MCP server exposes 8 read-only tools for navigating the knowledge graph. The server also provides navigation instructions to connected agents explaining how to explore the graph effectively.

## search_graph

**Entry point** — Search the knowledge graph for nodes matching a text query. Results are starting points for navigation: after finding relevant nodes, call `get_node` to read the definition, then `get_edges` to discover neighboring nodes and build full context.

An alternative entry point is `search_facts`, which searches the global fact pool directly and can surface evidence spanning multiple nodes.

Each result includes `fact_count` — higher counts indicate richer, better-evidenced nodes worth exploring first.

**Parameters:**

| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| `query` | string | yes | — | Search term for concept names |
| `limit` | int | no | 20 | Max results (1-100) |
| `node_type` | string | no | — | Filter: `concept`, `entity`, `perspective`, `event` |

**Returns:** List of matching nodes with `node_id`, `concept`, `node_type`, `fact_count`, and `also_known_as` aliases.

**Example:**
```
search_graph(query="climate change", limit=5, node_type="concept")
```

---

## get_node

**Node overview** — Load a node's core details including definition, type, parent, creation date, and counts (`fact_count`, `edge_count`, `dimension_count`).

Use the counts to decide what to explore next:
- High `edge_count` → call `get_edges` to see connections and navigate the graph neighborhood
- High `dimension_count` → call `get_dimensions` for deeper multi-model analyses
- High `fact_count` → call `get_facts` for the actual provenance-tracked evidence

If the node has no definition yet, a fallback dimension is included so there is always some descriptive content.

**Parameters:**

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `node_id` | string | yes | UUID of the node |

**Returns:** Node details including `concept`, `node_type`, `definition`, `parent_id`, `parent_concept`, `fact_count`, `edge_count`, `dimension_count`, `aliases`, `merged_from`, and metadata.

**Example:**
```
get_node(node_id="a1b2c3d4-...")
```

---

## get_dimensions

**Deeper analysis** — Load dimensions (model perspectives) for a node. Each dimension is an independent AI model's analysis of the same node, grounded in the same fact base.

Convergence across models (similar content, high confidence) reveals genuine consensus. Divergence reveals where model biases determine conclusions — both are valuable signals.

**Parameters:**

| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| `node_id` | string | yes | — | UUID of the node |
| `limit` | int | no | 10 | Max dimensions (1-50) |
| `offset` | int | no | 0 | Skip N dimensions |

**Returns:** Array of dimensions with `model_id`, `content`, `confidence`, and `generated_at`. Includes pagination info (`returned`, `total`, `offset`).

---

## get_edges

**Graph navigation** — Load edges (relationships) for a node, sorted by shared fact count (most evidence first). This is the key tool for navigating the graph.

Follow high-weight edges to neighboring nodes to build full context around a topic — don't stop at a single node. Each edge includes a justification explaining the relationship and the fact count that backs it.

Edge types: `related` connects same-type nodes, `cross_type` connects different types (e.g., entity↔event).

**Parameters:**

| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| `node_id` | string | yes | — | UUID of the node |
| `limit` | int | no | 10 | Max edges (1-100) |
| `offset` | int | no | 0 | Skip N edges |
| `edge_type` | string | no | — | Filter: `related` or `cross_type` |

**Returns:** Array of edges with `other_node_id`, `other_concept`, `other_node_type`, `relationship_type`, `weight`, `justification`, and `fact_count`. Sorted by evidence strength.

---

## get_facts

**Evidence layer** — Load provenance-tracked facts linked to a node, grouped by source. Every fact traces back to a real external source — nothing comes from AI internal knowledge.

Use this when you need the actual evidence behind a node, need to cite specific claims, or want to verify information. To trace facts to original URLs and citations, use `get_fact_sources`.

**Parameters:**

| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| `node_id` | string | yes | — | UUID of the subject node |
| `limit` | int | no | 50 | Max facts (1-200) |
| `offset` | int | no | 0 | Skip N facts |
| `source_node_id` | string | no | — | UUID of a second node — returns only facts linked to BOTH nodes |
| `author_org` | string | no | — | Filter by author organization (partial match) |
| `source_domain` | string | no | — | Filter by source URL domain (partial match) |
| `search` | string | no | — | Filter by fact content text |
| `fact_type` | string | no | — | Filter: `claim`, `account`, `measurement`, `quote`, etc. |

**Returns:** Facts organized by source groups, each containing `uri`, `title`, `author_org`, `published_date`, and an array of facts. Includes `next_offset` for pagination.

### Node intersection filtering

The `source_node_id` parameter is the most powerful filter. It returns only facts linked to **both** the primary node and the source node. This is the best way to answer questions like "What does CNN say about topic X?" — pass the topic as `node_id` and "CNN" as `source_node_id`.

**Example:**
```
get_facts(
  node_id="<epstein-node-uuid>",
  source_node_id="<cnn-node-uuid>"
)
```

---

## get_fact_sources

**Provenance** — Load all original sources for a node's facts. Returns a deduplicated list of the real external sources (URLs, titles, authors, publication dates) that back the facts linked to this node.

This completes the provenance chain: Node → Facts → Sources. Use this to build citations, verify claims against original articles, or understand which sources contributed to a node's knowledge base.

**Parameters:**

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `node_id` | string | yes | UUID of the node |

**Returns:** List of unique sources with `uri`, `title`, `provider_id`, `published_date`, `author_person`, `author_org`, and `fact_count`.

---

## search_facts

**Alternative entry point** — Search the global fact pool by text query. Searches across ALL facts in the knowledge graph, not just those linked to a specific node.

This is valuable when a topic may not yet have a well-developed node, when you want evidence spanning multiple concepts, or when you want to discover which nodes are relevant (each result includes `linked_nodes`). Uses hybrid search (semantic + keyword) for best results.

**Parameters:**

| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| `query` | string | no | — | Text search query |
| `node_id` | string | no | — | Use a node's concept name and aliases as the search query (more reliable than typing the name) |
| `limit` | int | no | 30 | Max results (1-100) |
| `offset` | int | no | 0 | Skip N results |
| `fact_type` | string | no | — | Filter by fact type |
| `author_org` | string | no | — | Filter by author organization |
| `source_domain` | string | no | — | Filter by source domain |

**Returns:** Array of facts with `content`, `fact_type`, source information, `linked_nodes`, and provenance details.

---

## get_node_paths

**Connection discovery** — Find how two nodes connect through the graph using breadth-first search. This reveals indirect relationships and shared context that may not be obvious.

Returns all shortest paths (same hop count) up to the limit. Explore interesting intermediate nodes with `get_node` and `get_facts` to understand the chain of evidence.

**Parameters:**

| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| `source_node_id` | string | yes | — | UUID of the start node |
| `target_node_id` | string | yes | — | UUID of the end node |
| `max_depth` | int | no | 6 | Maximum path length in hops (1-10) |
| `limit` | int | no | 5 | Maximum number of paths to return (1-20) |

**Returns:** Array of paths, each containing an ordered list of steps (node → edge → node) from source to target with concepts, types, and edge justifications.
