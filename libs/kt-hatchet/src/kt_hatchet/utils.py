"""Shared utilities for Hatchet workflows -- no service dependencies."""

from __future__ import annotations

import logging
import uuid

logger = logging.getLogger(__name__)


def resolve_perspective_source_ids(
    perspective_plans: list[dict],
    built_nodes: list[dict],
) -> list[dict]:
    """Replace concept-name source_concept_ids with real node UUIDs.

    The planner produces ``source_concept_id`` values that are either:
    - A real UUID (for existing nodes found during scouting) -> kept as-is
    - A concept name (for nodes being built) -> resolved from built_nodes

    Perspectives whose source cannot be resolved are dropped with a warning.
    """
    name_to_id: dict[str, str] = {}
    for n in built_nodes:
        nid = n.get("node_id")
        concept = (n.get("concept") or "").strip()
        if nid and concept:
            name_to_id[concept.lower()] = nid

    resolved: list[dict] = []
    for plan in perspective_plans:
        source = (plan.get("source_concept_id") or "").strip()
        if not source:
            continue

        # Already a valid UUID -- keep as-is
        try:
            uuid.UUID(source)
            resolved.append(plan)
            continue
        except (ValueError, AttributeError):
            pass

        # Exact name match
        nid = name_to_id.get(source.lower())

        # Partial match fallback
        if not nid:
            for name, candidate in name_to_id.items():
                if source.lower() in name or name in source.lower():
                    nid = candidate
                    break

        if nid:
            resolved.append({**plan, "source_concept_id": nid})
        else:
            logger.debug(
                "resolve_perspective_source_ids: cannot resolve %r -- dropping",
                source,
            )

    return resolved
