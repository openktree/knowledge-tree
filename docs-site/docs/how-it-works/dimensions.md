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
4. **Compute convergence** across all dimensions
5. **Store** all dimensions and the convergence report

### Node-type-specific prompts

Different node types get different analysis prompts:

| Node Type | Focus |
|-----------|-------|
| **Concept** | What the evidence reveals, patterns, connections |
| **Entity** | Role, factual details, relationships, involvement |
| **Event** | Timeline, causes, effects, participants, context |
| **Perspective** | Build the strongest case, note challenges as obstacles |

For perspective nodes specifically, facts are first **stance-classified** as supporting, challenging, or neutral. The dimension then presents the perspective's case using its supporting facts while acknowledging challenges.

## Convergence

After all dimensions are generated, a **convergence report** is automatically computed:

### Convergence score (0-1)

- **> 0.7** — Strong consensus across models. The conclusion is well-supported.
- **0.4 - 0.7** — Moderate agreement. Some uncertainty remains.
- **< 0.4** — Significant disagreement. Multiple competing interpretations exist.

### What the report contains

| Field | Description |
|-------|-------------|
| **convergence_score** | Overall agreement level |
| **converged_claims** | Claims all models agree on |
| **recommended_content** | Synthesized view of the consensus |
| **divergent_claims** | Where models disagree, with each model's position and analysis |

### Divergences

When models disagree, each divergent claim records:

- The specific claim in question
- Each model's position on it
- The **divergence type** — whether models interpret the same facts differently, emphasize different facts, or reach different conclusions from the same reasoning
- An analysis of what might explain the divergence

Divergences are surfaced transparently to users. They represent genuine areas of uncertainty or bias — exactly the kind of information that a single-model system would hide.

## Why multiple models matter

A single AI model can:
- Overweight certain types of evidence based on its training data
- Reflect the biases of its creators or training process
- Miss patterns that other architectures would catch
- Present confident-sounding conclusions that lack genuine support

By requiring convergence across diverse models analyzing the same evidence, Knowledge Tree provides a level of epistemic robustness that no single model can achieve. The system doesn't hide disagreement — it highlights it as valuable information.
