# Big-Seed Dedup Experiment

Prototype of the **big-seed multiplexer** dedup design. Experiment only — no DB
migration, no worker changes. Validates the idea before committing to schema.

## Design

One big-seed row per canonical concept holding:

- `aliases[]` — merged surface forms (admitted via exact alias match OR embedding distance)
- `paths[]` — disambiguation branches (sub-entities sharing the surface form). Each path has its own label, facts, embedding, aliases.

**Multiplexer admit:** incoming name + facts → (1) exact alias? route there. (2) embedding distance? auto-route if very close, reject if too far. (3) LLM sees canonical + all paths w/ sample facts + incoming → picks `merge_path` | `alias_to_parent` | `new_path` | `reject`.

**Alias generation at birth:** when a new path is created, LLM is called once with up to 10 sample facts to emit known aliases / acronyms / alternate spellings. These populate `aliases[]` so future surface forms can hit exact-alias match without LLM.

**Dropped strategies:** phonetic, trigram-typo. Unreliable.

## Files

| File | Purpose |
|---|---|
| `dump_seeds.py` | Pulls seed family + facts from a write-db URL → JSON fixture |
| `big_seed.py` | `BigSeed`, `Path`, `Decision`, `Usage` data classes |
| `llm.py` | Cached LLM + embedding calls, returns `Usage` per call |
| `alias_gen.py` | Generates aliases from name + sample facts |
| `multiplex.py` | `admit(big, name, facts)` — full decision logic |
| `replay.py` | Iterates fixture family, drives multiplexer, records history |
| `report.py` | HTML report with per-step token accounting |
| `run.py` | CLI glue |
| `fixtures/*.json` | Dumped real seed families (nate + others) |
| `llm_cache.jsonl` | Cache of LLM responses keyed by prompt hash (re-run cheap) |

## Run

```bash
# 1. Dump fixtures (prod — requires kubectl port-forward to write-db on 15433)
uv run --project services/api python experiments/big_seed_dedup/dump_seeds.py \
  --db "postgresql+asyncpg://kt:${PROD_PW}@localhost:15433/knowledge_tree_write" \
  --names nate,<others> \
  --label prod

# 2. Dump local
uv run --project services/api python experiments/big_seed_dedup/dump_seeds.py \
  --names nate \
  --label local

# 3. Replay + report
uv run --project services/api python experiments/big_seed_dedup/run.py \
  --fixtures experiments/big_seed_dedup/fixtures \
  --out experiments/big_seed_dedup/report.html

# 4. Open report
xdg-open experiments/big_seed_dedup/report.html
```

Requires `OPENROUTER_API_KEY` in `.env`.

## Success criterion

Nate seed splits into its real underlying entities, LLM reasoning + token cost
visible on each admit step, alias-match hits fire on later arrivals.
