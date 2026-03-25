"""State for the SuperSynthesizer Agent."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Annotated

from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages
from pydantic import BaseModel, Field


class SuperSynthesizerState(BaseModel):
    """LangGraph state for the super-synthesizer combining agent."""

    synthesis_node_ids: list[str] = Field(default_factory=list)
    super_synthesis_text: str = ""
    phase: str = "combining"  # combining | done
    messages: Annotated[Sequence[BaseMessage], add_messages] = Field(default_factory=list)

    model_config = {"arbitrary_types_allowed": True}
