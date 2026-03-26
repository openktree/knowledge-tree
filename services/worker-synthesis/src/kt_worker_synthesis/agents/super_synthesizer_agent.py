"""SuperSynthesizerAgent — reads sub-synthesis documents and produces a meta-synthesis.

Extends BaseAgent with tools to read synthesis documents, access graph structure,
and produce a combined super-synthesis document.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.tools import BaseTool, tool

from kt_agents_core.base import BaseAgent
from kt_agents_core.state import AgentContext
from kt_worker_synthesis.agents.super_synthesizer_state import SuperSynthesizerState

logger = logging.getLogger(__name__)


def _build_super_tools(ctx: AgentContext, state_ref: list[Any]) -> list[BaseTool]:
    """Build tools for the super-synthesizer agent."""

    @tool
    async def read_synthesis(synthesis_node_id: str) -> str:
        """Read a sub-synthesis document. Returns the full text of the synthesis."""
        try:
            nid = uuid.UUID(synthesis_node_id)
        except (ValueError, AttributeError):
            return f"Invalid node_id: '{synthesis_node_id}'"
        node = await ctx.graph_engine.get_node(nid)
        if node is None:
            return f"Synthesis node not found: {synthesis_node_id}"
        if not node.definition:
            return f"Synthesis {synthesis_node_id} ({node.concept}) has no content."
        return f"# {node.concept}\n\n{node.definition}"

    @tool
    async def get_synthesis_nodes(synthesis_node_id: str) -> str:
        """Get all nodes referenced in a sub-synthesis document."""
        try:
            nid = uuid.UUID(synthesis_node_id)
        except (ValueError, AttributeError):
            return f"Invalid node_id: '{synthesis_node_id}'"

        node = await ctx.graph_engine.get_node(nid)
        if not node:
            return f"Synthesis node not found: {synthesis_node_id}"
        meta = node.metadata_ or {}
        doc = meta.get("synthesis_document", {})
        ref_nodes = doc.get("referenced_nodes", [])
        if not ref_nodes:
            return f"No referenced nodes found for synthesis {synthesis_node_id}"
        lines = [f"Referenced nodes ({len(ref_nodes)}):"]
        for rn in ref_nodes:
            lines.append(f"- {rn.get('node_id', '?')} — {rn.get('concept', '?')}")
        return "\n".join(lines)

    @tool
    async def search_graph(query: str, limit: int = 20) -> str:
        """Search for nodes in the knowledge graph. Use for additional exploration during combination."""
        nodes = await ctx.graph_engine.search_nodes(query, limit=limit)
        if not nodes:
            return f"No nodes found for: {query}"
        lines = [f"Found {len(nodes)} nodes:"]
        for n in nodes:
            lines.append(f"- {n.id} — {n.concept} [{n.node_type}]")
        return "\n".join(lines)

    @tool
    async def get_node(node_id: str) -> str:
        """Get node details including definition and edges."""
        try:
            nid = uuid.UUID(node_id)
        except (ValueError, AttributeError):
            return f"Invalid node_id: '{node_id}'"
        node = await ctx.graph_engine.get_node(nid)
        if node is None:
            return f"Node not found: {node_id}"
        lines = [f"# {node.concept} [{node.node_type}]"]
        if node.definition:
            lines.append(f"\n{node.definition[:2000]}")
        edges = await ctx.graph_engine.get_edges(nid, direction="both")
        if edges:
            lines.append(f"\nEdges ({len(edges)}):")
            for edge in edges[:20]:
                target_id = edge.target_node_id if edge.source_node_id == nid else edge.source_node_id
                target = await ctx.graph_engine.get_node(target_id)
                concept = target.concept if target else "unknown"
                lines.append(f"- {concept} ({edge.relationship_type}, weight={edge.weight})")
        return "\n".join(lines)

    @tool
    async def finish_super_synthesis(text: str) -> str:
        """Submit the final super-synthesis document. The text argument MUST contain the COMPLETE markdown text."""
        state = state_ref[0]
        if state is not None:
            state.super_synthesis_text = text
            state.phase = "done"
        return "Super-synthesis document submitted."

    return [read_synthesis, get_synthesis_nodes, search_graph, get_node, finish_super_synthesis]


class SuperSynthesizerAgent(BaseAgent[SuperSynthesizerState]):
    """Combines multiple sub-synthesis documents into a meta-synthesis."""

    terminal_phase = "done"
    emit_tool_label = "super_synthesizer"
    max_trim_tokens = 200_000
    route_nudges_to_agent = True

    def get_model_id(self) -> str:
        return self.ctx.model_gateway.synthesis_model

    def get_reasoning_effort(self) -> str | None:
        return self.ctx.model_gateway.synthesis_thinking_level or None

    def get_model_kwargs(self) -> dict[str, Any]:
        return {"max_tokens": 32000}

    def get_state_type(self) -> type[SuperSynthesizerState]:
        return SuperSynthesizerState

    def create_tools(self) -> list[BaseTool]:
        return _build_super_tools(self.ctx, self._state_ref)

    def propagate_state(self, state: SuperSynthesizerState) -> dict[str, Any]:
        return {
            "super_synthesis_text": state.super_synthesis_text,
            "phase": state.phase,
        }

    def post_llm_hook(self, state: SuperSynthesizerState, response: AIMessage) -> dict[str, Any] | None:
        if not response.tool_calls and not state.super_synthesis_text and state.phase != "done":
            return {
                "messages": [
                    response,
                    HumanMessage(content="You must call finish_super_synthesis(text) with your complete document."),
                ]
            }
        return None
