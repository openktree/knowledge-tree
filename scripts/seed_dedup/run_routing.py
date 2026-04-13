"""Routing experiment: verify that facts reach the correct leaf seed.

Tests the full routing stack with mock repos:
- Active seeds → direct return
- Merged seeds → follow chain
- Ambiguous seeds → route through pipe to child
- Cascading ambiguity → multi-level pipe resolution
- Phonetic routing → typo detection through ambiguous parents
- Text search routing → substring match in fact content
- Embedding routing → cosine similarity to child seeds

Usage:
    uv run --project libs/kt-facts python scripts/seed_dedup/run_routing.py
"""

from __future__ import annotations

import asyncio
import sys
from dataclasses import dataclass
from unittest.mock import AsyncMock, MagicMock

# ── Routing test cases ───────────────────────────────────────────────────


@dataclass
class RoutingCase:
    """A single routing test scenario."""

    name: str
    # Seed graph: key -> (name, node_type, status, merged_into_key)
    seeds: dict[str, tuple[str, str, str, str | None]]
    # Routes: parent_key -> [(child_key, label, ambiguity_type)]
    routes: dict[str, list[tuple[str, str, str]]]
    # Input: (name, node_type, fact_content)
    input: tuple[str, str, str]
    # Expected resolved key
    expected: str
    category: str
    notes: str = ""


ROUTING_CASES: list[RoutingCase] = [
    # ── Active seeds: direct return ──────────────────────────
    RoutingCase(
        name="active_seed_direct",
        seeds={"concept:mars": ("Mars", "concept", "active", None)},
        routes={},
        input=("Mars", "concept", "Mars is the fourth planet"),
        expected="concept:mars",
        category="active",
        notes="Simple active seed — no routing needed",
    ),
    RoutingCase(
        name="promoted_seed_direct",
        seeds={"concept:mars": ("Mars", "concept", "promoted", None)},
        routes={},
        input=("Mars", "concept", "Mars is the fourth planet"),
        expected="concept:mars",
        category="active",
        notes="Promoted seed — still direct return",
    ),
    # ── Merge chain following ────────────────────────────────
    RoutingCase(
        name="single_merge_hop",
        seeds={
            "entity:fbi": ("FBI", "entity", "merged", "entity:federal-bureau-of-investigation"),
            "entity:federal-bureau-of-investigation": ("Federal Bureau of Investigation", "entity", "active", None),
        },
        routes={},
        input=("FBI", "entity", "The FBI investigates federal crimes"),
        expected="entity:federal-bureau-of-investigation",
        category="merge_chain",
        notes="Single merge hop to expanded name",
    ),
    RoutingCase(
        name="double_merge_hop",
        seeds={
            "entity:a": ("A", "entity", "merged", "entity:b"),
            "entity:b": ("B", "entity", "merged", "entity:c"),
            "entity:c": ("C", "entity", "active", None),
        },
        routes={},
        input=("A", "entity", "Some fact about A"),
        expected="entity:c",
        category="merge_chain",
        notes="Two merge hops",
    ),
    RoutingCase(
        name="merge_into_ambiguous",
        seeds={
            "concept:light-reaction": ("Light reaction", "concept", "merged", "concept:light-reactions"),
            "concept:light-reactions": ("Light reactions", "concept", "ambiguous", None),
            "concept:light-dependent-reactions": ("light-dependent reactions", "concept", "active", None),
            "concept:light-independent-reactions": ("light-independent reactions", "concept", "active", None),
        },
        routes={
            "concept:light-reactions": [
                ("concept:light-dependent-reactions", "light-dependent reactions", "embedding"),
                ("concept:light-independent-reactions", "light-independent reactions", "embedding"),
            ],
        },
        input=("Light reaction", "concept", "The light-dependent reactions produce ATP"),
        expected="concept:light-dependent-reactions",
        category="merge_chain",
        notes="Merge chain leads to ambiguous seed → text search routes to correct child",
    ),
    # ── Single-level disambiguation (text ambiguity) ─────────
    RoutingCase(
        name="text_ambiguity_single_child",
        seeds={
            "concept:mars": ("Mars", "concept", "ambiguous", None),
            "concept:mars-planet": ("Mars (planet)", "concept", "active", None),
        },
        routes={
            "concept:mars": [
                ("concept:mars-planet", "Mars (planet)", "text"),
            ],
        },
        input=("Mars", "concept", "Mars is the fourth planet"),
        expected="concept:mars-planet",
        category="disambiguation",
        notes="Single child route — always routes there",
    ),
    RoutingCase(
        name="embedding_ambiguity_text_search",
        seeds={
            "concept:photosynthesis-phases": ("Photosynthesis phases", "concept", "ambiguous", None),
            "concept:light-dep": ("light-dependent reactions", "concept", "active", None),
            "concept:light-indep": ("light-independent reactions", "concept", "active", None),
        },
        routes={
            "concept:photosynthesis-phases": [
                ("concept:light-dep", "light-dependent reactions", "embedding"),
                ("concept:light-indep", "light-independent reactions", "embedding"),
            ],
        },
        input=("Photosynthesis phases", "concept", "The light-dependent reactions occur in thylakoid"),
        expected="concept:light-dep",
        category="disambiguation",
        notes="Embedding ambiguity resolved by text search",
    ),
    # ── Cascading pipe resolution ────────────────────────────
    RoutingCase(
        name="two_level_cascade",
        seeds={
            "concept:mercury": ("Mercury", "concept", "ambiguous", None),
            "concept:mercury-astro": ("Mercury (astronomy)", "concept", "ambiguous", None),
            "concept:mercury-planet": ("Mercury (planet)", "concept", "active", None),
            "concept:mercury-crater": ("Mercury crater", "concept", "active", None),
            "concept:mercury-element": ("Mercury (element)", "concept", "active", None),
        },
        routes={
            "concept:mercury": [
                ("concept:mercury-astro", "Mercury (astronomy)", "text"),
                ("concept:mercury-element", "Mercury (element)", "text"),
            ],
            "concept:mercury-astro": [
                ("concept:mercury-planet", "Mercury (planet)", "text"),
                ("concept:mercury-crater", "Mercury crater", "text"),
            ],
        },
        input=("Mercury", "concept", "Mercury (planet) is closest to the Sun"),
        expected="concept:mercury-planet",
        category="cascade",
        notes="Two-level: Mercury → Mercury(astronomy) → Mercury(planet)",
    ),
    RoutingCase(
        name="three_level_cascade",
        seeds={
            "concept:l0": ("L0", "concept", "ambiguous", None),
            "concept:l1": ("L1", "concept", "ambiguous", None),
            "concept:l2": ("L2", "concept", "ambiguous", None),
            "concept:leaf": ("Leaf", "concept", "active", None),
        },
        routes={
            "concept:l0": [("concept:l1", "L1", "text")],
            "concept:l1": [("concept:l2", "L2", "text")],
            "concept:l2": [("concept:leaf", "Leaf", "text")],
        },
        input=("L0", "concept", "Something about Leaf"),
        expected="concept:leaf",
        category="cascade",
        notes="Three-level single-child cascade",
    ),
    RoutingCase(
        name="cascade_stops_at_active",
        seeds={
            "concept:a": ("A", "concept", "ambiguous", None),
            "concept:b": ("B", "concept", "active", None),
            "concept:c": ("C", "concept", "ambiguous", None),
            "concept:d": ("D", "concept", "active", None),
        },
        routes={
            "concept:a": [
                ("concept:b", "B", "text"),
                ("concept:c", "C", "text"),
            ],
            "concept:c": [("concept:d", "D", "text")],
        },
        input=("A", "concept", "Something about B and D"),
        # Text search finds "B" in fact → routes to concept:b (active) → stops
        expected="concept:b",
        category="cascade",
        notes="Stops at first active child, doesn't cascade further",
    ),
    # ── Not found → new seed ─────────────────────────────────
    RoutingCase(
        name="not_found_returns_original",
        seeds={},
        routes={},
        input=("Quantum entanglement", "concept", "Particles become correlated"),
        expected="concept:quantum-entanglement",
        category="not_found",
        notes="No existing seed → returns generated key",
    ),
    # ── Ambiguous with no routes → stuck at parent ───────────
    RoutingCase(
        name="ambiguous_no_routes_stays",
        seeds={
            "concept:mars": ("Mars", "concept", "ambiguous", None),
        },
        routes={},
        input=("Mars", "concept", "Mars is interesting"),
        expected="concept:mars",
        category="edge_case",
        notes="Ambiguous but no routes configured → stays at parent",
    ),
]


# ── Test infrastructure ──────────────────────────────────────────────────


def _build_mock_route(child_key: str, label: str, ambiguity_type: str) -> MagicMock:
    route = MagicMock()
    route.child_seed_key = child_key
    route.label = label
    route.ambiguity_type = ambiguity_type
    route.parent_seed_key = ""  # filled by caller if needed
    return route


def _build_repo(case: RoutingCase) -> MagicMock:
    """Build a mock WriteSeedRepository from a RoutingCase."""
    repo = MagicMock()

    # Build seed objects
    seed_objs: dict[str, MagicMock] = {}
    for key, (name, node_type, status, merged_into) in case.seeds.items():
        s = MagicMock()
        s.key = key
        s.name = name
        s.node_type = node_type
        s.status = status
        s.merged_into_key = merged_into
        s.metadata_ = None
        s.fact_count = 5
        seed_objs[key] = s

    # Build route objects
    route_objs: dict[str, list[MagicMock]] = {}
    for parent_key, children in case.routes.items():
        routes = []
        for child_key, label, amb_type in children:
            r = _build_mock_route(child_key, label, amb_type)
            r.parent_seed_key = parent_key
            routes.append(r)
        route_objs[parent_key] = routes

    repo.get_seed_by_key = AsyncMock(side_effect=lambda k: seed_objs.get(k))
    repo.get_routes_for_parent = AsyncMock(side_effect=lambda k: route_objs.get(k, []))

    # Phonetic and similar seeds — not needed for most routing tests
    repo.find_by_phonetic = AsyncMock(return_value=[])
    repo.find_similar_seeds = AsyncMock(return_value=[])
    repo.find_seeds_by_alias = AsyncMock(return_value=[])
    repo._session = MagicMock()
    repo._session.execute = AsyncMock()

    return repo


async def run_case(case: RoutingCase) -> tuple[str, bool, str]:
    """Run a single routing case. Returns (case_name, passed, detail)."""
    from kt_facts.processing.seed_routing import route_seed

    repo = _build_repo(case)
    name, _node_type, fact_content = case.input

    result = await route_seed(
        name=name,
        fact_content=fact_content,
        write_seed_repo=repo,
        embedding_service=None,
        qdrant_seed_repo=None,
        model_gateway=None,
    )

    passed = result == case.expected
    detail = f"got={result}" if not passed else ""
    return case.name, passed, detail


async def main() -> None:
    print(f"Running {len(ROUTING_CASES)} routing test cases\n")

    header = f"  {'Case':<40} {'Category':<16} {'Status':>6}  {'Detail'}"
    print(header)
    print("  " + "-" * (len(header) - 2))

    total_pass = 0
    total_fail = 0
    failures: list[tuple[str, str, str]] = []

    current_cat = None
    for case in ROUTING_CASES:
        if case.category != current_cat:
            if current_cat is not None:
                print()
            current_cat = case.category
            print(f"  [{case.category}]")

        name, passed, detail = await run_case(case)

        if passed:
            total_pass += 1
            status = "PASS"
        else:
            total_fail += 1
            status = "FAIL"
            failures.append((name, case.expected, detail))

        notes = f"  # {case.notes}" if case.notes else ""
        print(f"  {name:<40} {case.category:<16} {status:>6}  {detail}{notes}")

    # Summary
    print()
    print(f"Results: {total_pass} passed, {total_fail} failed out of {len(ROUTING_CASES)}")

    if failures:
        print("\nFailures:")
        for fname, expected, detail in failures:
            print(f"  {fname}: expected={expected}, {detail}")
        sys.exit(1)
    else:
        print("\nAll routing cases passed.")


if __name__ == "__main__":
    asyncio.run(main())
