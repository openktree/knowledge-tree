"""Abstract base class for node builders."""

from __future__ import annotations

from abc import ABC, abstractmethod

from kt_agents_core.state import AgentContext


class NodeBuilder(ABC):
    """Abstract base class for node builders."""

    def __init__(self, ctx: AgentContext) -> None:
        self._ctx = ctx

    @property
    @abstractmethod
    def builder_id(self) -> str:
        """Unique identifier for this builder type."""
        ...
