---
sidebar_position: 6
title: Dimensions
---

# Dimensions — Multi-Model Analysis

Dimensions are the heart of Knowledge Tree's approach to understanding. For every node in the graph, **each configured AI model independently analyzes the same fact base**, producing its own perspective. By comparing these perspectives, the system reveals where genuine consensus exists and where model biases determine conclusions.

## The multimodal principle

Knowledge Tree uses multiple AI models — Claude, Gemini, GPT, Grok, Llama, GLM, and others — not as knowledge sources, but as **diverse reasoning engines**. Each model:

1. Receives the **same set of facts** for a given node
2. Analyzes them **independently** (no access to other models' outputs)
3. Produces a **dimension** — a structured analysis with content, confidence score, and suggested concepts

The key insight: when models trained on different data, by different companies, with different architectures all reach the same conclusion from the same evidence — that conclusion is likely robust. When they diverge, the divergence itself is informative.

## What a dimension contains

| Field | Description |
|-------|-------------|
| **content** | The model's analysis text, grounded in facts |
| **confidence** | A 0-1 score reflecting the model's certainty |
| **suggested_concepts** | Related topics the model recommends exploring |
| **fact_count** | Number of facts provided to the model |
| **model_id** | Which model generated this dimension |
| **model_metadata** | Token usage, generation parameters |

### Fact citations

Dimensions include inline citations to specific facts using markdown links: `[brief description](/facts/<uuid>)`. This means every claim in a dimension can be traced to the specific fact that supports it, and from there to the original source.

## Dimension generation process

1. **Load all facts** linked to the node
2. **Load neighbor context** — dimensions from connected nodes (via edges) provide additional context
3. **For each configured model:**
   - Send the same fact base + neighbor context
   - The model generates its analysis independently
   - Parse the response into a Dimension record
4. **Store** all dimensions

### Node-type-specific prompts

Different node types get different analysis prompts:

| Node Type | Focus |
|-----------|-------|
| **Concept** | What the evidence reveals, patterns, connections |
| **Entity** | Role, factual details, relationships, involvement |
| **Event** | Timeline, causes, effects, participants, context |

## Why multiple models matter

A single AI model can:
- Overweight certain types of evidence based on its training data
- Reflect the biases of its creators or training process
- Miss patterns that other architectures would catch
- Present confident-sounding conclusions that lack genuine support

By running diverse models over the same evidence, Knowledge Tree surfaces each perspective as an independent dimension. Readers compare them directly — disagreement is information, not noise.
