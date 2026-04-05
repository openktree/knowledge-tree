---
sidebar_position: 4
title: Examples
---

# Example Workflows

Common patterns for exploring the knowledge graph through the MCP tools.

## Explore a topic

Start with a search, then drill into the most relevant node:

1. **Search** for the topic:
   ```
   search_graph(query="quantum entanglement", node_type="concept")
   ```

2. **Load the node** to see its definition and counts:
   ```
   get_node(node_id="<node-uuid>")
   ```

3. **Read dimensions** to see how different AI models analyze the topic:
   ```
   get_dimensions(node_id="<node-uuid>")
   ```

4. **Explore connections** to find related concepts:
   ```
   get_edges(node_id="<node-uuid>", limit=20)
   ```

## Deep-dive into evidence

When you want to understand the factual basis for a node:

1. **Get the facts** grouped by source:
   ```
   get_facts(node_id="<node-uuid>", limit=100)
   ```

2. **Check provenance** — see all sources that contributed:
   ```
   get_fact_sources(node_id="<node-uuid>")
   ```

3. **Filter by type** to focus on specific evidence:
   ```
   get_facts(node_id="<node-uuid>", fact_type="measurement")
   ```

## Find what a source says about a topic

Use node intersection to find facts shared between a topic and a source entity:

1. **Search for both nodes:**
   ```
   search_graph(query="climate change")
   search_graph(query="NASA", node_type="entity")
   ```

2. **Get intersecting facts:**
   ```
   get_facts(
     node_id="<climate-change-uuid>",
     source_node_id="<nasa-uuid>"
   )
   ```

This returns only facts that are linked to both nodes — effectively answering "What does NASA say about climate change?"

## Find connections between concepts

Discover how two topics are related through the graph:

1. **Search for both nodes:**
   ```
   search_graph(query="sleep deprivation")
   search_graph(query="immune system")
   ```

2. **Find paths between them:**
   ```
   get_node_paths(
     source_node_id="<sleep-uuid>",
     target_node_id="<immune-uuid>",
     max_depth=4
   )
   ```

3. **Explore intermediate nodes** on the path:
   ```
   get_node(node_id="<intermediate-node-uuid>")
   get_facts(node_id="<intermediate-node-uuid>")
   ```

## Compare model analyses

See where AI models agree and disagree on a topic:

1. **Load all dimensions:**
   ```
   get_dimensions(node_id="<node-uuid>", limit=50)
   ```

2. **Compare confidence scores** — high confidence across models suggests strong evidence. Low or divergent confidence suggests uncertainty.

## Building references for users

When citing information from the knowledge graph, construct wiki URLs so users can verify claims in the browser. The MCP server instructions include the URL patterns, but here they are for reference:

**Node pages:**
```
https://wiki.openktree.com/nodes/{node_type}-{slug}
```
Where `slug` = concept name lowercased, non-alphanumeric characters replaced with `-`, leading/trailing `-` stripped.

Examples:
- "Machine Learning" (concept) → `https://wiki.openktree.com/nodes/concept-machine-learning`
- "NASA" (entity) → `https://wiki.openktree.com/nodes/entity-nasa`
- "Apollo 11 Moon Landing" (event) → `https://wiki.openktree.com/nodes/event-apollo-11-moon-landing`

**Fact pages:**
```
https://wiki.openktree.com/facts/{fact_id}
```
Where `fact_id` is the UUID returned by `get_facts` or `search_facts`.
