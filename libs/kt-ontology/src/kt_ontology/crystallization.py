"""Ontology crystallization — promotes high-child-count nodes to stable anchors.

When a node accumulates enough children (>= threshold via parent_id), it becomes
a "crystallized" ontological anchor with a richer, authoritative definition
generated from its dimensions + child concepts + child perspectives.

The crystallized definition is protected from normal regeneration during
enrichment cycles. Re-crystallization happens when >ratio children are
updated since last crystallization, or when the node is stale.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from kt_config.settings import get_settings
from kt_ontology.prompts.crystallization import (
    CRYSTALLIZATION_SYSTEM_PROMPT,
    build_crystallization_user_prompt,
)

if TYPE_CHECKING:
    from kt_agents_core.state import AgentContext
    from kt_db.models import Node

logger = logging.getLogger(__name__)


# ── Module-level helpers (importable from other modules) ─────────────


def _is_crystallized(node: Node) -> bool:
    """Check whether a node has been crystallized (marked ontology_stable)."""
    if node.metadata_ is None:
        return False
    return bool(node.metadata_.get("ontology_stable"))


def _needs_recrystallization(
    node: Node,
    children: list[Node],
    child_change_ratio: float | None = None,
) -> bool:
    """Check whether a crystallized node needs re-crystallization.

    Re-crystallization is needed when:
    - >ratio of children have updated_at > crystallized_at
    - The node is stale (updated_at + stale_after < now)
    """
    if not _is_crystallized(node):
        return False

    if child_change_ratio is None:
        child_change_ratio = get_settings().crystallization_child_change_ratio

    metadata = node.metadata_ or {}
    crystallized_at_str = metadata.get("crystallized_at")
    if not crystallized_at_str:
        return True  # Corrupted state — re-crystallize

    try:
        crystallized_at = datetime.fromisoformat(crystallized_at_str)
    except (ValueError, TypeError):
        return True  # Bad timestamp — re-crystallize

    # Make timezone-aware if needed
    if crystallized_at.tzinfo is None:
        crystallized_at = crystallized_at.replace(tzinfo=timezone.utc)

    if not children:
        return False

    # Count children updated after crystallization
    changed = sum(1 for child in children if child.updated_at.replace(tzinfo=timezone.utc) > crystallized_at)
    ratio = changed / len(children)

    return ratio > child_change_ratio


# ── CrystallizationPipeline ─────────────────────────────────────────


class CrystallizationPipeline:
    """Checks and performs crystallization for parent nodes.

    Constructor follows the same pattern as DefinitionPipeline.
    """

    def __init__(self, ctx: AgentContext) -> None:
        self._ctx = ctx

    async def check_and_crystallize(self, parent_id: Any) -> bool:
        """Check if a parent node should be crystallized and do it if so.

        Returns True if crystallization was performed, False otherwise.
        """
        import uuid as _uuid

        ctx = self._ctx
        settings = get_settings()

        pid = _uuid.UUID(str(parent_id)) if not isinstance(parent_id, _uuid.UUID) else parent_id

        node = await ctx.graph_engine.get_node(pid)
        if node is None:
            logger.warning("crystallize: parent node %s not found", parent_id)
            return False

        all_children = await ctx.graph_engine.get_children(pid)
        # Only count children that have a definition — empty stub nodes
        # should not contribute to the crystallization threshold.
        children = [c for c in all_children if c.definition]
        child_count = len(children)

        # Check threshold
        if child_count < settings.crystallization_child_threshold:
            logger.debug(
                "crystallize: node %s has %d children with definitions (%d total, threshold %d) — skipping",
                node.concept,
                child_count,
                len(all_children),
                settings.crystallization_child_threshold,
            )
            return False

        # Already crystallized — check if re-crystallization is needed
        if _is_crystallized(node):
            if not _needs_recrystallization(node, children):
                logger.debug(
                    "crystallize: node %s is already crystallized and up-to-date",
                    node.concept,
                )
                return False
            logger.info(
                "crystallize: re-crystallizing node %s (%d children changed)",
                node.concept,
                child_count,
            )

        # Perform crystallization
        definition = await self._generate_crystallized_definition(node, children)
        if not definition:
            logger.warning("crystallize: LLM returned empty definition for %s", node.concept)
            return False

        # Write definition and metadata
        await ctx.graph_engine.set_node_definition(pid, definition, source="crystallized")

        metadata = dict(node.metadata_ or {})
        metadata["ontology_stable"] = True
        metadata["crystallized_at"] = datetime.now(timezone.utc).isoformat()
        metadata["crystallized_child_count"] = child_count
        await ctx.graph_engine.update_node(pid, metadata_=metadata)

        logger.info(
            "crystallize: crystallized node %s with %d children (%d chars)",
            node.concept,
            child_count,
            len(definition),
        )
        return True

    async def _generate_crystallized_definition(
        self,
        parent: Node,
        children: list[Node],
    ) -> str | None:
        """Generate a crystallized definition via LLM.

        Gathers parent dimensions + child names/definitions + child perspectives,
        then makes a single LLM call.
        """
        ctx = self._ctx

        # Gather parent dimensions
        dims = await ctx.graph_engine.get_dimensions(parent.id)

        # Gather child perspectives (up to 3 per child, 30 total)
        child_perspectives: list[tuple[str, str]] = []
        for child in children[:50]:
            if len(child_perspectives) >= 30:
                break
            perspectives = await ctx.graph_engine.get_perspectives(child.id)
            for persp in perspectives[:3]:
                child_perspectives.append((child.concept, persp.concept))
                if len(child_perspectives) >= 30:
                    break

        user_prompt = build_crystallization_user_prompt(
            parent_concept=parent.concept,
            parent_definition=parent.definition,
            dimensions=dims,
            children=children,
            child_perspectives=child_perspectives if child_perspectives else None,
        )

        model_id = ctx.model_gateway.crystallization_model
        thinking_level = ctx.model_gateway.crystallization_thinking_level

        try:
            result = await ctx.model_gateway.generate(
                model_id=model_id,
                messages=[{"role": "user", "content": user_prompt}],
                system_prompt=CRYSTALLIZATION_SYSTEM_PROMPT,
                temperature=0.3,
                max_tokens=4000,
                reasoning_effort=thinking_level or None,
            )
            return result.strip() if result else None
        except Exception:
            logger.exception("crystallize: LLM call failed for %s", parent.concept)
            return None
