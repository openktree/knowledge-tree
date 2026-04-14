"""Global-pipeline replay: flatten all fixtures into one stream, feed registry."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path as _Path
from typing import Any

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
) -> Registry:
    registry = Registry()
    members = order_stream(load_fixtures(fixtures_dir))

    step = 0
    for m in members:
        if not m.name:
            continue
        if not m.facts:
            # skip empty members — nothing for the LLM to reason about
            continue
        step += 1
        decision = await intake(
            name=m.name,
            facts=m.facts,
            node_type=m.node_type,
            registry=registry,
            runner=runner,
            qdrant=qdrant,
            step=step,
        )
        registry.history.append(decision)

    return registry
