"""Summarization bench: plain-text output, semantic coverage scoring.

Each model receives 20 facts about a seed and must write a summary
paragraph — no JSON, no bullet points, just flowing prose.

Score = mean cosine similarity between each source fact's embedding
and the summary embedding. Higher = denser coverage.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

import litellm
import yaml
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[2] / ".env")

from kt_config.settings import get_settings  # noqa: E402
from kt_models.embeddings import EmbeddingService  # noqa: E402

from .report import generate_report  # noqa: E402


@dataclass
class SourceResult:
    model: str
    seed_id: int
    seed: str
    summary: str
    prompt_tokens: int
    completion_tokens: int
    cost_usd: float
    latency_ms: int
    error: str | None = None
    coverage_mean: float = 0.0
    coverage_min: float = 0.0
    coverage_max: float = 0.0
    per_fact_cos: list[float] = field(default_factory=list)
    facts: list[str] = field(default_factory=list)


# ── cache ────────────────────────────────────────────────────────
class Cache:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._idx: dict[str, dict] = {}
        if path.exists():
            with path.open() as f:
                for line in f:
                    try:
                        row = json.loads(line)
                        self._idx[row["key"]] = row
                    except Exception:
                        pass

    @staticmethod
    def key(model: str, seed_id: int, fact_hash: str) -> str:
        h = hashlib.sha256(f"{model}|{seed_id}|{fact_hash}".encode()).hexdigest()[:16]
        return h

    def get(self, key: str) -> dict | None:
        return self._idx.get(key)

    def put(self, row: dict) -> None:
        self._idx[row["key"]] = row
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


# ── prompt ───────────────────────────────────────────────────────
_PROMPT = """\
Write a single concise summary paragraph (150-250 words) that integrates \
the following facts about "{seed}". The summary must cover as much of \
the factual content as possible in fluent, readable prose.

Output rules:
- Plain text only. No JSON, no bullets, no headers, no markdown.
- Single paragraph. No line breaks inside.
- Do not cite fact numbers.
- Do not invent facts beyond those provided.

Facts:
{facts}
"""


def _build_prompt(seed: str, facts: list[str]) -> str:
    numbered = "\n".join(f"{i+1}. {f}" for i, f in enumerate(facts))
    return _PROMPT.format(seed=seed, facts=numbered)


# ── extraction ───────────────────────────────────────────────────
async def _extract(
    model_id: str,
    seed: str,
    facts: list[str],
    max_tokens: int,
    temperature: float,
    timeout: int,
    reasoning: dict | None,
    pricing: dict | None,
) -> tuple[str, dict]:
    prompt = _build_prompt(seed, facts)
    stats: dict = {
        "prompt_tokens": 0, "completion_tokens": 0, "cost_usd": 0.0,
        "latency_ms": 0, "error": None,
    }
    t0 = time.perf_counter()
    kwargs: dict = {
        "model": model_id,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens,
        "temperature": temperature,
        "timeout": timeout,
    }
    if reasoning is not None:
        kwargs["reasoning"] = reasoning
    try:
        resp = await litellm.acompletion(**kwargs)
        msg = resp.choices[0].message
        text = (msg.content or "").strip()
        # Fallback: some providers (e.g. nitro gpt-oss) emit the entire
        # output as reasoning_content with empty content.
        if not text:
            rc = getattr(msg, "reasoning_content", None) or getattr(msg, "reasoning", None)
            if rc:
                text = str(rc).strip()
        usage = getattr(resp, "usage", None)
        pt = int(getattr(usage, "prompt_tokens", 0) or 0)
        ct = int(getattr(usage, "completion_tokens", 0) or 0)
        stats["prompt_tokens"] = pt
        stats["completion_tokens"] = ct
        if pricing:
            stats["cost_usd"] = (
                pt / 1_000_000 * float(pricing["input"])
                + ct / 1_000_000 * float(pricing["output"])
            )
        else:
            try:
                stats["cost_usd"] = float(litellm.completion_cost(completion_response=resp) or 0.0)
            except Exception:
                stats["cost_usd"] = 0.0
    except Exception as e:
        stats["error"] = f"{type(e).__name__}: {e}"[:400]
        text = ""
    stats["latency_ms"] = int((time.perf_counter() - t0) * 1000)
    return text, stats


# ── scoring ──────────────────────────────────────────────────────
def _cos(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


_SENT_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z])")


def _split_sentences(text: str) -> list[str]:
    # Simple splitter: on ./!/? followed by whitespace + capital. Good
    # enough for prose summaries. Filters out fragments shorter than 15 chars.
    parts = _SENT_SPLIT.split(text.strip())
    return [p.strip() for p in parts if len(p.strip()) >= 15]


async def _score(
    r: SourceResult,
    embedder: EmbeddingService,
    emb_cache: dict[str, list[float]],
) -> None:
    if r.error or not r.summary:
        return
    sentences = _split_sentences(r.summary) or [r.summary]
    texts_needed = [t for t in sentences + r.facts if t not in emb_cache]
    if texts_needed:
        vecs = await embedder.embed_batch(texts_needed)
        for t, v in zip(texts_needed, vecs):
            emb_cache[t] = v
    sent_vecs = [emb_cache[s] for s in sentences]
    # For each fact, find the BEST-matching sentence in the summary.
    sims: list[float] = []
    for f in r.facts:
        fv = emb_cache[f]
        best = max((_cos(sv, fv) for sv in sent_vecs), default=0.0)
        sims.append(best)
    r.per_fact_cos = sims
    if sims:
        r.coverage_mean = sum(sims) / len(sims)
        r.coverage_min = min(sims)
        r.coverage_max = max(sims)


# ── orchestrator ─────────────────────────────────────────────────
async def run_bench(config_path: Path) -> None:
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    here = config_path.resolve().parent

    fixtures = yaml.safe_load((here / str(config.get("sources_fixture"))).read_text())
    seeds = fixtures.get("seeds", [])

    cache = Cache(here / str(config.get("cache_file", "bench_cache.jsonl")))
    embedder = EmbeddingService()
    emb_cache_path = here / str(config.get("emb_cache_file", "emb_cache.json"))
    emb_cache: dict[str, list[float]] = {}
    if emb_cache_path.exists():
        try:
            emb_cache = json.loads(emb_cache_path.read_text(encoding="utf-8"))
            print(f"Loaded {len(emb_cache)} cached embeddings")
        except Exception:
            emb_cache = {}

    models = config.get("models", []) or []
    pricing_map = config.get("pricing", {}) or {}
    max_tokens = int(config.get("max_tokens", 800))
    temperature = float(config.get("temperature", 0.0))
    timeout = int(config.get("timeout_seconds", 180))
    concurrency = int(config.get("concurrency", 3))

    sem = asyncio.Semaphore(concurrency)

    async def one(m: dict, seed: dict) -> SourceResult:
        async with sem:
            facts = list(seed.get("facts", []) or [])
            fact_hash = hashlib.sha256("\n".join(facts).encode()).hexdigest()[:12]
            key = Cache.key(m["id"], int(seed["id"]), fact_hash)
            cached = cache.get(key)
            if cached:
                text = cached.get("summary", "")
                stats = {
                    "prompt_tokens": cached.get("prompt_tokens", 0),
                    "completion_tokens": cached.get("completion_tokens", 0),
                    "cost_usd": cached.get("cost_usd", 0.0),
                    "latency_ms": cached.get("latency_ms", 0),
                    "error": cached.get("error"),
                }
            else:
                text, stats = await _extract(
                    m["id"], seed["seed"], facts,
                    max_tokens=max_tokens, temperature=temperature, timeout=timeout,
                    reasoning=m.get("reasoning"),
                    pricing=pricing_map.get(m["id"]),
                )
                cache.put({
                    "key": key, "model": m["id"], "seed_id": seed["id"],
                    "fact_hash": fact_hash, "summary": text, **stats,
                })
            return SourceResult(
                model=m.get("label", m["id"]),
                seed_id=int(seed["id"]),
                seed=seed["seed"],
                summary=text,
                prompt_tokens=stats["prompt_tokens"],
                completion_tokens=stats["completion_tokens"],
                cost_usd=stats["cost_usd"],
                latency_ms=stats["latency_ms"],
                error=stats["error"],
                facts=facts,
            )

    jobs = [one(m, s) for m in models for s in seeds]
    print(f"Bench: {len(models)} model(s) × {len(seeds)} seed(s) = {len(jobs)} calls")

    results_by_model: dict[str, list[SourceResult]] = {}
    for coro in asyncio.as_completed(jobs):
        r = await coro
        if r.error is None:
            await _score(r, embedder, emb_cache)
        results_by_model.setdefault(r.model, []).append(r)
        cov = f"{r.coverage_mean:.3f}" if r.error is None else "ERR"
        print(f"  [{r.model[:30]:30}] seed{r.seed_id} {r.seed[:25]:25}  "
              f"cov={cov}  words={len(r.summary.split()):4}  ${r.cost_usd:.5f}  {r.latency_ms:5d}ms")

    emb_cache_path.write_text(json.dumps(emb_cache), encoding="utf-8")
    print(f"Saved {len(emb_cache)} embeddings")

    out_path = here / str(config.get("output_html", "report.html"))
    generate_report(config, results_by_model, seeds, out_path)


def main() -> None:
    here = Path(__file__).resolve().parent
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--config", default=str(here / "config.yaml"))
    args = p.parse_args()
    asyncio.run(run_bench(Path(args.config)))


if __name__ == "__main__":
    main()
