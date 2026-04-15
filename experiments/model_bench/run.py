"""Benchmark multiple LLMs on the big-seed pipeline's classifier tasks.

Batched: one LLM call per batch of N items (config.batch_size). Scores
each item individually from the batch response. Mirrors how the
production pipeline actually uses these classifiers.

Usage:
    uv run --project services/api python -m experiments.model_bench.run
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import time
from dataclasses import dataclass
from pathlib import Path

import yaml
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[2] / ".env")

import litellm  # noqa: E402
from kt_config.settings import get_settings  # noqa: E402

from .datasets import TASKS, BenchItem  # noqa: E402
from .report import generate_report  # noqa: E402


# ── System prompts (BATCH mode — production shape) ────────────────

_ALIAS_SYSTEM = """\
ALIAS RULE

X is an alias of Y iff replacing Y with X, or X with Y, in any sentence
preserves what the sentence refers to. Bidirectional substitution must
hold in every possible sentence.

Emit aliases only for pure naming variants: acronym↔expansion,
spelling/transliteration variants, singular↔plural of one concept,
official short↔long forms, capitalization/stylization variants.

If substitution narrows, broadens, or shifts the referent set, not
aliases. Empty list when uncertain.

BATCH MODE: user message lists multiple names. Return per-index.
Include every entry, empty list allowed.

Output JSON exactly:
{"results": [{"index": N, "aliases": ["..."]}, ...]}
"""

_SHELL_SYSTEM = """\
SHELL RULE

A noun is SHELL only when it cannot, in any domain anywhere, serve as
a legitimate topic of study, policy, or substantive discourse. Shell
words are pure propositional slots — only meaningful via a complement.

Default: false. When uncertain, false. Multi-token names NEVER shell.

BATCH MODE: user message lists multiple names. Boolean-only per entry,
no reasoning.

Output JSON exactly:
{"results": [{"index": N, "is_shell": bool}, ...]}
"""

_SUGGEST_DISAMBIG_SYSTEM = """\
NATURAL AMBIGUITY RULE

Given a bare name, list canonical disambiguation labels if the name is
naturally ambiguous and commonly refers to multiple distinct real-world
entities/concepts.

Examples:
  Mercury    → ["Mercury (planet)", "Mercury (element)", "Mercury (Roman god)"]
  Apollo     → ["Apollo (Greek god)", "Apollo (NASA program)"]
  Java       → ["Java (programming language)", "Java (island)"]
  Jaguar     → ["Jaguar (animal)", "Jaguar (car brand)"]

Multi-token names typically NOT naturally ambiguous. Default to [].

BATCH MODE: user message lists multiple names. Per-entry list.

Output JSON exactly:
{"results": [{"index": N, "paths": ["..."]}, ...]}
"""


PROMPTS = {
    "alias_gen":         _ALIAS_SYSTEM,
    "shell_classify":    _SHELL_SYSTEM,
    "suggest_disambig":  _SUGGEST_DISAMBIG_SYSTEM,
}


def _build_batch_user(task: str, names: list[str]) -> str:
    parts = "\n".join(f'[{i}] "{n}"' for i, n in enumerate(names, start=1))
    if task == "alias_gen":
        schema = '{"results": [{"index": N, "aliases": [...]}, ...]}'
    elif task == "shell_classify":
        schema = '{"results": [{"index": N, "is_shell": bool}, ...]}'
    elif task == "suggest_disambig":
        schema = '{"results": [{"index": N, "paths": [...]}, ...]}'
    else:
        raise ValueError(task)
    return (
        f"For each of the {len(names)} names below:\n\n{parts}\n\n"
        f"Return JSON: {schema}. Only the JSON."
    )


# ── cache (append-only JSONL, keyed by content hash) ──────────────

class Cache:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._d: dict[str, dict] = {}
        if path.exists():
            for line in path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                key = row.get("key")
                if key:
                    self._d[key] = row

    @staticmethod
    def key(model: str, task: str, names: list[str], temperature: float) -> str:
        h = hashlib.sha256()
        h.update(json.dumps([model, task, names, temperature], sort_keys=True).encode("utf-8"))
        return h.hexdigest()

    def get(self, key: str) -> dict | None:
        return self._d.get(key)

    def put(self, row: dict) -> None:
        self._d[row["key"]] = row
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row) + "\n")


# ── result dataclasses ────────────────────────────────────────────

@dataclass
class CallResult:
    model: str
    task: str
    name: str
    expected: dict
    response: dict | None
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float = 0.0
    latency_ms: int = 0
    error: str | None = None
    correct: bool | None = None


# ── scoring ────────────────────────────────────────────────────────

def _score(task: str, expected: dict, response: dict | None) -> bool:
    if response is None:
        return False
    if task == "alias_gen":
        raw = response.get("aliases", []) if isinstance(response, dict) else []
        emitted = {str(a).strip().lower() for a in raw if isinstance(a, str) and str(a).strip()}
        must_include = {s.lower() for s in expected.get("must_include", [])}
        must_exclude = {s.lower() for s in expected.get("must_exclude", [])}
        if not must_include.issubset(emitted):
            return False
        if emitted & must_exclude:
            return False
        return True
    if task == "shell_classify":
        got = bool(response.get("is_shell", False)) if isinstance(response, dict) else False
        return got == bool(expected.get("is_shell"))
    if task == "suggest_disambig":
        raw = response.get("paths", []) if isinstance(response, dict) else []
        emitted = [str(p).strip() for p in raw if isinstance(p, str) and str(p).strip()]
        expect_ambig = bool(expected.get("ambiguous", False))
        got_ambig = len(emitted) >= 2
        if got_ambig != expect_ambig:
            return False
        if expect_ambig:
            needles = [s.lower() for s in expected.get("must_include_any", [])]
            if needles:
                joined = " ".join(p.lower() for p in emitted)
                if not any(n in joined for n in needles):
                    return False
        return True
    return False


# ── one batched LLM call ──────────────────────────────────────────

async def _call_batch(
    gateway,  # unused; kept for signature stability
    model_id: str,
    task: str,
    items: list[BenchItem],
    *,
    max_tokens: int,
    temperature: float,
    timeout: int,
    cache: Cache,
    reasoning: dict | None = None,
    pricing: dict | None = None,
) -> tuple[list[CallResult], dict]:
    """Returns (per-item results, batch stats)."""
    names = [it.name for it in items]
    key = Cache.key(model_id, task, names, temperature)
    cached = cache.get(key)
    batch_stats: dict = {
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "cost_usd": 0.0,
        "latency_ms": 0,
        "error": None,
    }
    resp: dict | None = None
    if cached and "response" in cached:
        resp = cached.get("response")
        batch_stats.update({
            "prompt_tokens": cached.get("prompt_tokens", 0),
            "completion_tokens": cached.get("completion_tokens", 0),
            "cost_usd": cached.get("cost_usd", 0.0),
            "latency_ms": cached.get("latency_ms", 0),
            "error": cached.get("error"),
        })
    else:
        settings = get_settings()
        t0 = time.time()
        err: str | None = None
        prompt_tok = 0
        compl_tok = 0
        call_kwargs: dict = {
            "model": model_id,
            "messages": [
                {"role": "system", "content": PROMPTS[task]},
                {"role": "user", "content": _build_batch_user(task, names)},
            ],
            "temperature": temperature,
            "max_tokens": max_tokens,
            "api_key": settings.openrouter_api_key,
            "api_base": "https://openrouter.ai/api/v1",
            "response_format": {"type": "json_object"},
            "timeout": timeout,
        }
        if reasoning:
            # OpenRouter format: {"reasoning": {"effort": "none" | "minimal", ...}}
            call_kwargs["reasoning"] = reasoning
        try:
            response = await asyncio.wait_for(
                litellm.acompletion(**call_kwargs),
                timeout=timeout,
            )
            raw_text = response.choices[0].message.content or ""
            try:
                resp = json.loads(raw_text)
            except json.JSONDecodeError:
                # Salvage attempt — strip markdown fences if any
                txt = raw_text.strip()
                if txt.startswith("```"):
                    txt = txt.strip("`").split("\n", 1)[-1].rsplit("```", 1)[0].strip()
                try:
                    resp = json.loads(txt)
                except json.JSONDecodeError:
                    err = f"JSON parse failed (len={len(raw_text)}): {raw_text[:120]}"
            usage = getattr(response, "usage", None)
            if usage is not None:
                prompt_tok = int(getattr(usage, "prompt_tokens", 0) or 0)
                compl_tok = int(getattr(usage, "completion_tokens", 0) or 0)
        except asyncio.TimeoutError:
            err = f"timeout>{timeout}s"
        except Exception as exc:
            err = f"{type(exc).__name__}: {str(exc)[:300]}"
        latency_ms = int((time.time() - t0) * 1000)

        # Cost: use pricing override if provided, else LiteLLM
        cost_usd = 0.0
        if pricing:
            p_in = float(pricing.get("input", 0.0))
            p_out = float(pricing.get("output", 0.0))
            cost_usd = prompt_tok * p_in / 1_000_000 + compl_tok * p_out / 1_000_000
        elif err is None:
            try:
                cost_usd = float(litellm.completion_cost(completion_response=response) or 0.0)
            except Exception:
                cost_usd = 0.0

        batch_stats.update({
            "prompt_tokens": prompt_tok,
            "completion_tokens": compl_tok,
            "cost_usd": cost_usd,
            "latency_ms": latency_ms,
            "error": err,
        })
        row = {
            "key": key,
            "model": model_id,
            "task": task,
            "names": names,
            "response": resp,
            **batch_stats,
        }
        cache.put(row)

    # Map response entries back to items
    by_idx: dict[int, dict] = {}
    if isinstance(resp, dict):
        for r in resp.get("results", []) or []:
            if not isinstance(r, dict):
                continue
            try:
                idx = int(r.get("index"))
            except (TypeError, ValueError):
                continue
            by_idx[idx] = r

    # Apportion tokens / cost across the batch (per-item estimates only)
    n = max(1, len(items))
    per_item_prompt = batch_stats["prompt_tokens"] // n
    per_item_compl = batch_stats["completion_tokens"] // n
    per_item_cost = batch_stats["cost_usd"] / n

    per_item_results: list[CallResult] = []
    for i, it in enumerate(items, start=1):
        item_resp = by_idx.get(i)
        # pack a single-item-shaped response for scoring:
        if item_resp is None:
            scored_resp = None
        else:
            if task == "alias_gen":
                scored_resp = {"aliases": item_resp.get("aliases", [])}
            elif task == "shell_classify":
                scored_resp = {"is_shell": item_resp.get("is_shell", False)}
            elif task == "suggest_disambig":
                scored_resp = {"paths": item_resp.get("paths", [])}
            else:
                scored_resp = item_resp
        error = batch_stats["error"]
        if not error and item_resp is None:
            error = "missing_batch_entry"
        r = CallResult(
            model=model_id,
            task=task,
            name=it.name,
            expected=it.expected,
            response=scored_resp,
            prompt_tokens=per_item_prompt,
            completion_tokens=per_item_compl,
            cost_usd=per_item_cost,
            latency_ms=batch_stats["latency_ms"],
            error=error,
        )
        r.correct = _score(task, it.expected, scored_resp) if error is None else False
        per_item_results.append(r)
    return per_item_results, batch_stats


# ── orchestrator ───────────────────────────────────────────────────

async def run_bench(config_path: Path) -> None:
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    here = config_path.resolve().parent

    cache_path = here / str(config.get("cache_file", "bench_cache.jsonl"))
    out_path = here / str(config.get("output_html", "report.html"))
    cache = Cache(cache_path)

    models: list[dict] = config.get("models", []) or []
    tasks: list[str] = config.get("tasks", []) or []
    batch_size = int(config.get("batch_size", 10))
    temperature = float(config.get("temperature", 0.0))
    timeout = int(config.get("timeout_seconds", 120))
    concurrency = int(config.get("concurrency", 3))
    max_tokens_per_item = int(config.get("max_tokens_per_item", 80))
    pricing_map = config.get("pricing", {}) or {}

    # Build (model, task, batch) jobs
    jobs: list[tuple[str, str, str, list[BenchItem], dict | None, dict | None]] = []
    for m in models:
        mid = m["id"]
        label = m.get("label", mid)
        reasoning = m.get("reasoning")
        price = pricing_map.get(mid)
        for task in tasks:
            items = TASKS[task]
            for i in range(0, len(items), batch_size):
                batch = items[i : i + batch_size]
                jobs.append((mid, label, task, batch, reasoning, price))

    total_items = sum(len(b[3]) for b in jobs)
    print(f"Bench: {len(models)} model(s) × {len(tasks)} task(s), batch_size={batch_size}, "
          f"total batches={len(jobs)}, total items={total_items}")

    sem = asyncio.Semaphore(concurrency)

    async def _one(mid: str, lbl: str, tsk: str, batch: list[BenchItem],
                    reasoning: dict | None, price: dict | None):
        async with sem:
            per_item, stats = await _call_batch(
                None, mid, tsk, batch,
                max_tokens=max(150, max_tokens_per_item * len(batch)),
                temperature=temperature,
                timeout=timeout,
                cache=cache,
                reasoning=reasoning,
                pricing=price,
            )
            return lbl, per_item, stats, tsk, len(batch)

    results_by_model: dict[str, list[CallResult]] = {}
    completed_batches = 0
    for coro in asyncio.as_completed([
        _one(mid, lbl, tsk, batch, reasoning, price)
        for mid, lbl, tsk, batch, reasoning, price in jobs
    ]):
        lbl, per_item, stats, tsk, n = await coro
        results_by_model.setdefault(lbl, []).extend(per_item)
        completed_batches += 1
        if stats["error"]:
            print(f"  [{completed_batches:3d}/{len(jobs)}] ERR  {lbl[:30]:30} {tsk:20} "
                  f"(batch n={n}) {stats['error']}")
        else:
            correct = sum(1 for r in per_item if r.correct)
            print(f"  [{completed_batches:3d}/{len(jobs)}]      {lbl[:30]:30} {tsk:20} "
                  f"(batch n={n})  {correct}/{n} correct  "
                  f"{stats['latency_ms']:5d}ms  ${stats['cost_usd']:.5f}")

    generate_report(config, results_by_model, out_path)


def main() -> None:
    here = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default=str(here / "config.yaml"))
    args = parser.parse_args()
    asyncio.run(run_bench(Path(args.config)))


if __name__ == "__main__":
    main()
