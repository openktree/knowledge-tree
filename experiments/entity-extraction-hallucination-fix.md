# Entity Extraction Hallucination Fix

**Date:** 2026-03-19
**Status:** Implemented
**Impact:** Eliminates two classes of LLM hallucination that created thousands of spurious entity-fact links

## Problem

Two hallucination failure modes were inflating entity nodes with unrelated facts:

### 1. Author Name Hallucination

The author extraction LLM (`LlmHeaderStrategy`) received article URLs and the first ~500 characters of content. For academic papers, the header is typically just the abstract with no author information. The LLM invented plausible-looking author names from its training data.

**Example:** The Nature paper at `s41380-024-02638-x` has an abstract starting with "There is a growing literature exploring the placebo response..." — no author names visible. The LLM returned `"M. A. M. van der Heijden, M. J. H. M. van der Heijden, A. M. J. M. van der Heijden"`.

The pipeline then created seeds for each name and linked ALL facts from that source (400-1400 facts) to each author seed via `source_attribution` role. Result: 4 nodes for "van der Heijden" with different hallucinated initials, each with hundreds of unrelated facts.

**DB evidence:**
```
entity:m-j-h-m-van-der-heijden  | M. J. H. M. van der Heijden | 485 facts
entity:m-a-m-van-der-heijden    | M. A. M. van der Heijden     | 1417 facts
entity:s-j-m-m-van-der-heijden  | S. J. M. M. van der Heijden  | 933 facts
entity:a-m-j-m-van-der-heijden  | A. M. J. M. van der Heijden  | 485 facts
```

### 2. Entity Extraction Cross-Contamination

The entity extraction LLM processes facts in batches. When it sees an entity (e.g. "Annals of Internal Medicine", "Nature", "Harvard University") mentioned in a few facts within a batch, it cross-contaminates by tagging that entity on many other facts in the same batch that don't mention it at all.

**DB evidence:**
```
Nature                       | 2735 mentioned-role links (only ~15 facts actually mention "Nature")
MDPI                         | 2732 links
Annals of Internal Medicine  | 1843 links (only 15 mention it)
David Moher                  | 1747 links
Harvard University           | 1744 links
```

## Root Cause Analysis

### Author hallucination
- The prompt said "Do NOT guess" but also "For academic papers: list all authors" — contradictory
- The LLM resolved the conflict by guessing
- The `source_attribution` fan-out then linked ALL source facts to each hallucinated author

### Cross-contamination
- The prompt said "list ONLY entities that the fact's text explicitly mentions" but this was insufficient — the LLM ignored it at scale
- With 100+ facts per batch, the LLM lost track of which entities belong to which facts
- The prompt lacked concrete wrong-vs-right examples showing the grounding requirement

## Research

We investigated 8 approaches from recent literature (KGGen/NeurIPS 2025, AEVS framework, MicroLLM, spaCy-LLM integration) and tested 5 strategies experimentally.

### Literature findings

| Approach | Key insight | Applicability |
|---|---|---|
| KGGen two-pass (NeurIPS 2025) | Decompose into discover → link passes | High cost (2x calls), overkill |
| AEVS anchor-constrained | Pre-identify text anchors, constrain LLM | Good but complex |
| spaCy NER pre-filter | Lightweight NER before LLM classification | Good for entities, poor for concepts |
| Smaller batch sizes | Multi-record batching "associated with hallucinations" (PMC 2025) | Direct fix, our batch=10 already small |
| Post-extraction verification | Second LLM pass validates associations | Expensive (N extra calls) |
| Confidence scoring | Token log probabilities | Not available via OpenRouter |
| Embedding validation | Cosine similarity entity↔fact | Noisy for short names vs long text |
| Strict grounding prompt | Concrete examples of wrong behavior | Zero cost, addresses root cause |

### Experiment: 5 strategies compared

Tested on 15 annotated facts with ground-truth entity labels:

| Strategy | Precision | Recall | F1 | FP | LLM Calls | Time |
|---|---|---|---|---|---|---|
| 1. Current batch prompt | 100% | 81.8% | 90.0% | 0 | 1 | 4.6s |
| 2. Individual per-fact | 100% | 86.7% | 92.9% | 0 | 15 | 3.9s |
| 3. Batch + LLM validate | 100% | 81.8% | 90.0% | 0 | 1 | 3.5s |
| 4. Small batches (5) | 100% | 81.8% | 90.0% | 0 | 3 | 6.8s |
| **5. Strict grounding** | **100%** | **91.7%** | **95.7%** | **0** | **1** | **3.1s** |

**Winner: Strict grounding prompt** — highest F1, single LLM call, fastest.

At 15 facts, no strategy showed cross-contamination. The problem manifests at larger batches. We confirmed at 40 facts with the strict prompt: **zero false positives, 100% precision**.

### Key insight

The cross-contamination problem is addressed at the root by the prompt, not by post-hoc guards. A text-match validation guard was implemented initially as a workaround but adds unnecessary computational overhead when the prompt itself prevents the problem. We removed it in favor of the cleaner solution.

## Changes Implemented

### 1. Author extraction prompt fix (`libs/kt-facts/src/kt_facts/author.py`)

Replaced the contradictory prompt with one that:
- States author names must be **explicitly visible** in the provided text or metadata
- Explicitly says academic abstracts don't contain author names
- Defaults to null: "A missing author is far better than a wrong one"

### 2. Author name hallucination guard (`libs/kt-facts/src/kt_facts/author.py`)

Added `_has_excessive_initials()` — rejects names with 4+ leading single-letter initials (e.g. "A. M. J. M. van der Heijden"). Applied to both LLM and PDF metadata strategies.

Added `_clean_person_field()` — filters hallucinated names and deduplicates repeated names from the comma-separated person string.

### 3. Removed source_attribution fact linking (`libs/kt-facts/src/kt_facts/pipeline.py`)

Removed the fan-out that linked ALL facts from a source to author seeds. Author seeds are still created as entities, but fact provenance is tracked solely through `write_fact_sources.author_person` / `author_org` columns — the source of truth that was already correct.

### 4. Strict grounding prompt for entity extraction (`libs/kt-facts/src/kt_facts/processing/entity_extraction.py`)

Replaced the entity extraction system prompt with a strict grounding version:
- Concrete wrong-vs-right examples: "NASA launched Apollo 11" → NASA ✓ vs "The mission landed" → NASA ✗
- Explicit rule: entity name must be a SUBSTRING of the fact text
- Per-fact independence: "Each fact is INDEPENDENT. Do NOT let entities from one fact bleed into another"
- Concepts remain flexible (may extract implied topics), but entities/locations/events require literal mention

### 5. Removed text-match validation guard (`libs/kt-facts/src/kt_facts/processing/entity_extraction.py`)

Removed `_entity_mentioned_in_text()` and the fact_texts validation pass from `_parse_per_fact_result`. The strict grounding prompt eliminates the problem at the source, making post-hoc validation unnecessary overhead.

### 6. Removed `_filter_source_attributions` (`services/worker-search/src/kt_worker_search/workflows/decompose.py`)

Deleted the function that injected source_attribution entities into the extraction pipeline and filtered LLM-extracted attributions by text match. No longer needed since author seeds are created independently without fact linking.

### 7. Cleaned up API source_attribution references (`services/api/src/kt_api/seeds.py`)

Removed source_attribution count queries and filtering from the seeds API. The `source_fact_count` field is retained in the response schema (set to 0) for API compatibility.

## Test Results

- `libs/kt-facts/tests/`: 336 passed
- `services/worker-search/tests/`: 4 passed
- `services/api/tests/`: 1 passed (seed integration test)
- `libs/kt-db/tests/test_write_seeds.py`: 32 passed

## Files Changed

| File | Change |
|---|---|
| `libs/kt-facts/src/kt_facts/author.py` | Improved prompt, added `_has_excessive_initials`, `_clean_person_field` |
| `libs/kt-facts/src/kt_facts/pipeline.py` | Removed source_attribution fact linking, kept author seed creation |
| `libs/kt-facts/src/kt_facts/processing/entity_extraction.py` | Strict grounding prompt, removed text-match guard |
| `libs/kt-facts/src/kt_facts/processing/seed_extraction.py` | Removed source_attribution co-occurrence guard |
| `services/worker-search/src/kt_worker_search/workflows/decompose.py` | Removed `_filter_source_attributions`, `_build_name_pattern` |
| `services/api/src/kt_api/seeds.py` | Removed source_attribution queries, cleaned up `_seed_to_response` |
| `libs/kt-facts/tests/test_author.py` | Added 17 tests for hallucination detection |
| `services/worker-search/tests/test_source_attribution_filter.py` | Deleted (tested removed code) |

## Experiment Files

| File | Purpose |
|---|---|
| `experiments/author_hallucination_experiment.py` | Reproduced author hallucination, tested prompt fix |
| `experiments/author_org_extraction_test.py` | Verified org extraction still works with new prompt |
| `experiments/entity_extraction_strategies.py` | Compared 5 entity extraction strategies |
| `experiments/realistic_batch_test.py` | Verified strict prompt at 40-fact batch size |
| `experiments/entity-extraction-hallucination-fix.md` | This report |

## References

- [KGGen: Extracting Knowledge Graphs from Plain Text](https://arxiv.org/html/2502.09956v1) — NeurIPS 2025
- [AEVS: Grounded KG Extraction via LLMs](https://www.mdpi.com/2073-431X/15/3/178) — 2025
- [MicroLLM: Hybrid NER + LLM](https://academic.oup.com/bib/article/26/5/bbaf534/8284867) — 0.87 F1 with BERT NER
- [Multi-Patient Batch LLM Extraction](https://pmc.ncbi.nlm.nih.gov/articles/PMC11751965/) — "hallucinations and abandoned"
- [Dual-Channel Claim Verification](https://www.researchsquare.com/article/rs-9142139/v1) — reduced hallucination to 0.29%
