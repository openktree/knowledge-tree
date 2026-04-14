"""Dump seed families from a write-db to JSON fixtures.

Pulls, for each named seed:
- the canonical seed row
- all seeds merged into it (transitively, via merged_into_key chains)
- all seeds routed from it (parent_seed_key) and vice versa
- WriteSeedFact rows joined with WriteFact content for each family member
- WriteSeedMerge audit rows

Usage:
    uv run --project services/api python experiments/big_seed_dedup/dump_seeds.py \\
        --names nate,alice \\
        --label local \\
        --out experiments/big_seed_dedup/fixtures

Or with explicit DB URL (prod via kubectl port-forward):
    --db postgresql+asyncpg://kt:PW@localhost:15433/knowledge_tree_write
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from dotenv import load_dotenv
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

load_dotenv(Path(__file__).resolve().parents[2] / ".env")

from kt_db.write_models import (  # noqa: E402
    WriteFact,
    WriteSeed,
    WriteSeedFact,
    WriteSeedMerge,
    WriteSeedRoute,
)


def _jsonable(val: Any) -> Any:
    if isinstance(val, datetime):
        return val.isoformat()
    if isinstance(val, UUID):
        return str(val)
    if isinstance(val, (list, tuple)):
        return [_jsonable(v) for v in val]
    if isinstance(val, dict):
        return {k: _jsonable(v) for k, v in val.items()}
    return val


async def _find_seeds_by_name(session: AsyncSession, name: str) -> list[WriteSeed]:
    stmt = select(WriteSeed).where(WriteSeed.name.ilike(name))
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def _collect_family(session: AsyncSession, seed: WriteSeed) -> list[WriteSeed]:
    """BFS: find all seeds related via merged_into_key or routes."""
    visited: dict[str, WriteSeed] = {seed.key: seed}
    queue: list[str] = [seed.key]

    while queue:
        key = queue.pop(0)

        # seeds merged INTO this one
        r = await session.execute(select(WriteSeed).where(WriteSeed.merged_into_key == key))
        for s in r.scalars().all():
            if s.key not in visited:
                visited[s.key] = s
                queue.append(s.key)

        # this seed may have been merged into another
        current = visited[key]
        if current.merged_into_key and current.merged_into_key not in visited:
            r = await session.execute(select(WriteSeed).where(WriteSeed.key == current.merged_into_key))
            parent = r.scalar_one_or_none()
            if parent:
                visited[parent.key] = parent
                queue.append(parent.key)

        # routes: parent → children
        r = await session.execute(select(WriteSeedRoute).where(WriteSeedRoute.parent_seed_key == key))
        for route in r.scalars().all():
            if route.child_seed_key not in visited:
                r2 = await session.execute(select(WriteSeed).where(WriteSeed.key == route.child_seed_key))
                child = r2.scalar_one_or_none()
                if child:
                    visited[child.key] = child
                    queue.append(child.key)

        # child → parent
        r = await session.execute(select(WriteSeedRoute).where(WriteSeedRoute.child_seed_key == key))
        for route in r.scalars().all():
            if route.parent_seed_key not in visited:
                r2 = await session.execute(select(WriteSeed).where(WriteSeed.key == route.parent_seed_key))
                parent = r2.scalar_one_or_none()
                if parent:
                    visited[parent.key] = parent
                    queue.append(parent.key)

    return list(visited.values())


async def _facts_for_seed(session: AsyncSession, seed_key: str) -> list[dict[str, Any]]:
    stmt = (
        select(WriteFact, WriteSeedFact)
        .join(WriteSeedFact, WriteSeedFact.fact_id == WriteFact.id)
        .where(WriteSeedFact.seed_key == seed_key)
        .order_by(WriteSeedFact.created_at.asc())
    )
    result = await session.execute(stmt)
    out: list[dict[str, Any]] = []
    for fact, link in result.all():
        out.append(
            {
                "id": str(fact.id),
                "content": fact.content,
                "extraction_role": link.extraction_role,
                "confidence": float(link.confidence or 0.0),
                "created_at": link.created_at.isoformat() if link.created_at else None,
            }
        )
    return out


async def _merges_touching(session: AsyncSession, keys: list[str]) -> list[dict[str, Any]]:
    stmt = (
        select(WriteSeedMerge)
        .where(
            (WriteSeedMerge.source_seed_key.in_(keys)) | (WriteSeedMerge.target_seed_key.in_(keys))
        )
        .order_by(WriteSeedMerge.created_at.asc())
    )
    result = await session.execute(stmt)
    out: list[dict[str, Any]] = []
    for m in result.scalars().all():
        out.append(
            {
                "operation": m.operation,
                "source": m.source_seed_key,
                "target": m.target_seed_key,
                "reason": m.reason,
                "fact_ids_moved": list(m.fact_ids_moved or []),
                "created_at": m.created_at.isoformat() if m.created_at else None,
            }
        )
    return out


def _seed_to_dict(seed: WriteSeed) -> dict[str, Any]:
    return {
        "key": seed.key,
        "name": seed.name,
        "node_type": seed.node_type,
        "entity_subtype": seed.entity_subtype,
        "status": seed.status,
        "merged_into_key": seed.merged_into_key,
        "promoted_node_key": seed.promoted_node_key,
        "fact_count": seed.fact_count,
        "metadata": _jsonable(seed.metadata_ or {}),
        "created_at": seed.created_at.isoformat() if seed.created_at else None,
        "updated_at": seed.updated_at.isoformat() if seed.updated_at else None,
    }


async def _trigram_neighbors(
    session: AsyncSession, name: str, node_type: str, limit: int, threshold: float
) -> list[WriteSeed]:
    """Pull trigram-similar seeds (any status) to expand the candidate pool."""
    stmt = (
        select(WriteSeed)
        .where(
            WriteSeed.node_type == node_type,
            func.similarity(WriteSeed.name, name) >= threshold,
        )
        .order_by(func.similarity(WriteSeed.name, name).desc())
        .limit(limit)
    )
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def dump_one(
    session: AsyncSession,
    name: str,
    out_dir: Path,
    label: str,
    *,
    fan_out: int,
    trigram_threshold: float,
) -> Path | None:
    hits = await _find_seeds_by_name(session, name)
    if not hits:
        print(f"  [skip] no seed named '{name}'")
        return None

    # Pick the one with the most facts as canonical anchor
    hits.sort(key=lambda s: -(s.fact_count or 0))
    anchor = hits[0]
    family = await _collect_family(session, anchor)

    # Include any other same-name seeds found (may be disjoint families)
    for h in hits:
        if h.key not in {s.key for s in family}:
            extra = await _collect_family(session, h)
            for s in extra:
                if s.key not in {f.key for f in family}:
                    family.append(s)

    # Fan out: pull trigram-similar neighbors so multiplexer sees realistic mix.
    seen = {s.key for s in family}
    neighbors = await _trigram_neighbors(
        session, anchor.name, anchor.node_type, limit=fan_out, threshold=trigram_threshold
    )
    for n in neighbors:
        if n.key not in seen:
            family.append(n)
            seen.add(n.key)

    members: list[dict[str, Any]] = []
    for s in family:
        members.append({**_seed_to_dict(s), "facts": await _facts_for_seed(session, s.key)})

    merges = await _merges_touching(session, [s.key for s in family])

    fixture = {
        "fixture_label": label,
        "target_name": name,
        "canonical": _seed_to_dict(anchor),
        "family_size": len(family),
        "family": members,
        "current_merges": merges,
    }

    out_path = out_dir / f"{label}__{_safe(name)}.json"
    out_path.write_text(json.dumps(fixture, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"  [ok]   {out_path.name}  family={len(family)}  facts={sum(len(m['facts']) for m in members)}")
    return out_path


def _safe(s: str) -> str:
    return "".join(c if c.isalnum() or c in ("-", "_") else "_" for c in s.lower())[:60]


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", help="Async SQLAlchemy URL for the write-db. Defaults to settings.write_database_url.")
    parser.add_argument("--names", required=True, help="Comma-separated seed names to dump")
    parser.add_argument("--label", required=True, help="Fixture label prefix, e.g. 'prod' or 'local'")
    parser.add_argument("--out", default=str(Path(__file__).resolve().parent / "fixtures"))
    parser.add_argument("--fan-out", type=int, default=80,
                        help="Max trigram-similar seeds to include beyond the family (for realistic replay)")
    parser.add_argument("--trigram-threshold", type=float, default=0.30,
                        help="pg_trgm similarity cutoff for neighbor fan-out")
    args = parser.parse_args()

    if args.db:
        url = args.db
    else:
        from kt_config.settings import get_settings

        url = get_settings().write_database_url

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"Connecting: {url.split('@')[-1] if '@' in url else url}")
    engine = create_async_engine(url, pool_pre_ping=True)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    names = [n.strip() for n in args.names.split(",") if n.strip()]
    async with maker() as session:
        for name in names:
            print(f"- {name}")
            try:
                await dump_one(
                    session, name, out_dir, args.label,
                    fan_out=args.fan_out, trigram_threshold=args.trigram_threshold,
                )
            except Exception as exc:
                print(f"  [ERR] {exc}", file=sys.stderr)

    await engine.dispose()


if __name__ == "__main__":
    os.environ.setdefault("KT_SKIP_DOTENV", "")
    asyncio.run(main())
