# big_seed_from_facts — end-to-end from raw facts

Sibling experiment to `big_seed_dedup`. Same pipeline (alias_gen →
embedding → reverse alias → qdrant → multiplex), but input is **raw
fact text**, not already-deduped seeds.

Pipeline:

```
raw facts (N ~ 400)
   │
   ▼  spaCy NER + noun chunks
extracted entities (name + source fact)
   │
   ▼  group by normalized name
(name, [facts_mentioning_it]) tuples
   │
   ▼  batched alias_gen
   │  embed + qdrant search + reverse alias
   │  multiplex (merge | split | genesis)
   ▼
big-seed registry + HTML report
```

Kept separate so the seed-based experiment
(`experiments/big_seed_dedup`) stays a stable reference path. Re-uses
`big_seed`, `multiplex`, `llm`, `alias_gen`, `qdrant_index`, and
`report` from that package. Own qdrant collection
(`bigseed_facts_experiment_paths`) so the two don't collide.

## Run

```bash
# Dump 400 random facts from prod (requires kubectl port-forward)
uv run --project services/api python -m experiments.big_seed_from_facts.dump_facts \
    --db "$PROD_URL" --n 400 --label prod

# End-to-end: extract + dedup + report
uv run --project services/api python -m experiments.big_seed_from_facts.run \
    --reset-qdrant \
    --out experiments/big_seed_from_facts/report.html
```

Requires spaCy + `en_core_web_lg`:
```
uv run --project libs/kt-facts python -m spacy download en_core_web_lg
```
