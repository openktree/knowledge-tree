"""Bottom-up state types — scope plan + wave pipeline types.

Wave pipeline types (ScopePlan, ScopeBriefing, WaveAccumulator) were moved here
from the deprecated orchestrator_state module so bottom_up workflows can still
reference them without depending on the deleted agents/ directory.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# ── Wave pipeline types (moved from agents/orchestrator_state.py) ─────


@dataclass
class ScopePlan:
    """Output of wave planner: one scope to execute."""

    scope: str
    explore_budget: int
    nav_budget: int
    search_hints: list[str] = field(default_factory=list)


@dataclass
class ScopeBriefing:
    """Result of executing one scope."""

    scope: str
    wave: int
    summary: str
    visited_nodes: list[str] = field(default_factory=list)
    created_nodes: list[str] = field(default_factory=list)
    created_edges: list[str] = field(default_factory=list)
    nav_used: int = 0
    explore_used: int = 0
    gathered_fact_count: int = 0
    super_sources: list[dict] = field(default_factory=list)  # type: ignore[type-arg]


@dataclass
class WaveAccumulator:
    """Accumulates results across waves."""

    query: str
    nav_budget: int
    explore_budget: int
    nav_used: int = 0
    explore_used: int = 0
    visited_nodes: list[str] = field(default_factory=list)
    created_nodes: list[str] = field(default_factory=list)
    created_edges: list[str] = field(default_factory=list)
    gathered_fact_count: int = 0
    briefings: list[ScopeBriefing] = field(default_factory=list)
    super_sources: list[dict] = field(default_factory=list)  # type: ignore[type-arg]

    def merge(self, briefing: ScopeBriefing) -> None:
        """Merge a scope briefing into the accumulator."""
        self.briefings.append(briefing)
        self.nav_used += briefing.nav_used
        self.explore_used += briefing.explore_used
        self.gathered_fact_count += briefing.gathered_fact_count
        if briefing.super_sources:
            self.super_sources.extend(briefing.super_sources)
        for nid in briefing.visited_nodes:
            if nid not in self.visited_nodes:
                self.visited_nodes.append(nid)
        for nid in briefing.created_nodes:
            if nid not in self.created_nodes:
                self.created_nodes.append(nid)
        for eid in briefing.created_edges:
            if eid not in self.created_edges:
                self.created_edges.append(eid)

    @property
    def explore_remaining(self) -> int:
        return max(0, self.explore_budget - self.explore_used)

    @property
    def nav_remaining(self) -> int:
        return max(0, self.nav_budget - self.nav_used)


def wave_budget_ratios(n: int) -> list[float]:
    """Return budget fraction for each wave."""
    if n <= 0:
        return [1.0]
    if n == 1:
        return [1.0]
    if n == 2:
        return [0.6, 0.4]
    if n == 3:
        return [0.4, 0.35, 0.25]
    # General: first wave 40%, rest split equally
    return [0.4] + [(0.6 / (n - 1))] * (n - 1)


# ── Bottom-up scope plan ─────────────────────────────────────────


@dataclass
class BottomUpScopePlan:
    """Complete build plan produced by the bottom-up scope pipeline."""

    node_plans: list[dict[str, Any]] = field(default_factory=list)
    """[{name: str, node_type: str}] — nodes to build."""

    perspective_plans: list[dict[str, Any]] = field(default_factory=list)
    """[{claim: str, antithesis: str, source_concept_id: str}]"""

    explore_used: int = 0
    gathered_fact_count: int = 0
    extracted_count: int = 0
    content_summary: str = ""
    source_urls: list[dict[str, str]] = field(default_factory=list)
    """[{url: str, title: str}] — deduplicated source URLs from fact gathering."""

    super_sources: list[dict[str, Any]] = field(default_factory=list)
    """Super sources detected during gathering (deferred to user ingestion)."""

    inserted_fact_ids: list[str] = field(default_factory=list)
    """UUIDs (as str) of facts inserted during this scope's gather phase.

    Forwarded to ``dedup_pending_facts_wf`` by ``bottom_up_scope_wf`` so
    that the dedup workflow can collapse any duplicates before the node
    pipeline (autograph) consumes them.
    """
