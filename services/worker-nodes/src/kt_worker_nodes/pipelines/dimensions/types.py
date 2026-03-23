"""Dimension-specific data types."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class DimensionResult:
    """Result of generating and storing dimensions for a node."""

    edges_created: int = 0
    relevant_fact_indices: set[int] = field(default_factory=set)
    suggested_concepts: list[str] = field(default_factory=list)
    dim_results: list[dict[str, Any]] = field(default_factory=list)
