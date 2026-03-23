"""Node-specific data types.

Colocated with the node creation sub-pipeline. Originally in ``nodes/models.py``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from kt_db.models import Fact


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
