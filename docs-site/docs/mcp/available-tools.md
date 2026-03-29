---
sidebar_position: 3
title: Available Tools
---

# Available Tools

The Knowledge Tree MCP server exposes 8 read-only tools for navigating the knowledge graph.

## search_graph

Search the knowledge graph for nodes matching a text query.

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

Load a node's core details including definition, parent, creation date, and counts.

**Parameters:**

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `node_id` | string | yes | UUID of the node |

**Returns:** Node details including `concept`, `node_type`, `definition`, `parent_id`, `parent_concept`, `fact_count`, `edge_count`, `dimension_count`, `aliases`, `merged_from`, and metadata. If the node has no definition, a fallback dimension is included.

**Example:**
```
get_node(node_id="a1b2c3d4-...")
```

---

## get_dimensions

Load dimensions (model perspectives) for a node. Each dimension is a different AI model's independent analysis.

**Parameters:**

| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| `node_id` | string | yes | — | UUID of the node |
| `limit` | int | no | 10 | Max dimensions (1-50) |
| `offset` | int | no | 0 | Skip N dimensions |

**Returns:** Array of dimensions with `model_id`, `content`, `confidence`, and `generated_at`. Includes pagination info (`returned`, `total`, `offset`).

---

## get_edges

Load edges (relationships) for a node, sorted by fact count (most evidence first).

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

Load facts linked to a node, grouped by source. Supports powerful filtering for targeted research.

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

Load deduplicated sources for a node's facts — useful for understanding provenance.

**Parameters:**

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `node_id` | string | yes | UUID of the node |

**Returns:** List of unique sources with `uri`, `title`, `provider_id`, `published_date`, `author_person`, `author_org`, and `fact_count`.

---

## search_facts

Search the global fact pool by text query, optionally scoped to a node.

**Parameters:**

| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| `query` | string | no | — | Text search query |
| `node_id` | string | no | — | Scope search to a specific node's facts |
| `limit` | int | no | 20 | Max results (1-100) |
| `offset` | int | no | 0 | Skip N results |
| `fact_type` | string | no | — | Filter by fact type |
| `author_org` | string | no | — | Filter by author organization |
| `source_domain` | string | no | — | Filter by source domain |

**Returns:** Array of facts with `content`, `fact_type`, source information, and provenance details.

---

## get_node_paths

Find shortest paths between two nodes in the graph using breadth-first search.

**Parameters:**

| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| `source_node_id` | string | yes | — | UUID of the start node |
| `target_node_id` | string | yes | — | UUID of the end node |
| `max_depth` | int | no | 4 | Maximum path length |
| `limit` | int | no | 3 | Maximum number of paths to return |

**Returns:** Array of paths, each containing an ordered list of nodes from source to target with their concepts and types.
