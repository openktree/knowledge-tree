"""Edge-specific data types.

Colocated with the edge sub-pipeline. Originally in ``nodes/models.py``.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from typing import Any

from kt_db.models import Fact

# ── Cross-type edge discovery mapping ────────────────────────────────

# For each source node type, which target types should be checked for
# cross-type edges.  Each (source, target) pair runs as an independent
# discover + classify + apply cycle.
CROSS_TYPE_TARGETS: dict[str, list[str]] = {
    "entity":      ["event", "concept", "location"],
    "event":       ["entity", "concept", "location"],
    "concept":     ["entity", "event", "location"],
    "location":    ["entity", "event", "concept"],
    "perspective": [],
    "synthesis":   [],
}

CROSS_TYPE_EDGE_TYPE = "cross_type"

# ── Edge candidate types ─────────────────────────────────────────────


@dataclass
class EdgeCandidate:
    """A candidate pair of nodes with shared facts as evidence."""

    source_node_id: uuid.UUID
    source_concept: str
    source_node_type: str = "concept"
    target_node_id: uuid.UUID = field(default_factory=uuid.uuid4)
    target_concept: str = ""
    target_node_type: str = "concept"
    evidence_fact_ids: list[uuid.UUID] = field(default_factory=list)
    evidence_facts: list[Fact] = field(default_factory=list)
    source_seed_key: str | None = None
    target_seed_key: str | None = None
    discovery_strategy: str | None = None

    @property
    def evidence_count(self) -> int:
        return len(self.evidence_fact_ids)

    @property
    def all_evidence_fact_ids(self) -> list[uuid.UUID]:
        return list(self.evidence_fact_ids)

    @property
    def all_evidence_facts(self) -> list[Fact]:
        return list(self.evidence_facts)


@dataclass
class EdgeResolution:
    """Result of the discovery + classification phase (no DB writes yet)."""

    candidates: list[EdgeCandidate]
    decisions: list[dict[str, Any] | None]
    node_concept: str


# ── Helpers ──────────────────────────────────────────────────────────


_FACT_TOKEN_RE = re.compile(r"\{fact:(\d+)\}")


def resolve_fact_tokens(
    justification: str,
    idx_map: dict[int, uuid.UUID],
) -> str:
    """Replace {fact:N} tokens with {fact:<uuid>} using the index mapping.

    Unresolvable tokens (index not in map) are left as-is.
    """

    def _replace(m: re.Match[str]) -> str:
        idx = int(m.group(1))
        fid = idx_map.get(idx)
        if fid is not None:
            return f"{{fact:{fid}}}"
        return m.group(0)

    return _FACT_TOKEN_RE.sub(_replace, justification)
