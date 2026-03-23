"""Ingest agent state — structurally compatible with OrchestratorState."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Annotated, Any

from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages
from pydantic import BaseModel, Field


class IngestState(BaseModel):
    """State for the Ingest Agent — node-budget model.

    The fact pool is pre-filled by decompose_all_sources() before the agent runs.
    The agent's job is to strategically build nodes from the pool, constrained
    by nav_budget (max nodes to create + existing nodes to read).
    """

    conversation_id: str
    query: str  # "Ingest: <title>" — needed for decompose() query_context

    # Source context (set before agent starts, for prompt only)
    source_summaries: list[dict[str, Any]] = Field(default_factory=list)

    # Budgets — unified node budget
    nav_budget: int = 50   # max nodes to create + read
    nav_used: int = 0      # nodes created + nodes read

    # Tracking (compatible with OrchestratorState)
    gathered_fact_count: int = 0
    existing_concepts: list[dict[str, Any]] = Field(default_factory=list)
    existing_perspectives: list[dict[str, Any]] = Field(default_factory=list)
    visited_nodes: list[str] = Field(default_factory=list)
    created_nodes: list[str] = Field(default_factory=list)
    created_edges: list[str] = Field(default_factory=list)
    exploration_path: list[str] = Field(default_factory=list)
    sub_explorer_summaries: list[dict[str, Any]] = Field(default_factory=list)

    # Ingest-specific
    disable_external_search: bool = True

    # Decomposition summary (set before agent starts)
    total_facts: int = 0
    fact_type_counts: dict[str, int] = Field(default_factory=dict)
    key_topics: list[str] = Field(default_factory=list)
    decomp_source_summaries: list[dict[str, Any]] = Field(default_factory=list)

    # Content index for browse tools (set before agent starts, not serialized)
    content_index: Any = None  # ContentIndex instance
    # Partition range if running as a parallel partition agent
    partition_index_range: tuple[int, int] | None = None

    scope: str = "ingest-building"  # pipeline tracker scope id
    phase: str = "building"  # building | done
    nudge_count: int = 0  # How many times the agent has been nudged to continue
    answer: str = ""
    messages: Annotated[Sequence[BaseMessage], add_messages] = Field(default_factory=list)

    model_config = {"arbitrary_types_allowed": True}

    @property
    def nav_remaining(self) -> int:
        """How many node budget units remain."""
        return max(0, self.nav_budget - self.nav_used)

    @property
    def explore_remaining(self) -> int:
        """Ingest has no explore budget — always 0."""
        return 0

    @property
    def explore_budget(self) -> int:
        """Ingest has no explore budget — always 0."""
        return 0

    @property
    def explore_used(self) -> int:
        """Ingest has no explore budget — always 0."""
        return 0

    def has_visited(self, node_id: str) -> bool:
        """Check if a node has already been visited."""
        return node_id in self.visited_nodes
