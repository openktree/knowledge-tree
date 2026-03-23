"""Query Agent state — minimal state for read-only graph navigation."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Annotated

from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages
from pydantic import BaseModel, Field


class QueryAgentState(BaseModel):
    """State for the Query Agent — read-only graph navigation companion.

    No explore_budget, no created_nodes/edges, no phase, no answer field.
    The LLM's message IS the answer.
    """

    query: str
    nav_budget: int
    nav_used: int = 0

    visited_nodes: list[str] = Field(default_factory=list)
    hidden_nodes: list[str] = Field(default_factory=list)

    # Follow-up context
    original_query: str = ""
    prior_answer: str = ""
    prior_visited_nodes: list[str] = Field(default_factory=list)

    messages: Annotated[Sequence[BaseMessage], add_messages] = Field(default_factory=list)

    model_config = {"arbitrary_types_allowed": True}

    @property
    def nav_remaining(self) -> int:
        """How many nav budget units remain."""
        return max(0, self.nav_budget - self.nav_used)

    def has_visited(self, node_id: str) -> bool:
        """Check if a node has been visited in this turn or prior turns."""
        return node_id in self.visited_nodes or node_id in self.prior_visited_nodes

    @property
    def all_visited_nodes(self) -> list[str]:
        """Union of nodes visited in this turn and prior turns."""
        return list(set(self.visited_nodes) | set(self.prior_visited_nodes))
