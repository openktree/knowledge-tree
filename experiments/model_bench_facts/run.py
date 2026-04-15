"""Fact-extraction bench: each model extracts facts from 10 sources; score
via embedding similarity to hand-curated ground truth.

- hit: emitted fact cosine >= config.hit_threshold with a good_fact → +1
- blacklist: emitted cosine >= config.blacklist_threshold with a bad_fact → -1
- miss: each good_fact with no emitted match counts as 0 (not negative)

Score per source per model = hits − blacklist_hits.
Ceiling per source = len(good_facts).
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


_SYSTEM = """\
You extract atomic, self-explanatory factual statements from a source text
for a knowledge graph.

Each fact MUST:
- be a single declarative sentence;
- contain explicit subjects, names, dates, and quantities (no "the
  study", "it", "this", "they" without a clear antecedent inside the
  fact itself);
- stand alone — a reader who has NOT seen the source should understand
  what the fact is about from the sentence alone;
- come from the source text (do not invent).

Skip meta-commentary (browser errors, page metadata, author bios, view
counts, document-viewer navigation).

Output JSON exactly:
{"facts": ["fact 1", "fact 2", ...]}
"""


def _hash(model: str, source_id: int) -> str:
    return hashlib.sha256(f"{model}:{source_id}".encode("utf-8")).hexdigest()


# ── cache ────────────────────────────────────────────────────────

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
                if "key" in row:
                    self._d[row["key"]] = row

    def get(self, key: str) -> dict | None:
        return self._d.get(key)

    def put(self, row: dict) -> None:
        self._d[row["key"]] = row
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row) + "\n")


# ── JSON extraction (robust) ─────────────────────────────────────

_FENCE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL)


def _extract_json(raw: str) -> dict | None:
    if not raw:
        return None
    raw = raw.strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass
    m = _FENCE.search(raw)
    if m:
        try:
            return json.loads(m.group(1).strip())
        except json.JSONDecodeError:
            pass
    # fall back to first {...}
    depth = 0
    start = raw.find("{")
    if start >= 0:
        for i in range(start, len(raw)):
            if raw[i] == "{":
                depth += 1
            elif raw[i] == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(raw[start : i + 1])
                    except json.JSONDecodeError:
                        break
    return None


# ── one call ─────────────────────────────────────────────────────

@dataclass
class SourceResult:
    model: str
    source_id: int
    title: str
    emitted_facts: list[str]
    hits: list[tuple[str, str, float]] = field(default_factory=list)  # (emitted, gt_fact, score)
    blacklist_hits: list[tuple[str, str, float]] = field(default_factory=list)
    missed_gt: list[str] = field(default_factory=list)
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float = 0.0
    latency_ms: int = 0
    error: str | None = None


async def _extract(
    model_id: str,
    source: dict,
    *,
    max_tokens: int,
    temperature: float,
    timeout: int,
    reasoning: dict | None,
    pricing: dict | None,
    cache: Cache,
) -> tuple[list[str], dict]:
    key = hashlib.sha256(json.dumps([model_id, source["id"], source.get("raw_content", "")[:2000]], sort_keys=True).encode()).hexdigest()
    cached = cache.get(key)
    if cached is not None:
        return cached.get("facts", []) or [], {
            "prompt_tokens": cached.get("prompt_tokens", 0),
            "completion_tokens": cached.get("completion_tokens", 0),
            "cost_usd": cached.get("cost_usd", 0.0),
            "latency_ms": cached.get("latency_ms", 0),
            "error": cached.get("error"),
        }

    settings = get_settings()
    call_kwargs: dict = {
        "model": model_id,
        "messages": [
            {"role": "system", "content": _SYSTEM},
            {"role": "user", "content": f"Source title: {source.get('title', '')}\n\nSource text:\n\n{source['raw_content']}"},
        ],
        "temperature": temperature,
        "max_tokens": max_tokens,
        "api_key": settings.openrouter_api_key,
        "api_base": "https://openrouter.ai/api/v1",
        "response_format": {"type": "json_object"},
        "timeout": timeout,
    }
    if reasoning:
        call_kwargs["reasoning"] = reasoning

    t0 = time.time()
    err = None
    prompt_tok = compl_tok = 0
    facts: list[str] = []
    try:
        response = await asyncio.wait_for(litellm.acompletion(**call_kwargs), timeout=timeout)
        raw_text = response.choices[0].message.content or ""
        raw_json = _extract_json(raw_text)
        if isinstance(raw_json, dict):
            raw_facts = raw_json.get("facts", [])
            if isinstance(raw_facts, list):
                facts = [str(f).strip() for f in raw_facts if isinstance(f, str) and str(f).strip()]
        usage = getattr(response, "usage", None)
        if usage is not None:
            prompt_tok = int(getattr(usage, "prompt_tokens", 0) or 0)
            compl_tok = int(getattr(usage, "completion_tokens", 0) or 0)
    except asyncio.TimeoutError:
        err = f"timeout>{timeout}s"
    except Exception as exc:
        err = f"{type(exc).__name__}: {str(exc)[:200]}"
    latency_ms = int((time.time() - t0) * 1000)

    cost = 0.0
    if pricing:
        cost = prompt_tok * float(pricing.get("input", 0)) / 1_000_000 + compl_tok * float(pricing.get("output", 0)) / 1_000_000

    cache.put({
        "key": key,
        "model": model_id,
        "source_id": source["id"],
        "facts": facts,
        "prompt_tokens": prompt_tok,
        "completion_tokens": compl_tok,
        "cost_usd": cost,
        "latency_ms": latency_ms,
        "error": err,
    })
    return facts, {
        "prompt_tokens": prompt_tok,
        "completion_tokens": compl_tok,
        "cost_usd": cost,
        "latency_ms": latency_ms,
        "error": err,
    }


# ── embedding + matching ────────────────────────────────────────

def _cos(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


async def _score_source(
    result: SourceResult,
    good_facts: list[str],
    bad_facts: list[str],
    embedder: EmbeddingService,
    emb_cache: dict[str, list[float]],
    hit_threshold: float,
    blacklist_threshold: float,
) -> None:
    all_texts: list[str] = []
    for t in result.emitted_facts + good_facts + bad_facts:
        if t not in emb_cache:
            all_texts.append(t)
    # batch embed any missing
    if all_texts:
        vecs = await embedder.embed_batch(all_texts)
        for t, v in zip(all_texts, vecs):
            emb_cache[t] = v

    emitted_vecs = [emb_cache[t] for t in result.emitted_facts]
    good_vecs = [emb_cache[t] for t in good_facts]
    bad_vecs = [emb_cache[t] for t in bad_facts]

    matched_good: set[int] = set()
    for i, ev in enumerate(emitted_vecs):
        # good fact match
        best_good_score = -1.0
        best_good_idx = -1
        for gi, gv in enumerate(good_vecs):
            s = _cos(ev, gv)
            if s > best_good_score:
                best_good_score = s
                best_good_idx = gi
        if best_good_score >= hit_threshold and best_good_idx >= 0 and best_good_idx not in matched_good:
            result.hits.append((result.emitted_facts[i], good_facts[best_good_idx], best_good_score))
            matched_good.add(best_good_idx)
            continue
        # blacklist match
        best_bad_score = -1.0
        best_bad_idx = -1
        for bi, bv in enumerate(bad_vecs):
            s = _cos(ev, bv)
            if s > best_bad_score:
                best_bad_score = s
                best_bad_idx = bi
        if best_bad_score >= blacklist_threshold and best_bad_idx >= 0:
            result.blacklist_hits.append((result.emitted_facts[i], bad_facts[best_bad_idx], best_bad_score))

    for gi, gf in enumerate(good_facts):
        if gi not in matched_good:
            result.missed_gt.append(gf)


# ── orchestrator ─────────────────────────────────────────────────

async def run_bench(config_path: Path) -> None:
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    here = config_path.resolve().parent

    sources_raw = json.loads((here / str(config.get("sources_fixture", "fixtures/sources_10.json"))).read_text())["sources"]
    # Assign sequential idx 1..N; GT is keyed by this idx
    sources = [{**s, "idx": i + 1} for i, s in enumerate(sources_raw)]
    gt = yaml.safe_load((here / str(config.get("ground_truth", "fixtures/ground_truth.yaml"))).read_text())
    gt_by_id = {int(s["id"]): s for s in gt.get("sources", [])}

    cache = Cache(here / str(config.get("cache_file", "bench_cache.jsonl")))
    embedder = EmbeddingService()
    emb_cache_path = here / str(config.get("emb_cache_file", "emb_cache.json"))
    emb_cache: dict[str, list[float]] = {}
    if emb_cache_path.exists():
        try:
            emb_cache = json.loads(emb_cache_path.read_text(encoding="utf-8"))
            print(f"Loaded {len(emb_cache)} cached embeddings from {emb_cache_path.name}")
        except Exception:
            emb_cache = {}

    models = config.get("models", []) or []
    pricing_map = config.get("pricing", {}) or {}
    max_tokens = int(config.get("max_tokens", 3000))
    temperature = float(config.get("temperature", 0.0))
    timeout = int(config.get("timeout_seconds", 180))
    concurrency = int(config.get("concurrency", 3))
    hit_thr = float(config.get("hit_threshold", 0.95))
    bl_thr = float(config.get("blacklist_threshold", 0.92))

    sem = asyncio.Semaphore(concurrency)

    async def one(m: dict, source: dict) -> SourceResult:
        async with sem:
            facts, stats = await _extract(
                m["id"], source,
                max_tokens=max_tokens, temperature=temperature, timeout=timeout,
                reasoning=m.get("reasoning"),
                pricing=pricing_map.get(m["id"]),
                cache=cache,
            )
            r = SourceResult(
                model=m.get("label", m["id"]),
                source_id=source["idx"],
                title=source.get("title") or "",
                emitted_facts=facts,
                prompt_tokens=stats["prompt_tokens"],
                completion_tokens=stats["completion_tokens"],
                cost_usd=stats["cost_usd"],
                latency_ms=stats["latency_ms"],
                error=stats["error"],
            )
            return r

    jobs = [one(m, s) for m in models for s in sources]
    print(f"Bench: {len(models)} model(s) × {len(sources)} source(s) = {len(jobs)} calls")

    results_by_model: dict[str, list[SourceResult]] = {}
    for coro in asyncio.as_completed(jobs):
        r = await coro
        # Score now (sequential is fine; embed call batches internally)
        src_gt = gt_by_id.get(r.source_id, {})
        good = src_gt.get("good_facts", []) or []
        bad = src_gt.get("bad_facts", []) or []
        if r.error is None:
            await _score_source(r, good, bad, embedder, emb_cache, hit_thr, bl_thr)
        results_by_model.setdefault(r.model, []).append(r)
        status = f"{len(r.hits)}✓/{len(good)}  -{len(r.blacklist_hits)}✗" if r.error is None else "ERR"
        print(f"  [{r.model[:30]:30}] src{r.source_id:2}  {r.title[:35]:35}  {status:12}  ${r.cost_usd:.5f}  {r.latency_ms:5d}ms")

    emb_cache_path.write_text(json.dumps(emb_cache), encoding="utf-8")
    print(f"Saved {len(emb_cache)} embeddings to {emb_cache_path.name}")

    out_path = here / str(config.get("output_html", "report.html"))
    generate_report(config, results_by_model, gt_by_id, out_path)


def main() -> None:
    here = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default=str(here / "config.yaml"))
    args = parser.parse_args()
    asyncio.run(run_bench(Path(args.config)))


if __name__ == "__main__":
    main()
