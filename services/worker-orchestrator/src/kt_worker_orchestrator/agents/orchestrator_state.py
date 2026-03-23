"""Orchestrator state — state model for the perspective-structured orchestrator agent."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field as dc_field
from typing import Annotated

from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages
from pydantic import BaseModel, Field


class SubExplorerState(BaseModel):
    """State for a scoped sub-explorer agent.

    Structurally compatible with OrchestratorState for duck-typing with
    existing ``_impl`` functions (gather_facts_impl, build_node_unified, etc.).
    """

    scope: str
    parent_query: str
    query: str  # Set to scope for _impl compatibility

    nav_budget: int
    explore_budget: int
    explore_used: int = 0
    nav_used: int = 0

    # Tracking (same fields as OrchestratorState for _impl compat)
    gathered_fact_count: int = 0
    visited_nodes: list[str] = Field(default_factory=list)
    created_nodes: list[str] = Field(default_factory=list)
    created_edges: list[str] = Field(default_factory=list)
    exploration_path: list[str] = Field(default_factory=list)

    existing_concepts: list[dict] = Field(default_factory=list)  # type: ignore[type-arg]
    existing_perspectives: list[dict] = Field(default_factory=list)  # type: ignore[type-arg]

    # Sub-explorer specific
    summary: str = ""
    phase: str = "exploring"  # exploring | done
    answer: str = ""  # unused but needed for structural compat
    perspectives_built: int = 0
    nudge_count: int = 0
    iteration_count: int = 0

    # Event-mode fields (used when sub-explorer emits events instead of calling pipelines)
    tracking_ids: list[str] = Field(default_factory=list)
    scope_barrier_key: str = ""
    messages: Annotated[Sequence[BaseMessage], add_messages] = Field(default_factory=list)

    model_config = {"arbitrary_types_allowed": True}

    @property
    def explore_remaining(self) -> int:
        """How many explore budget units remain."""
        return max(0, self.explore_budget - self.explore_used)

    def has_visited(self, node_id: str) -> bool:
        """Check if a node has already been visited."""
        return node_id in self.visited_nodes


class OrchestratorState(BaseModel):
    """State that persists across the Orchestrator Agent's execution.

    Tracks budgets, graph awareness, and assembled nodes/edges.
    """

    query: str
    nav_budget: int
    explore_budget: int
    explore_used: int = 0
    nav_used: int = 0

    # Graph awareness (populated during scout phase)
    existing_concepts: list[dict] = Field(default_factory=list)  # type: ignore[type-arg]
    existing_perspectives: list[dict] = Field(default_factory=list)  # type: ignore[type-arg]

    # Tracking
    gathered_fact_count: int = 0
    visited_nodes: list[str] = Field(default_factory=list)
    created_nodes: list[str] = Field(default_factory=list)
    created_edges: list[str] = Field(default_factory=list)
    exploration_path: list[str] = Field(default_factory=list)

    # Sub-explorer briefings
    sub_explorer_summaries: list[dict] = Field(default_factory=list)  # type: ignore[type-arg]

    phase: str = "planning"  # planning | gathering | assembling | synthesizing
    answer: str = ""
    messages: Annotated[Sequence[BaseMessage], add_messages] = Field(default_factory=list)

    model_config = {"arbitrary_types_allowed": True}

    @property
    def explore_remaining(self) -> int:
        """How many explore budget units remain."""
        return max(0, self.explore_budget - self.explore_used)

    def has_visited(self, node_id: str) -> bool:
        """Check if a node has already been visited."""
        return node_id in self.visited_nodes


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
