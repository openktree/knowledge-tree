"""Quick benchmark for the shell classifier.

Compares three prompt variants on 20 hand-picked names (10 shell,
10 non-shell):
  A. current — {is_shell, shell_reason}, medium reasoning
  B. no-reason — {is_shell} only, medium reasoning
  C. no-reason + low reasoning effort (if supported by model)

Reports accuracy, cost, and latency per variant. Uses a SEPARATE cache
file so it doesn't pollute the main experiment cache.

Run:
    uv run --project services/api python -m experiments.big_seed_from_facts.bench_shell
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[2] / ".env")

from kt_models.embeddings import EmbeddingService  # noqa: E402
from kt_models.gateway import ModelGateway  # noqa: E402
from kt_models.usage import start_usage_tracking, stop_usage_tracking  # noqa: E402

from experiments.big_seed_dedup.llm import LLMRunner  # noqa: E402

SHELL_TRUTH = [
    ("method", True), ("approach", True), ("way", True), ("issue", True),
    ("aspect", True), ("kind", True), ("type", True), ("lack", True),
    ("matter", True), ("thing", True),
]
NON_SHELL_TRUTH = [
    ("leadership", False), ("life", False), ("income", False),
    ("consciousness", False), ("anxiety", False), ("democracy", False),
    ("capitalism", False), ("philosophy", False), ("justice", False),
    ("memory", False),
]
DATASET = SHELL_TRUTH + NON_SHELL_TRUTH

_BASE_RULE = """\
SHELL RULE — a noun is SHELL only when it cannot, in any domain
anywhere, serve as a legitimate topic of study, policy, or
substantive discourse.

Universal topic test: "in any domain whatsoever — philosophy,
science, economics, sociology, biology, psychology, self-help,
business, politics, everyday life — could a book, article, or
research project meaningfully have this noun as its subject?"
If yes → is_shell=false. If no → is_shell=true.

Shell words are pure propositional slots — they only acquire
meaning through a complement ("the METHOD of X", "the ASPECTS of
Y"). They are grammatical containers, not substantive concepts.

Examples — shell (pure containers): method, approach, way, kind,
sort, type, form, aspect, issue, matter, case, point, fact,
thing, item, respect.

NOT shell — always legitimate topics somewhere: consciousness,
anxiety, memory, democracy, capitalism, philosophy, psychology,
ethics, justice, freedom, life, leadership, income, knowledge,
education, religion, poverty, health.

Default: is_shell=FALSE. Flip true only when you are highly
confident. When uncertain, emit is_shell=false.

Multi-token names are NEVER shell.
"""

_SYS_WITH_REASON = (
    _BASE_RULE
    + "\nOutput JSON exactly:\n"
    + '{"is_shell": bool, "shell_reason": "brief justification"}\n'
)

_SYS_NO_REASON = (
    _BASE_RULE
    + "\nOutput JSON exactly (NO reasoning — bool only):\n"
    + '{"is_shell": bool}\n'
)


async def _call(
    runner: LLMRunner,
    name: str,
    system: str,
    *,
    reasoning_effort: str | None = None,
    max_tokens: int,
):
    t0 = time.time()
    acc = start_usage_tracking()
    try:
        resp = await runner.gateway.generate_json(
            model_id=runner.gateway.decomposition_model,
            messages=[{"role": "user", "content": f'Name: "{name}"\nReturn JSON only.'}],
            system_prompt=system,
            temperature=0.0,
            max_tokens=max_tokens,
            reasoning_effort=reasoning_effort,
        )
    finally:
        stop_usage_tracking()
    lat = int((time.time() - t0) * 1000)
    is_shell = bool(resp.get("is_shell", False)) if isinstance(resp, dict) else False
    return {
        "name": name,
        "is_shell": is_shell,
        "resp": resp,
        "prompt_tokens": acc.total_prompt_tokens if acc else 0,
        "completion_tokens": acc.total_completion_tokens if acc else 0,
        "cost": acc.total_cost_usd if acc else 0.0,
        "latency_ms": lat,
    }


async def run_variant(
    label: str,
    system: str,
    runner: LLMRunner,
    *,
    reasoning_effort: str | None,
    max_tokens: int,
):
    results = []
    for name, truth in DATASET:
        r = await _call(runner, name, system,
                        reasoning_effort=reasoning_effort, max_tokens=max_tokens)
        r["truth"] = truth
        r["correct"] = r["is_shell"] == truth
        results.append(r)
    return label, results


def _summarize(label: str, rows: list[dict]) -> None:
    correct = sum(1 for r in rows if r["correct"])
    tp = sum(1 for r in rows if r["truth"] and r["is_shell"])
    fp = sum(1 for r in rows if not r["truth"] and r["is_shell"])
    fn = sum(1 for r in rows if r["truth"] and not r["is_shell"])
    tot_prompt = sum(r["prompt_tokens"] for r in rows)
    tot_compl = sum(r["completion_tokens"] for r in rows)
    tot_cost = sum(r["cost"] for r in rows)
    tot_lat = sum(r["latency_ms"] for r in rows) / 1000

    print(f"\n=== {label} ===")
    print(f"  accuracy: {correct}/{len(rows)}  (TP={tp} FP={fp} FN={fn})")
    print(f"  tokens:   prompt={tot_prompt:,}  compl={tot_compl:,}")
    print(f"  cost:     ${tot_cost:.5f}  ({100 * tot_cost / max(1e-9, tot_cost):.0f}% ref)")
    print(f"  latency:  {tot_lat:.1f}s total, {tot_lat / len(rows):.2f}s/call")
    errors = [r for r in rows if not r["correct"]]
    if errors:
        print("  errors:")
        for r in errors:
            got = r["is_shell"]; want = r["truth"]
            reason = ""
            if isinstance(r["resp"], dict):
                reason = str(r["resp"].get("shell_reason", ""))[:80]
            print(f"    {r['name']:18} got={got} want={want}  {reason}")


async def main():
    gateway = ModelGateway()
    embedder = EmbeddingService()
    bench_cache = Path(__file__).resolve().parent / "bench_shell_cache.jsonl"
    if bench_cache.exists():
        bench_cache.unlink()
    runner = LLMRunner(gateway=gateway, embedder=embedder, cache_path=bench_cache)

    print(f"Model: {gateway.decomposition_model}  ·  dataset: {len(DATASET)} words")

    variants = [
        ("A. with reason (default)", _SYS_WITH_REASON, None, 150),
        ("B. no reason",             _SYS_NO_REASON,   None, 40),
        ("C. no reason + low think", _SYS_NO_REASON,   "low", 40),
    ]

    all_results = []
    for label, system, effort, max_tok in variants:
        all_results.append(
            await run_variant(label, system, runner,
                              reasoning_effort=effort, max_tokens=max_tok)
        )

    # Re-summarize referenced against variant A cost
    ref_cost = sum(r["cost"] for r in all_results[0][1]) or 1e-9
    for label, rows in all_results:
        _summarize(label, rows)
        tot_cost = sum(r["cost"] for r in rows)
        print(f"  vs A:     {tot_cost / ref_cost:.2f}× cost")


if __name__ == "__main__":
    asyncio.run(main())
