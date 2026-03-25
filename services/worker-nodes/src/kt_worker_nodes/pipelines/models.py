"""Shared data types for the node creation pipeline.

Cross-cutting result types live here. Domain-specific types are colocated
with their sub-pipelines and re-exported for backwards compatibility.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from kt_db.models import Fact

# Re-exports from domain-specific type modules (backwards compat)
from kt_worker_nodes.pipelines.edges.types import (
    EdgeCandidate as EdgeCandidate,
)
from kt_worker_nodes.pipelines.edges.types import (
    EdgeResolution as EdgeResolution,
)
from kt_worker_nodes.pipelines.edges.types import (
    resolve_fact_tokens as resolve_fact_tokens,
)


@dataclass
class CreateNodeTask:
    """Tracks a single node through the 4-phase batch pipeline."""

    name: str
    node_type: str  # concept/entity/event/perspective
    seed_key: str = ""  # REQUIRED — seed to promote
    entity_subtype: str | None = None  # person/organization/other (entities only)

    # Phase 1 outputs
    action: str = ""  # create | enrich | refresh | skip | read
    existing_node: Any = None
    embedding: list[float] | None = None
    pool_facts: list[Fact] = field(default_factory=list)
    explore_charged: bool = False
    seed_context: str | None = None  # Prompt hint: sub-seeds (ambiguous) or aliases (merged)

    # Phase 2 outputs
    node: Any = None

    # Phase 3 outputs
    dim_results: list[dict[str, Any]] = field(default_factory=list)

    # Phase 4 outputs
    edges_created: int = 0

    # Final result dict
    result: dict[str, Any] = field(default_factory=dict)

    @property
    def dim_mode(self) -> str:
        return "neutral" if self.node_type in ("concept", "location") else self.node_type

    @property
    def is_concept(self) -> bool:
        return self.node_type == "concept"


# ── Node build results ───────────────────────────────────────────────


@dataclass
class NodeBuildResult:
    """Result of building/enriching a single node."""

    action: str  # created | enriched | read | refreshed | skipped | error
    node_id: str | None = None
    concept: str = ""
    node_type: str = ""
    fact_count: int = 0
    new_facts_linked: int = 0
    is_stale: bool = False
    was_refreshed: bool = False
    suggested_concepts: list[str] = field(default_factory=list)
    pool_hint: str = ""
    edges_created: int = 0
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to a dict suitable for tool return values."""
        d: dict[str, Any] = {"action": self.action}
        if self.node_id is not None:
            d["node_id"] = self.node_id
        if self.concept:
            d["concept"] = self.concept
        if self.node_type:
            d["node_type"] = self.node_type
        if self.fact_count:
            d["fact_count"] = self.fact_count
        if self.new_facts_linked:
            d["new_facts_linked"] = self.new_facts_linked
        d["is_stale"] = self.is_stale
        d["was_refreshed"] = self.was_refreshed
        if self.suggested_concepts:
            d["suggested_concepts"] = self.suggested_concepts
        if self.pool_hint:
            d["pool_hint"] = self.pool_hint
        if self.edges_created:
            d["edges_created"] = self.edges_created
        if self.error is not None:
            d["error"] = self.error
        return d


@dataclass
class EnrichResult:
    """Result of enriching a node from the fact pool."""

    new_facts_linked: int
    dimensions_regenerated: bool


@dataclass
class PerspectiveResult:
    """Result of building a perspective node."""

    action: str  # created | read | skipped | error
    node_id: str | None = None
    claim: str = ""
    source_concept_id: str = ""
    node_type: str = "perspective"
    supporting_count: int = 0
    challenging_count: int = 0
    neutral_count: int = 0
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to a dict suitable for tool return values."""
        d: dict[str, Any] = {"action": self.action}
        if self.node_id is not None:
            d["node_id"] = self.node_id
        if self.claim:
            d["claim"] = self.claim
        if self.source_concept_id:
            d["source_concept_id"] = self.source_concept_id
        d["node_type"] = self.node_type
        if self.supporting_count:
            d["supporting_count"] = self.supporting_count
        if self.challenging_count:
            d["challenging_count"] = self.challenging_count
        if self.neutral_count:
            d["neutral_count"] = self.neutral_count
        if self.error is not None:
            d["error"] = self.error
        return d
