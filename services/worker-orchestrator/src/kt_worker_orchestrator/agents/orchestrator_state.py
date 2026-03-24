"""Orchestrator state — re-exports PipelineState as OrchestratorState for backward compat."""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field as dc_field

from pydantic import Field

from kt_agents_core.state import PipelineState

# Backward-compatible alias — all shared budget/tracking logic lives in PipelineState.
OrchestratorState = PipelineState


class SubExplorerState(PipelineState):
    """State for a scoped sub-explorer agent.

    Extends PipelineState with sub-explorer-specific fields.
    """

    scope: str = ""
    parent_query: str = ""

    # Sub-explorer specific
    summary: str = ""
    phase: str = "exploring"  # type: ignore[assignment]
    perspectives_built: int = 0
    nudge_count: int = 0
    iteration_count: int = 0

    # Event-mode fields
    tracking_ids: list[str] = Field(default_factory=list)
    scope_barrier_key: str = ""


# ── Wave pipeline types ──────────────────────────────────────────


@dataclass
class ScopePlan:
    """Output of wave planner: one scope to execute."""

    scope: str
    explore_budget: int
    nav_budget: int
    search_hints: list[str] = dc_field(default_factory=list)


@dataclass
class ScopeBriefing:
    """Result of executing one scope."""

    scope: str
    wave: int
    summary: str
    visited_nodes: list[str] = dc_field(default_factory=list)
    created_nodes: list[str] = dc_field(default_factory=list)
    created_edges: list[str] = dc_field(default_factory=list)
    nav_used: int = 0
    explore_used: int = 0
    gathered_fact_count: int = 0
    super_sources: list[dict] = dc_field(default_factory=list)  # type: ignore[type-arg]


@dataclass
class WaveAccumulator:
    """Accumulates results across waves."""

    query: str
    nav_budget: int
    explore_budget: int
    nav_used: int = 0
    explore_used: int = 0
    visited_nodes: list[str] = dc_field(default_factory=list)
    created_nodes: list[str] = dc_field(default_factory=list)
    created_edges: list[str] = dc_field(default_factory=list)
    gathered_fact_count: int = 0
    briefings: list[ScopeBriefing] = dc_field(default_factory=list)
    super_sources: list[dict] = dc_field(default_factory=list)  # type: ignore[type-arg]

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
