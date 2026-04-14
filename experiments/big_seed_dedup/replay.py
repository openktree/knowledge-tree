"""Global-pipeline replay: flatten all fixtures into one stream, feed registry."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path as _Path
from typing import Any

from .alias_gen import generate_aliases_batch
from .big_seed import Fact, Registry
from .llm import LLMRunner
from .multiplex import intake
from .qdrant_index import QdrantIndex


@dataclass
class FixtureMember:
    fixture_label: str
    target_name: str
    name: str
    node_type: str
    status: str
    fact_count: int
    facts: list[Fact]
    created_at: str


def _fact_list(raw: list[dict[str, Any]]) -> list[Fact]:
    out: list[Fact] = []
    for f in raw:
        content = str(f.get("content", "")).strip()
        if not content:
            continue
        out.append(Fact(id=str(f.get("id", "")), content=content, source=str(f.get("extraction_role", ""))))
    return out


def load_fixtures(fixtures_dir: _Path) -> list[FixtureMember]:
    members: list[FixtureMember] = []
    for path in sorted(fixtures_dir.glob("*.json")):
        doc = json.loads(path.read_text(encoding="utf-8"))
        label = str(doc.get("fixture_label", path.stem))
        target = str(doc.get("target_name", ""))
        for m in doc.get("family", []):
            members.append(
                FixtureMember(
                    fixture_label=label,
                    target_name=target,
                    name=str(m.get("name", "")).strip(),
                    node_type=str(m.get("node_type", "entity")),
                    status=str(m.get("status", "")),
                    fact_count=int(m.get("fact_count") or 0),
                    facts=_fact_list(m.get("facts", []) or []),
                    created_at=str(m.get("created_at") or ""),
                )
            )
    return members


def order_stream(members: list[FixtureMember]) -> list[FixtureMember]:
    """Arrival-like order: by created_at (fallback: fact_count desc, then name)."""
    return sorted(
        members,
        key=lambda m: (m.created_at or "", -m.fact_count, m.name.lower()),
    )


async def run_pipeline(
    fixtures_dir: _Path,
    *,
    runner: LLMRunner,
    qdrant: QdrantIndex,
    alias_batch_size: int = 20,
    alias_concurrency: int = 5,
) -> Registry:
    registry = Registry()
    members = order_stream(load_fixtures(fixtures_dir))
    members = [m for m in members if m.name and m.facts]

    # dedup names for batch (first-wins; later name reuses get fresh alias_gen call per intake)
    seen: set[str] = set()
    batch_entries: list[tuple[str, list[Fact]]] = []
    for m in members:
        if m.name in seen:
            continue
        seen.add(m.name)
        batch_entries.append((m.name, m.facts))

    print(f"Pre-computing aliases for {len(batch_entries)} unique names "
          f"(batch_size={alias_batch_size}, concurrency={alias_concurrency})...")
    alias_cache = await generate_aliases_batch(
        batch_entries, runner=runner, chunk_size=alias_batch_size, concurrency=alias_concurrency
    )
    print(f"  alias_gen done: {len(alias_cache)} entries")

    # Pre-embed all canonical names + their generated aliases to fully warm cache.
    to_embed: list[str] = []
    for name, (aliases, _u, _r) in alias_cache.items():
        to_embed.append(name)
        to_embed.extend(aliases)
    print(f"Pre-embedding {len(set(to_embed))} unique strings...")
    await runner.embed_batch(to_embed)
    print("  embeddings done")

    step = 0
    for m in members:
        step += 1
        pre = alias_cache.get(m.name)
        decision = await intake(
            name=m.name,
            facts=m.facts,
            node_type=m.node_type,
            registry=registry,
            runner=runner,
            qdrant=qdrant,
            step=step,
            precomputed_aliases=pre,
        )
        registry.history.append(decision)

    return registry
