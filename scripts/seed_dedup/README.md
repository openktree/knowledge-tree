# Seed Deduplication Experiments

Empirical tests for tuning seed dedup thresholds. Each script embeds name pairs
via the configured embedding service and reports how well the current (or swept)
thresholds separate same-entity pairs from different-entity pairs.

## Prerequisites

```bash
# Needs OPENROUTER_API_KEY (or OPENAI_API_KEY) in .env
docker compose up -d  # for any infra if needed
```

## Scripts

| Script | What it does |
|--------|-------------|
| `run_embedding_sim.py` | Embed all pairs, report cosine similarity vs threshold. Exit 1 on misclassification. |
| `run_typo_coverage.py` | For each typo pair: embedding score, phonetic codes, trigram similarity. Shows which typos need phonetic fallback. |
| `run_threshold_sweep.py` | Sweep `embedding_threshold` (0.70-0.95) and `typo_floor` (0.60-0.85), report precision/recall/F1. |

## How to run

```bash
# Core experiment — validates current thresholds
uv run --project libs/kt-models python scripts/seed_dedup/run_embedding_sim.py

# Typo gap analysis
uv run --project libs/kt-models python scripts/seed_dedup/run_typo_coverage.py

# Threshold optimization
uv run --project libs/kt-models python scripts/seed_dedup/run_threshold_sweep.py
```

## Adding test pairs

Edit `datasets.py`. Each pair is a `SeedPair` namedtuple:

```python
SeedPair("Democratic Party", "Democrtic Party", True, "typo", "missing letter")
```

Fields: `name_a`, `name_b`, `should_merge` (True/False/None), `category`, `notes`.

Categories: `typo`, `different_entity`, `alias`, `containment`, `subtle`.
