"""Dump N random seed names from local write-db for bench use.

Writes a JSON pool file used by datasets.py when `dataset: random_N`
is set. Ground truth per item is empty by default — models are judged
on raw emissions; next iteration curates the whitelist from results.

Usage:
    uv run --project services/api python -m experiments.model_bench.dump_bench_seeds --n 100
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

load_dotenv(Path(__file__).resolve().parents[2] / ".env")

from kt_db.write_models import WriteSeed  # noqa: E402


async def _pull(session: AsyncSession, n: int, min_facts: int) -> list[dict]:
    stmt = (
        select(WriteSeed)
        .where(
            WriteSeed.status.in_(["active", "promoted", "ambiguous"]),
            WriteSeed.fact_count >= min_facts,
        )
        .order_by(func.random())
        .limit(n)
    )
    result = await session.execute(stmt)
    rows = list(result.scalars().all())
    return [
        {"name": s.name, "node_type": s.node_type, "fact_count": int(s.fact_count or 0)}
        for s in rows
    ]


async def main_async(n: int, min_facts: int, out: Path) -> None:
    from kt_config.settings import get_settings

    url = get_settings().write_database_url
    print(f"Connecting: {url.split('@')[-1]}")
    engine = create_async_engine(url, pool_pre_ping=True)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with maker() as session:
        rows = await _pull(session, n, min_facts)
    await engine.dispose()

    # Dedup by name (case-insensitive) — different seeds can share names
    seen: set[str] = set()
    uniq: list[dict] = []
    for r in rows:
        k = r["name"].strip().lower()
        if k in seen:
            continue
        seen.add(k)
        uniq.append(r)

    payload = {
        "n": len(uniq),
        "min_facts": min_facts,
        "seeds": uniq,
    }
    out.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"[ok] {out}  n={len(uniq)} (requested {n}, deduped)")


def main() -> None:
    here = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--n", type=int, default=100)
    parser.add_argument("--min-facts", type=int, default=2)
    parser.add_argument("--out", default=str(here / "fixtures" / "random_seeds.json"))
    args = parser.parse_args()
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    asyncio.run(main_async(args.n, args.min_facts, out))


if __name__ == "__main__":
    main()
