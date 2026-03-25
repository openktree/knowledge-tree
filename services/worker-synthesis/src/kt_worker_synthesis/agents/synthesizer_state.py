"""State for the Synthesizer Agent."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Annotated

from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages
from pydantic import BaseModel, Field


class SynthesizerState(BaseModel):
    """LangGraph state for the document synthesizer agent."""

    topic: str = ""
    starting_node_ids: list[str] = Field(default_factory=list)
    exploration_budget: int = 20
    nodes_visited: list[str] = Field(default_factory=list)
    nodes_visited_count: int = 0
    facts_retrieved: dict[str, list[str]] = Field(default_factory=dict)
    synthesis_text: str = ""
    phase: str = "exploring"  # exploring | done
    messages: Annotated[Sequence[BaseMessage], add_messages] = Field(default_factory=list)

    model_config = {"arbitrary_types_allowed": True}
