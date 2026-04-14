"""Run big-seed replay on all fixtures, produce HTML report.

Usage:
    uv run --project services/api python -m experiments.big_seed_dedup.run \\
        --fixtures experiments/big_seed_dedup/fixtures \\
        --out experiments/big_seed_dedup/report.html
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
from .replay import replay_fixture  # noqa: E402
from .report import generate_report  # noqa: E402


async def main_async(fixtures: Path, out: Path, cache: Path) -> None:
    gateway = ModelGateway()
    embedder = EmbeddingService()
    runner = LLMRunner(gateway=gateway, embedder=embedder, cache_path=cache)

    fixture_paths = sorted(fixtures.glob("*.json"))
    if not fixture_paths:
        print(f"No fixtures found in {fixtures}")
        return

    results = []
    for fp in fixture_paths:
        print(f"\n== Replay: {fp.name} ==")
        try:
            rr = await replay_fixture(fp, runner)
        except Exception as exc:
            print(f"  ERROR: {exc}")
            import traceback

            traceback.print_exc()
            continue
        print(
            f"  paths={len(rr.big_seed.paths)}  processed={rr.members_processed}  "
            f"skipped={rr.members_skipped}  decisions={len(rr.big_seed.history)}"
        )
        for p in rr.big_seed.paths:
            print(f"    - [{p.id}] {p.label}  known_aliases={len(p.known_aliases)}  merged={len(p.merged_surface_forms)}")
        results.append(rr)

    generate_report(results, out, model_name=gateway.decomposition_model)


def main() -> None:
    here = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixtures", default=str(here / "fixtures"))
    parser.add_argument("--out", default=str(here / "report.html"))
    parser.add_argument("--cache", default=str(here / "llm_cache.jsonl"))
    args = parser.parse_args()
    asyncio.run(main_async(Path(args.fixtures), Path(args.out), Path(args.cache)))


if __name__ == "__main__":
    main()
