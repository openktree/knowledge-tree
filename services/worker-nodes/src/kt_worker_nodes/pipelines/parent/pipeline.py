"""Parent selection pipeline for tree structure.

Uses an LLM to pick the immediate conceptual parent — the most specific
candidate that still contains or encompasses the child node.

For each non-entity node, examines its same-type edges, loads the node's
own dimensions as context, and asks the model to choose the tightest-fit
parent. Supports parent reversal when the chosen candidate is currently
a child of the node being processed.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from typing import Any

from kt_agents_core.state import AgentContext
from kt_config.types import DEFAULT_PARENTS
from kt_worker_nodes.pipelines.nodes.types import CreateNodeTask

logger = logging.getLogger(__name__)

_TYPE_INSTRUCTIONS: dict[str, str] = {
    "concept": (
        "A parent concept directly contains this concept as a sub-topic or "
        "component. Pick the tightest fit, not a distant ancestor.\n"
        "Examples:\n"
        "  - 'transistor' → child of 'semiconductor devices', not 'electronics'\n"
        "  - 'photosynthesis' → child of 'plant metabolism', not 'biology'\n"
        "  - 'quicksort' → child of 'sorting algorithms', not 'computer science'\n"
        "  - 'serotonin' → child of 'neurotransmitters', not 'brain chemistry'\n"
        "  - 'sonnet' → child of 'poetic forms', not 'literature'\n"
        "  - 'TCP' → child of 'transport layer protocols', not 'networking'"
    ),
    "event": (
        "A parent event is the immediate larger event this is part of. "
        "Pick the smallest containing event, not the entire war/era/movement.\n"
        "Examples:\n"
        "  - 'D-Day landings' → child of 'Battle of Normandy', not 'World War II'\n"
        "  - 'Boston Tea Party' → child of 'American colonial protests', not 'American Revolution'\n"
        "  - 'Chernobyl explosion' → child of 'Chernobyl disaster', not 'Soviet nuclear program'\n"
        "  - 'signing of the Magna Carta' → child of 'First Barons War', not 'Medieval English history'\n"
        "  - 'Apollo 11 moon landing' → child of 'Apollo 11 mission', not 'Space Race'"
    ),
    "perspective": (
        "A parent perspective is the broader stance this perspective "
        "narrows or specifies. Pick the closest generalization.\n"
        "Examples:\n"
        "  - 'sugar taxes reduce childhood obesity' → child of 'government intervention reduces obesity', not 'public health policy'\n"
        "  - 'rent control causes housing shortages' → child of 'price controls distort markets', not 'economics'\n"
        "  - 'nuclear energy is the safest power source' → child of 'nuclear energy is beneficial', not 'energy policy'\n"
        "  - 'remote work increases productivity' → child of 'flexible work improves outcomes', not 'future of work'\n"
        "  - 'UBI eliminates poverty traps' → child of 'UBI is beneficial', not 'welfare reform'"
    ),
}

_SYSTEM_PROMPT = """\
You are classifying the tree-parent relationship for a knowledge graph node.

Pick the IMMEDIATE parent from the candidates — the most specific candidate
that still conceptually contains or encompasses the node. Do NOT pick a
distant ancestor; pick the tightest fit.

{type_instruction}

The node's definition (from its dimensions) is provided. Candidates show
only their name and current parent. Use the definition to understand what
the node actually represents.

Return JSON: {{"choice": N}} where N is the 1-based candidate number,
or {{"choice": null}} if none are a valid immediate parent."""

_MAX_DIM_CHARS = 300


@dataclass
class _ParentResult:
    """Internal result from a single parent selection."""

    parent_name: str | None = None
    is_default: bool = False
    is_reversal: bool = False


class ParentSelectionPipeline:
    """Selects tree parents for newly created nodes using LLM reasoning."""

    def __init__(self, ctx: AgentContext) -> None:
        self._ctx = ctx

    async def select_parents_batch(self, tasks: list[CreateNodeTask]) -> dict[str, Any]:
        """Select parents for all newly created nodes in the batch.

        Entities don't have tree parents (parent_id stays NULL).
        All other eligible nodes are always re-evaluated.

        Returns:
            Metrics dict with parent assignment counts and per-node detail.
        """
        eligible = [
            t for t in tasks if t.action in ("create", "refresh") and t.node is not None and t.node_type != "entity"
        ]
        parents_assigned = 0
        default_parents = 0
        reversals = 0
        node_details: list[dict[str, Any]] = []

        for task in eligible:
            result = await self._select_parent(task)
            await self._ctx.emit("activity_log", action=f"Selected parent for '{task.name}'", tool="build_pipeline")
            if result.is_default:
                default_parents += 1
            elif result.parent_name is not None:
                parents_assigned += 1
            if result.is_reversal:
                reversals += 1
            if len(node_details) < 10:
                node_details.append(
                    {
                        "name": task.name,
                        "parent": result.parent_name or "default",
                    }
                )

        return {
            "node_count": len(eligible),
            "parents_assigned": parents_assigned,
            "default_parents": default_parents,
            "reversals": reversals,
            "nodes": node_details,
        }

    async def _select_parent(self, task: CreateNodeTask) -> _ParentResult:
        """Select the best parent for a single node via LLM."""
        node = task.node
        if node is None:
            return _ParentResult()

        node_type = task.node_type
        default_parent = DEFAULT_PARENTS.get(node_type)
        if default_parent is None:
            return _ParentResult()

        try:
            # 1. Get same-type neighbor candidates from edges
            candidates = await self._get_candidates(node.id, node_type)
            if not candidates:
                await self._ctx.graph_engine.set_parent(node.id, default_parent)
                return _ParentResult(is_default=True)

            # 2. Load node's dimensions as definition context
            definition = await self._build_definition(node.id)

            # 3. Resolve parent concept names for node and candidates
            parent_names = await self._resolve_parent_names(node, candidates)

            # 4. Build prompt and call LLM
            choice_idx = await self._ask_llm(
                node,
                node_type,
                definition,
                candidates,
                parent_names,
            )

            # 5. Apply choice or fallback to default
            if choice_idx is not None and 0 <= choice_idx < len(candidates):
                chosen = candidates[choice_idx]
                is_reversal = getattr(chosen, "parent_id", None) == node.id
                await self._apply_with_reversal(node, chosen, default_parent)
                return _ParentResult(
                    parent_name=chosen.concept,
                    is_reversal=is_reversal,
                )
            else:
                await self._ctx.graph_engine.set_parent(node.id, default_parent)
                return _ParentResult(is_default=True)

        except Exception:
            logger.exception("Error selecting parent for node %s", node.id)
            if default_parent:
                try:
                    await self._ctx.graph_engine.set_parent(node.id, default_parent)
                except Exception:
                    logger.debug("Failed to set default parent", exc_info=True)
            return _ParentResult(is_default=True)

    async def _get_candidates(
        self,
        node_id: uuid.UUID,
        node_type: str,
    ) -> list[Any]:
        """Return same-type neighbor nodes from positive-weight edges."""
        edges = await self._ctx.graph_engine.get_edges(node_id, direction="both")
        if not edges:
            return []

        neighbor_ids: list[uuid.UUID] = []
        for edge in edges:
            if edge.weight > 0:
                other_id = edge.target_node_id if edge.source_node_id == node_id else edge.source_node_id
                neighbor_ids.append(other_id)

        if not neighbor_ids:
            return []

        neighbors = await self._ctx.graph_engine.get_nodes_by_ids(neighbor_ids)
        return [n for n in neighbors if n.node_type == node_type]

    async def _build_definition(self, node_id: uuid.UUID) -> str:
        """Build a definition string from the node's dimensions."""
        dims = await self._ctx.graph_engine.get_dimensions(node_id)
        if not dims:
            return "(no definition available)"

        parts: list[str] = []
        for dim in dims:
            content = dim.content or ""
            if len(content) > _MAX_DIM_CHARS:
                content = content[:_MAX_DIM_CHARS] + "..."
            parts.append(content)
        return "\n  ".join(parts)

    async def _resolve_parent_names(
        self,
        node: Any,
        candidates: list[Any],
    ) -> dict[uuid.UUID, str]:
        """Batch-resolve parent_id → concept name for node and candidates."""
        parent_ids: set[uuid.UUID] = set()
        defaults = set(DEFAULT_PARENTS.values())

        if node.parent_id and node.parent_id not in defaults:
            parent_ids.add(node.parent_id)
        for c in candidates:
            pid = getattr(c, "parent_id", None)
            if pid and pid not in defaults:
                parent_ids.add(pid)

        if not parent_ids:
            return {}

        parent_nodes = await self._ctx.graph_engine.get_nodes_by_ids(list(parent_ids))
        return {n.id: n.concept for n in parent_nodes}

    def _format_parent(
        self,
        parent_id: uuid.UUID | None,
        parent_names: dict[uuid.UUID, str],
    ) -> str:
        """Format a parent reference as a human-readable string."""
        defaults = set(DEFAULT_PARENTS.values())
        if parent_id is None or parent_id in defaults:
            return "root"
        name = parent_names.get(parent_id)
        return f'"{name}"' if name else "root"

    async def _ask_llm(
        self,
        node: Any,
        node_type: str,
        definition: str,
        candidates: list[Any],
        parent_names: dict[uuid.UUID, str],
    ) -> int | None:
        """Call the LLM and return the 0-based candidate index, or None."""
        type_instruction = _TYPE_INSTRUCTIONS.get(node_type, "")
        system = _SYSTEM_PROMPT.format(type_instruction=type_instruction)

        node_parent_str = self._format_parent(
            getattr(node, "parent_id", None),
            parent_names,
        )
        candidate_lines: list[str] = []
        for i, c in enumerate(candidates, 1):
            c_parent = self._format_parent(
                getattr(c, "parent_id", None),
                parent_names,
            )
            candidate_lines.append(f'{i}. "{c.concept}" (current parent: {c_parent})')

        user_msg = (
            f'Node: "{node.concept}" (type: {node_type})\n'
            f"Current parent: {node_parent_str}\n"
            f"Definition:\n  {definition}\n\n"
            f"Candidates:\n" + "\n".join(candidate_lines)
        )

        gateway = self._ctx.model_gateway
        result = await gateway.generate_json(
            model_id=gateway.parent_selection_model,
            messages=[{"role": "user", "content": user_msg}],
            system_prompt=system,
            temperature=0.0,
            max_tokens=256,
            reasoning_effort=gateway.parent_selection_thinking_level or None,
        )

        choice = result.get("choice")
        if choice is None:
            return None
        if isinstance(choice, int) and 1 <= choice <= len(candidates):
            return choice - 1  # convert to 0-based
        return None

    async def _apply_with_reversal(
        self,
        node: Any,
        chosen: Any,
        default_parent: uuid.UUID,
    ) -> None:
        """Apply parent assignment with reversal if needed.

        If the chosen candidate currently has parent_id == node.id
        (i.e. chosen is a child of this node), swap the relationship:
        - chosen.parent_id ← node's current parent (promote chosen)
        - node.parent_id ← chosen.id (node becomes child of chosen)
        """
        ge = self._ctx.graph_engine

        if getattr(chosen, "parent_id", None) == node.id:
            # Reversal: chosen is currently a child of node
            old_node_parent = getattr(node, "parent_id", None) or default_parent
            await ge.set_parent(chosen.id, old_node_parent)
            await ge.set_parent(node.id, chosen.id)
            logger.info(
                "Parent reversal: %s (%s) is now child of %s (%s)",
                node.concept,
                node.id,
                chosen.concept,
                chosen.id,
            )
        else:
            await ge.set_parent(node.id, chosen.id)
