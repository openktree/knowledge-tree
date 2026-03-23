"""Bottom-up scope plan — output of the bottom-up scope pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


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
