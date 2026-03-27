"""Shared LangGraph tool functions for composite node agents.

These tools are closures over an ``AgentContext`` and a mutable state reference,
following the same pattern used in
``kt_agents_core.synthesis``.
"""

from __future__ import annotations

import logging
import uuid
from typing import TYPE_CHECKING, Any

from langchain_core.tools import tool

from kt_config.types import COMPOUND_FACT_TYPES

if TYPE_CHECKING:
    from kt_agents_core.state import AgentContext
    from kt_db.models import Fact

logger = logging.getLogger(__name__)


# ── Helpers ──────────────────────────────────────────────────────────


def _fact_label(content: str, max_words: int = 8) -> str:
    """Extract a short label from fact content for the citation tag."""
    words = content.split()
    label = " ".join(words[:max_words])
    if len(words) > max_words:
        label += "…"
    return label.replace("{", "").replace("}", "").replace("|", "-")


def _format_fact(f: Fact, stance: str | None = None) -> str:
    """Format a single fact with type, stance, attribution, content, and ID."""
    attr_parts: list[str] = []
    for fs in getattr(f, "sources", []):
        source_parts: list[str] = []
        org = getattr(fs, "author_org", None)
        person = getattr(fs, "author_person", None)
        if org:
            source_parts.append(org)
        if person:
            source_parts.append(person)
        if source_parts:
            attr_parts.append("; ".join(source_parts))
        elif fs.attribution:
            attr_parts.append(fs.attribution)
        elif fs.raw_source and fs.raw_source.title:
            attr_parts.append(f"source: {fs.raw_source.title}")
    attr_suffix = f" ({'; '.join(attr_parts)})" if attr_parts else ""

    stance_label = f" [{stance.upper()}]" if stance else ""
    label = _fact_label(f.content)
    fact_id_tag = f" {{fact:{f.id}|{label}}}"
    if f.fact_type in COMPOUND_FACT_TYPES:
        return f"- [{f.fact_type}]{stance_label}{attr_suffix}{fact_id_tag}\n    {f.content}"
    return f"- [{f.fact_type}]{stance_label} {f.content}{attr_suffix}{fact_id_tag}"


# ── State protocol ───────────────────────────────────────────────────


class CompositeAgentState:
    """Minimal mutable state container shared between tools and the agent loop."""

    def __init__(self) -> None:
        self.definition: str = ""
        self.phase: str = "working"  # "working" | "done"
        self.facts_retrieved: dict[str, list[str]] = {}
        self.facts_referenced: list[str] = []


# ── Tool factory ─────────────────────────────────────────────────────


def build_composite_tools(
    ctx: AgentContext,
    state_ref: list[CompositeAgentState],
) -> list[Any]:
    """Build the set of LangGraph tools for composite node agents.

    Tools are closures over ``ctx`` (for graph engine access) and
    ``state_ref`` (a single-element list holding the mutable state so
    that tool_node can update it before invoking tools).

    Returns a list of ``@tool``-decorated async functions.
    """

    @tool
    async def get_node(node_id: str) -> str:
        """Get a node's concept, definition, and type. Use this to understand what a source node is about."""
        try:
            nid = uuid.UUID(node_id)
        except (ValueError, AttributeError):
            return f"Invalid node_id: '{node_id}' is not a valid UUID."

        node = await ctx.graph_engine.get_node(nid)
        if node is None:
            return f"Node not found: {node_id}"

        lines: list[str] = [
            f"# {node.concept} [{node.node_type}]",
        ]
        if node.definition:
            lines.append(f"\n## Definition\n{node.definition}")
        else:
            lines.append("\n_No definition available._")
        return "\n".join(lines)

    @tool
    async def get_node_facts(node_id: str) -> str:
        """Retrieve all facts for a node by its UUID. Returns formatted facts with attribution and stance."""
        agent_state = state_ref[0]
        try:
            nid = uuid.UUID(node_id)
        except (ValueError, AttributeError):
            return f"Invalid node_id: '{node_id}' is not a valid UUID."

        facts_with_stance = await ctx.graph_engine.get_node_facts_with_stance(nid)
        if not facts_with_stance:
            agent_state.facts_retrieved[node_id] = []
            return f"No facts found for node {node_id}."

        facts_with_sources = await ctx.graph_engine.get_node_facts_with_sources(nid)
        source_map = {f.id: f for f in facts_with_sources}

        formatted: list[str] = []
        for fact, stance in facts_with_stance:
            rich_fact = source_map.get(fact.id, fact)
            formatted.append(_format_fact(rich_fact, stance=stance))
            # Track referenced fact IDs
            agent_state.facts_referenced.append(str(fact.id))

        agent_state.facts_retrieved[node_id] = formatted
        return "\n".join(formatted)

    @tool
    async def get_node_dimensions(node_id: str) -> str:
        """Get all dimensions (multi-model analyses) for a node. Use for deeper understanding of how different models interpreted the node's facts."""
        try:
            nid = uuid.UUID(node_id)
        except (ValueError, AttributeError):
            return f"Invalid node_id: '{node_id}' is not a valid UUID."

        node = await ctx.graph_engine.get_node(nid)
        if node is None:
            return f"Node not found: {node_id}"

        dimensions = await ctx.graph_engine.get_dimensions(nid)
        if not dimensions:
            return f"No dimensions for node {node_id} ({node.concept})."

        lines: list[str] = [f"# Dimensions for: {node.concept} ({len(dimensions)} dimensions)"]
        for dim in dimensions:
            definitive_tag = " [DEFINITIVE]" if dim.is_definitive else ""
            lines.append(
                f"\n## {dim.model_id}{definitive_tag} (confidence={dim.confidence:.2f}, facts={dim.fact_count})"
            )
            lines.append(dim.content)

        return "\n".join(lines)

    @tool
    async def get_node_edges(node_id: str) -> str:
        """Get all edges for a node — shows connected nodes, relationship type, weight, and justification."""
        try:
            nid = uuid.UUID(node_id)
        except (ValueError, AttributeError):
            return f"Invalid node_id: '{node_id}' is not a valid UUID."

        edges = await ctx.graph_engine.get_edges(nid, direction="both")
        if not edges:
            return f"No edges for node {node_id}."

        lines: list[str] = [f"# Edges ({len(edges)})"]
        for edge in edges:
            target_id = edge.target_node_id if edge.source_node_id == nid else edge.source_node_id
            target_node = await ctx.graph_engine.get_node(target_id)
            target_concept = target_node.concept if target_node else "unknown"
            target_type = getattr(target_node, "node_type", "concept") if target_node else "?"
            weight_str = f"{edge.weight:+.2f}" if edge.weight is not None else "n/a"
            justification = edge.justification or "no justification"
            lines.append(
                f"- **{target_concept}** [{target_type}] "
                f"({edge.relationship_type}, weight={weight_str}, "
                f"id={target_id})\n"
                f"  Justification: {justification}"
            )

        return "\n".join(lines)

    @tool
    async def search_facts(query: str) -> str:
        """Search the global fact pool by text query. Returns matching facts with content and IDs."""
        agent_state = state_ref[0]
        facts = await ctx.graph_engine.search_fact_pool_text(query, limit=20)
        if not facts:
            return f"No facts found matching query: {query}"

        formatted: list[str] = []
        for fact in facts:
            label = _fact_label(fact.content)
            formatted.append(f"- [{fact.fact_type}] {fact.content} {{fact:{fact.id}|{label}}}")
            agent_state.facts_referenced.append(str(fact.id))

        return "\n".join(formatted)

    @tool
    async def finish(definition: str) -> str:
        """Submit the final definition text for the composite node. The definition argument MUST contain the COMPLETE text. Call this when you are done."""
        agent_state = state_ref[0]
        agent_state.definition = definition
        agent_state.phase = "done"
        return "Definition submitted."

    return [get_node, get_node_facts, get_node_dimensions, get_node_edges, search_facts, finish]
