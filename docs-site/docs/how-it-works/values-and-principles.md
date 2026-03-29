---
sidebar_position: 1
title: Values & Principles
---

# System Values & Principles

Knowledge Tree is built on a simple conviction: **humanity deserves a shared, open knowledge commons** — a living World Graph where every piece of evidence is preserved, every source is traceable, and every perspective is represented.

## The World Graph

The goal of Knowledge Tree is to create the **World Graph** — a global, open knowledge commons for humanity. Not a chatbot. Not a search engine. A persistent, growing graph of interconnected knowledge where:

- Every fact is grounded in real, external sources
- Every connection between ideas is backed by shared evidence
- Every claim can be traced to its origin
- Multiple perspectives coexist transparently

The World Graph grows richer with every query, every source ingested, and every synthesis created. Topics explored frequently accumulate deep factual bases. The graph belongs to everyone.

## Core Design Principles

### 1. Knowledge from data, not from models

AI models are **reasoning engines**, not knowledge sources. All knowledge in the graph traces back to raw external data — web pages, research papers, uploaded documents, and other real sources.

Models analyze, compare, and synthesize facts. They never inject their own training data as knowledge. This separation is fundamental: it means the graph's knowledge is auditable, updatable, and independent of any single model's biases or training cutoff.

### 2. Integration, not ignoring

The system **never discards coherent information**. When sources disagree, contradictory facts don't get suppressed — they produce [perspective nodes](/how-it-works/dimensions) that represent each viewpoint with its supporting evidence.

This principle means:
- Minority viewpoints are preserved alongside mainstream ones
- Contradictions are surfaced transparently, not hidden
- The graph grows by integration, not by filtering
- Users see the full picture and can evaluate evidence themselves

### 3. Accumulation

The graph **improves with every interaction**. Each query, each source ingested, each synthesis created adds to the shared knowledge base:

- New facts link to existing nodes, enriching them
- New edges are discovered between concepts
- Previously thin topics become deeply supported
- Zero-budget queries can leverage all prior work for free

### 4. Transparency

Nothing is hidden from the user. The system exposes:

- **Facts** — the raw evidence extracted from sources
- **Sources** — where each fact came from, with clickable links
- **Convergence scores** — where multiple AI models agree
- **Divergences** — where models disagree and why
- **The full graph** — every node, edge, and relationship used to generate any answer

### 5. Extensibility

Clean interfaces allow the system to grow without architectural changes:

- **New knowledge providers** (search engines, databases, APIs) implement a single abstract interface
- **New AI models** require configuration only — no code changes
- **New decomposition strategies** plug into the fact extraction pipeline
- **New node types** extend the graph's expressiveness

## How It All Fits Together

Knowledge Tree operates as a pipeline that transforms raw external data into structured, multi-perspective knowledge:

```
Sources → Facts → Seeds → Nodes → Dimensions → Synthesis
```

1. **[Facts](/how-it-works/facts)** are extracted from raw sources with full provenance
2. **[Entities and concepts](/how-it-works/entity-concept-extraction)** are identified within each fact
3. **[Seeds](/how-it-works/seeds-and-routing)** accumulate facts and get promoted to graph nodes
4. **[Relations](/how-it-works/relations-and-edges)** arise from factual co-occurrence, connecting ideas through evidence
5. **[Dimensions](/how-it-works/dimensions)** provide multi-model analysis of each node's fact base
6. **[Synthesis](/how-it-works/synthesis-and-super-synthesis)** weaves everything into coherent, attribution-grounded narratives
