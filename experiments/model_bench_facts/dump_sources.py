"""Dump N diverse sources from local write-db for the fact-extraction bench.

Picks sources with real raw_content, reasonable length (500-6000 chars),
distinct providers / titles for diversity. Writes a stable JSON fixture
for deterministic replay.

Usage:
    uv run --project services/api python -m experiments.model_bench_facts.dump_sources --n 10
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

load_dotenv(Path(__file__).resolve().parents[2] / ".env")

from kt_db.write_models import WriteRawSource  # noqa: E402


async def _pull(session: AsyncSession, n: int, min_chars: int, max_chars: int) -> list[dict]:
    stmt = (
        select(WriteRawSource)
        .where(
            and_(
                WriteRawSource.raw_content.isnot(None),
                func.length(WriteRawSource.raw_content) >= min_chars,
                func.length(WriteRawSource.raw_content) <= max_chars,
                WriteRawSource.is_full_text == True,  # noqa: E712
            )
        )
        .order_by(func.random())
        .limit(n * 4)  # oversample to hit diversity filter
    )
    rows = list((await session.execute(stmt)).scalars().all())

    # Diversity: unique provider_id × unique title stem. Take at most 2 per provider.
    seen_provider: dict[str, int] = {}
    picked: list[WriteRawSource] = []
    for r in rows:
        p = r.provider_id or "unknown"
        if seen_provider.get(p, 0) >= 2:
            continue
        seen_provider[p] = seen_provider.get(p, 0) + 1
        picked.append(r)
        if len(picked) >= n:
            break
    # If diversity couldn't fill n, backfill from remainder
    remainder = [r for r in rows if r not in picked]
    while len(picked) < n and remainder:
        picked.append(remainder.pop(0))

    out: list[dict] = []
    for s in picked[:n]:
        out.append({
            "id": str(s.id),
            "uri": s.uri,
            "title": s.title,
            "provider_id": s.provider_id,
            "raw_content": s.raw_content,
            "length": len(s.raw_content or ""),
        })
    return out


async def main_async(n: int, out: Path, min_chars: int, max_chars: int) -> None:
    from kt_config.settings import get_settings

    url = get_settings().write_database_url
    print(f"Connecting: {url.split('@')[-1]}")
    engine = create_async_engine(url, pool_pre_ping=True)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with maker() as session:
        sources = await _pull(session, n, min_chars, max_chars)
    await engine.dispose()

    payload = {"n": len(sources), "min_chars": min_chars, "max_chars": max_chars, "sources": sources}
    out.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"[ok] {out}  n={len(sources)}")
    for s in sources:
        title = (s.get("title") or "(untitled)")[:80]
        print(f"  [{s['provider_id']:20}] {s['length']:>5} chars  {title}")


def main() -> None:
    here = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--n", type=int, default=10)
    parser.add_argument("--min-chars", type=int, default=500)
    parser.add_argument("--max-chars", type=int, default=6000)
    parser.add_argument("--out", default=str(here / "fixtures" / "sources_10.json"))
    args = parser.parse_args()
    asyncio.run(main_async(args.n, Path(args.out), args.min_chars, args.max_chars))


if __name__ == "__main__":
    main()
