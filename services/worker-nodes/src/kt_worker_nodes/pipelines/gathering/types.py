"""Gathering-specific data types."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class GatherResult:
    """Result of gathering facts from external sources."""

    queries_executed: int = 0
    facts_gathered: int = 0
    explore_used: int = 0
    explore_remaining: int = 0
    source_titles_by_query: dict[str, list[str]] = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        """Convert to a dict suitable for tool return values."""
        d: dict[str, object] = {
            "queries_executed": self.queries_executed,
            "facts_gathered": self.facts_gathered,
            "explore_used": self.explore_used,
            "explore_remaining": self.explore_remaining,
        }
        if self.source_titles_by_query:
            d["source_titles_by_query"] = self.source_titles_by_query
        return d
