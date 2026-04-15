"""Run big-seed v2 pipeline on all fixtures, produce global report.

Usage:
    uv run --project services/api python -m experiments.big_seed_dedup.run \\
        --fixtures experiments/big_seed_dedup/fixtures \\
        --out experiments/big_seed_dedup/report.html \\
        --reset-qdrant
"""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[2] / ".env")

from kt_models.embeddings import EmbeddingService  # noqa: E402
from kt_models.gateway import ModelGateway  # noqa: E402

from .llm import LLMRunner  # noqa: E402
from .qdrant_index import QdrantIndex  # noqa: E402
from .replay import run_pipeline  # noqa: E402
from .report import generate_report  # noqa: E402


async def main_async(
    fixtures: Path, out: Path, cache: Path, reset_qdrant: bool, model_override: str | None
) -> None:
    gateway = ModelGateway()
    if model_override:
        gateway.decomposition_model = model_override
    embedder = EmbeddingService()
    runner = LLMRunner(gateway=gateway, embedder=embedder, cache_path=cache)

    qdrant = QdrantIndex()
    await qdrant.ensure(reset=reset_qdrant)

    print(f"Running pipeline on {fixtures} (reset_qdrant={reset_qdrant})")
    registry = await run_pipeline(fixtures, runner=runner, qdrant=qdrant)

    print(f"\nRegistry: {len(registry.big_seeds)} big-seed(s), {len(registry.history)} decisions")
    for b in registry.big_seeds:
        path_info = f"paths={len(b.paths)}" if b.paths else "flat"
        print(f"  - [{b.id}] {b.canonical_name!r} ({b.node_type}) aliases={len(b.aliases)} {path_info}")

    generate_report(registry, out, model_name=gateway.decomposition_model)


def main() -> None:
    here = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixtures", default=str(here / "fixtures"))
    parser.add_argument("--out", default=str(here / "report.html"))
    parser.add_argument("--cache", default=str(here / "llm_cache.jsonl"))
    parser.add_argument("--reset-qdrant", action="store_true",
                        help="Drop and recreate the experiment Qdrant collection before running.")
    parser.add_argument("--model", default=None,
                        help="Override LLM model id for alias_gen + multiplex (e.g. openrouter/openai/gpt-5-nano)")
    args = parser.parse_args()
    asyncio.run(main_async(
        Path(args.fixtures),
        Path(args.out),
        Path(args.cache),
        args.reset_qdrant,
        args.model,
    ))


if __name__ == "__main__":
    main()
