"""Replay a seed-family fixture through the big-seed multiplexer."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path as _Path
from typing import Any

from .big_seed import BigSeed, Fact
from .llm import LLMRunner
from .multiplex import admit, seed_from_first


@dataclass
class ReplayResult:
    fixture_path: _Path
    fixture_label: str
    target_name: str
    big_seed: BigSeed
    prod_merges: list[dict[str, Any]] = field(default_factory=list)
    members_processed: int = 0
    members_skipped: int = 0


def _facts_for(member: dict[str, Any]) -> list[Fact]:
    out: list[Fact] = []
    for f in member.get("facts", []):
        out.append(Fact(id=str(f.get("id")), content=str(f.get("content", "")).strip(),
                        source=str(f.get("extraction_role", ""))))
    return out


def _anchor_and_queue(fixture: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Pick the canonical anchor + ordered remaining members."""
    canonical = fixture.get("canonical", {})
    anchor_key = canonical.get("key")
    members = list(fixture.get("family", []))

    # Anchor = largest fact_count (already picked in dump, but re-sort defensively)
    members.sort(key=lambda m: (-(m.get("fact_count") or 0), m.get("created_at") or ""))
    if not members:
        return canonical, []
    # Make sure anchor is first; if anchor in members, lift it
    for i, m in enumerate(members):
        if m.get("key") == anchor_key:
            anchor = members.pop(i)
            return anchor, members
    # anchor not present in family list — synthesize from canonical dict
    return canonical, members


async def replay_fixture(path: _Path, runner: LLMRunner) -> ReplayResult:
    fixture = json.loads(path.read_text(encoding="utf-8"))
    anchor, queue = _anchor_and_queue(fixture)

    anchor_facts = _facts_for(anchor)
    big, _ = await seed_from_first(
        canonical_name=str(anchor.get("name", "")),
        node_type=str(anchor.get("node_type", "entity")),
        facts=anchor_facts,
        runner=runner,
    )

    result = ReplayResult(
        fixture_path=path,
        fixture_label=str(fixture.get("fixture_label", "")),
        target_name=str(fixture.get("target_name", "")),
        big_seed=big,
        prod_merges=list(fixture.get("current_merges", [])),
    )

    step = 1
    for member in queue:
        facts = _facts_for(member)
        # Skip members with no facts — nothing for the LLM to reason over
        if not facts:
            result.members_skipped += 1
            continue
        name = str(member.get("name", "")).strip()
        if not name:
            result.members_skipped += 1
            continue
        dec = await admit(big, name, facts, runner=runner, step=step)
        big.history.append(dec)
        result.members_processed += 1
        step += 1

    return result
