---
sidebar_position: 7
title: Synthesis & Super-Synthesis
---

# Synthesis & Super-Synthesis

Synthesis is where the knowledge graph comes alive — an AI agent navigates the graph, follows evidence trails, and weaves everything into a coherent, attribution-grounded research document.

## Synthesis

### The Synthesizer Agent

The **SynthesizerAgent** is a LangGraph-based agent that explores the knowledge graph to answer a research question. It operates with an **exploration budget** — a configurable limit on how many nodes it can visit, controlling investigation depth without restricting search.

### Navigation tools

The agent has access to tools for exploring the graph:

| Tool | Purpose | Budget Cost |
|------|---------|-------------|
| **get_node** | Load a node's definition, edge count, fact count | 1 unit per unvisited node |
| **get_node_facts** | Retrieve all facts for a node with source attribution | Free if already visited |
| **get_node_dimensions** | Load multi-model analyses for comparison | Free if already visited |
| **search_graph** | Find nodes by text search | Free |
| **finish_synthesis** | Submit the final document | Free |

The agent decides which nodes to visit, which edges to follow, and when it has gathered enough evidence to write.

### Synthesis principles

The agent follows strict principles when writing:

1. **Answer first, evidence second** — Lead with insight; facts are building blocks, not the conclusion
2. **Attribution-grounded** — Every claim connects to specific facts and sources. No bare assertions.
3. **Radical source neutrality** — No source gets automatic credibility based on institutional prestige. All receive equal scrutiny.
4. **Preserve perspectives** — When evidence supports multiple viewpoints, each gets dedicated coverage
5. **Stakeholder analysis** — For every claim, consider who benefits from belief in that claim
6. **Citation discipline** — Inline fact citations rendered as clickable links to original sources

### Convergence-aware writing

The agent adapts its writing based on convergence scores:

| Convergence | Treatment |
|-------------|-----------|
| **> 0.7** | Present as established, well-supported conclusion |
| **0.4 - 0.7** | Note uncertainty, present evidence from both sides |
| **< 0.4** | Present competing perspectives explicitly, let evidence speak |

### Output

A synthesis produces:

- **Markdown document** with inline fact citations
- **Confidence score** for the overall synthesis
- **Cited facts and nodes** — which evidence was used
- **Divergences** — where the synthesis encountered unresolved disagreements
- **Subgraph** — the nodes and edges traversed, for UI visualization

## Super-Synthesis

### When synthesis isn't enough

Some research questions are too broad or multi-faceted for a single synthesis agent. Super-synthesis solves this by orchestrating **multiple synthesis agents** working in parallel on different aspects of the question.

### The Super-Synthesizer Agent

The **SuperSynthesizerAgent** is a meta-level agent that:

1. **Reads sub-synthesis documents** — Reviews the output of multiple synthesis runs
2. **Identifies connections** — Finds themes, patterns, and relationships across syntheses
3. **Searches for gaps** — Queries the graph for additional context that bridges between sub-topics
4. **Produces a meta-narrative** — Combines everything into a unified, cross-domain document

### Super-synthesis tools

| Tool | Purpose |
|------|---------|
| **read_synthesis** | Load the full text of a sub-synthesis document |
| **get_synthesis_nodes** | List all nodes referenced in a synthesis |
| **search_graph** | Find additional connecting nodes |
| **get_node** | Load node details for cross-referencing |
| **finish_super_synthesis** | Submit the combined document |

### The workflow

1. **Reconnaissance** — The super-synthesizer plans scopes covering different angles of the topic
2. **Parallel dispatch** — Multiple synthesizer agents are launched, each investigating one scope
3. **Collection** — Sub-synthesis documents are gathered as they complete
4. **Integration** — The super-synthesizer reads all sub-syntheses, resolves overlaps, finds connections, and fills gaps
5. **Meta-narrative** — A unified document is produced that weaves all perspectives together

### Example

For a broad question like "What causes cancer?", the super-synthesizer might plan:

- **Agent 1**: Genetic factors and hereditary predisposition
- **Agent 2**: Environmental and occupational exposure
- **Agent 3**: Lifestyle factors (diet, exercise, substance use)
- **Agent 4**: Emerging research (epigenetics, microbiome, immune system)
- **Agent 5**: Treatment implications and prevention strategies

Each agent produces a focused synthesis. The super-synthesizer then combines these into a comprehensive document that reveals cross-domain connections — like how genetic predisposition interacts with environmental exposure, or how lifestyle factors modulate immune responses.

The result is a research document that no single synthesis run could produce, grounded in the same evidence-first, attribution-based approach that underlies all of Knowledge Tree.
