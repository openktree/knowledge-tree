"""Dump N random raw facts (pre-dedup) from a write-db to JSON.

Usage:
    uv run --project services/api python -m experiments.big_seed_from_facts.dump_facts \\
        --db "$PROD_URL" --n 400 --label prod
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

load_dotenv(Path(__file__).resolve().parents[2] / ".env")

from kt_db.write_models import WriteFact  # noqa: E402


async def _pull_facts(session: AsyncSession, n: int, min_len: int) -> list[dict]:
    stmt = (
        select(WriteFact)
        .where(
            WriteFact.dedup_status == "ready",
            func.length(WriteFact.content) >= min_len,
        )
        .order_by(func.random())
        .limit(n)
    )
    result = await session.execute(stmt)
    rows = list(result.scalars().all())
    return [{"id": str(f.id), "content": f.content} for f in rows]


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db")
    parser.add_argument("--n", type=int, default=400)
    parser.add_argument("--min-len", type=int, default=40)
    parser.add_argument("--label", required=True)
    parser.add_argument("--out", default=str(Path(__file__).resolve().parent / "fixtures"))
    args = parser.parse_args()

    url = args.db
    if not url:
        from kt_config.settings import get_settings
        url = get_settings().write_database_url

    print(f"Connecting: {url.split('@')[-1] if '@' in url else url}")
    engine = create_async_engine(url, pool_pre_ping=True)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with maker() as session:
        try:
            facts = await _pull_facts(session, args.n, args.min_len)
        except Exception as exc:
            print(f"[ERR] {exc}", file=sys.stderr)
            await engine.dispose()
            return

    await engine.dispose()

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{args.label}__facts_{len(facts)}.json"
    payload = {
        "fixture_kind": "facts",
        "fixture_label": args.label,
        "n": len(facts),
        "facts": facts,
    }
    out_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"[ok] {out_path.name}  n={len(facts)}")


if __name__ == "__main__":
    asyncio.run(main())
