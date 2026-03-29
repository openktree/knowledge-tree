---
sidebar_position: 2
title: Facts
---

# Facts — The Atomic Unit of Knowledge

Facts are the foundation of the entire Knowledge Tree system. A fact is the smallest unit of knowledge extracted from a raw external source. Facts are **typed, objective, and permanently linked** to their original sources for complete provenance traceability.

## What makes a fact

A fact captures what a source claims — without judgment. The system records facts as reported, preserving the source's framing. Whether a claim is true, false, controversial, or widely accepted, it enters the graph the same way: as a typed fact with full attribution.

This objectivity is essential. It means the graph faithfully represents what sources say, and evaluation happens later through [multi-model dimensional analysis](/how-it-works/dimensions) and [convergence scoring](/how-it-works/dimensions#convergence).

## Fact types

Every fact is classified into one of 10 types:

| Type | Description | Example |
|------|-------------|---------|
| **claim** | A declarative statement from a source | "The Great Wall is visible from space" |
| **account** | A narrative or testimony | "The witness described seeing a bright flash" |
| **measurement** | Quantitative data with units | "The sample measured 3.7 pH at 25C" |
| **formula** | A mathematical or logical expression | "E = mc^2" |
| **quote** | A direct quotation, preserved verbatim | "Ask not what your country can do for you" |
| **procedure** | A step-by-step process | "1. Preheat oven to 350F. 2. Mix dry ingredients..." |
| **reference** | A citation or pointer to another source | "See Smith et al. (2023) for methodology" |
| **code** | A source code snippet | "`def fibonacci(n): ...`" |
| **image** | A description of visual content | "Figure 3 shows a cross-section of the cell membrane" |
| **perspective** | An opinionated stance or viewpoint | "The author argues that remote work improves productivity" |

Typing facts enables the system to handle them appropriately — measurements carry different weight than perspectives, quotes must be preserved verbatim, and procedures maintain their ordering.

## The extraction pipeline

Facts are extracted from raw sources through a multi-stage pipeline:

```
Raw Source
  ↓
Segmentation — Split into logical passages
  ↓
Classification — Determine content type of each segment
  ↓
LLM Extraction — Extract structured facts with attribution
  ↓
Attribution — Record who said it, where, when, in what context
  ↓
Embedding — Generate vector embedding (text-embedding-3-large, 3072 dimensions)
  ↓
Deduplication — Check for existing identical facts by embedding similarity
  ↓
Storage — Persist fact with full provenance chain
```

### Segmentation

Raw source content is split into logical passages — not arbitrary character chunks, but semantically coherent segments that respect paragraph boundaries, section breaks, and topical shifts.

### LLM extraction

Each segment is processed by an LLM that extracts individual facts, classifies their type, and identifies the attribution context. The extraction is designed to be **objective** — the model captures what the source says, not what the model believes.

### Embedding and deduplication

Each extracted fact is embedded using OpenAI's `text-embedding-3-large` model (3072 dimensions). The embedding is compared against existing facts using cosine similarity.

If a near-duplicate is found (similarity > 0.92), the new extraction is **linked to the existing fact** as an additional source rather than creating a duplicate. This means popular facts naturally accumulate multiple independent sources over time, strengthening their standing in the graph without creating noise.

## Provenance chain

Every fact maintains a complete provenance chain:

```
Node → Fact → FactSource → RawSource
```

- **Fact** — the extracted claim with its type and content
- **FactSource** — links the fact to a specific raw source, with a `context_snippet` showing the exact text and an `attribution` field recording who said it
- **RawSource** — the original fetched content with URL, title, provider metadata, and retrieval timestamp

This chain is fully traversable. Users can click through from any node's dimension text to the specific facts cited, then to the original source documents.

## Facts are independent of nodes

A critical design decision: **facts exist independently of nodes**. A single fact can be linked to many different nodes. When the fact "Albert Einstein developed the theory of general relativity" is extracted, it can appear in nodes for "Albert Einstein" (entity), "general relativity" (concept), and "history of physics" (concept) simultaneously.

Facts accumulate sources over time. The same factual claim found in multiple independent sources gets linked to all of them, building a richer provenance base without duplication.
