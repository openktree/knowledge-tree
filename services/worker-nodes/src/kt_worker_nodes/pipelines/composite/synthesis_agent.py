"""Composite Synthesis Agent — LangGraph sub-agent that synthesizes multiple
source nodes into a single composite node definition.

Follows the same StateGraph pattern used in
``kt_worker_orchestrator.agents.tools.synthesize_answer``.
"""

from __future__ import annotations

import logging
import re
from typing import Annotated, Any, Sequence

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.messages.utils import trim_messages
from langgraph.graph import END, StateGraph
from langgraph.graph.message import add_messages
from pydantic import BaseModel, Field

from kt_agents_core.state import AgentContext
from kt_worker_nodes.pipelines.composite.prompts import SYNTHESIS_SYSTEM_PROMPT
from kt_worker_nodes.pipelines.composite.tools import CompositeAgentState, build_composite_tools

logger = logging.getLogger(__name__)


# ── Agent state ──────────────────────────────────────────────────────


class SynthesisAgentState(BaseModel):
    """LangGraph state for the composite synthesis agent."""

    source_node_ids: list[str] = Field(default_factory=list)
    concept: str = ""
    definition: str = ""
    phase: str = "working"  # "working" | "done"
    facts_referenced: list[str] = Field(default_factory=list)
    messages: Annotated[Sequence[BaseMessage], add_messages] = Field(default_factory=list)

    model_config = {"arbitrary_types_allowed": True}


# ── Helpers ──────────────────────────────────────────────────────────


def _extract_text_content(content: str | list[Any]) -> str:
    """Extract text from an AIMessage content field."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and block.get("type") == "text":
                parts.append(block.get("text", ""))
        return "\n".join(parts)
    return ""


def _approx_tokens(messages: list[BaseMessage]) -> int:
    """Approximate token count for message trimming (chars / 4)."""
    total = 0
    for m in messages:
        if isinstance(m.content, str):
            total += len(m.content) // 4
    return total


def _extract_fact_uuids(text: str) -> list[str]:
    """Extract fact UUIDs from {fact:<uuid>|label} tags in text."""
    pattern = r"\{fact:([0-9a-f-]+)\|"
    return re.findall(pattern, text)


# ── Graph builder ────────────────────────────────────────────────────


def _build_synthesis_graph(ctx: AgentContext) -> StateGraph:
    """Build the LangGraph StateGraph for composite synthesis.

    Nodes:
    - agent: LLM decides which tools to call
    - tools: Executes tool calls

    Routing:
    - phase == "done" -> END
    - tool_calls present -> tools
    - otherwise -> END (fallback)
    """
    agent_state = CompositeAgentState()
    state_ref: list[CompositeAgentState] = [agent_state]

    tools = build_composite_tools(ctx, state_ref)
    tools_by_name = {t.name: t for t in tools}

    chat_model = ctx.model_gateway.get_chat_model(
        model_id=ctx.model_gateway.synthesis_model,
        max_tokens=16000,
        reasoning_effort=ctx.model_gateway.synthesis_thinking_level or None,
    )
    llm_with_tools = chat_model.bind_tools(tools)

    async def agent_node(state: SynthesisAgentState) -> dict[str, Any]:
        """LLM decides next actions."""
        trimmed = trim_messages(
            state.messages,
            max_tokens=200_000,
            token_counter=_approx_tokens,
            strategy="last",
            include_system=True,
        )
        try:
            response = await llm_with_tools.ainvoke(trimmed)
        except Exception:
            logger.exception("Error in composite synthesis agent LLM call")
            return {
                "definition": "Synthesis failed: the LLM call encountered an error. Check logs for details.",
                "phase": "done",
            }
        return {"messages": [response]}

    async def tool_node(state: SynthesisAgentState) -> dict[str, Any]:
        """Execute tool calls from the last AIMessage."""
        # Sync mutable state
        state_ref[0] = agent_state
        ai_msg = state.messages[-1]

        if not isinstance(ai_msg, AIMessage) or not ai_msg.tool_calls:
            return {}

        tool_messages: list[ToolMessage] = []
        for tc in ai_msg.tool_calls:
            name = tc["name"]
            try:
                tool_fn = tools_by_name[name]
                result = await tool_fn.ainvoke(tc["args"])
                tool_messages.append(ToolMessage(content=str(result), tool_call_id=tc["id"], name=name))
            except Exception as exc:
                logger.exception("Error executing composite synthesis tool %s", name)
                tool_messages.append(
                    ToolMessage(
                        content=f"Error: {type(exc).__name__}: {exc}",
                        tool_call_id=tc["id"],
                        name=name,
                    )
                )

        result: dict[str, Any] = {"messages": tool_messages}
        if agent_state.phase == "done":
            result["definition"] = agent_state.definition
            result["phase"] = "done"
            result["facts_referenced"] = list(set(agent_state.facts_referenced))
        return result

    def should_continue(state: SynthesisAgentState) -> str:
        """Route after agent_node."""
        if state.phase == "done":
            return END
        last_msg = state.messages[-1] if state.messages else None
        if isinstance(last_msg, AIMessage) and last_msg.tool_calls:
            return "tools"
        return END

    def after_tools(state: SynthesisAgentState) -> str:
        """Route after tool_node."""
        if state.phase == "done":
            return END
        return "agent"

    graph = StateGraph(SynthesisAgentState)
    graph.add_node("agent", agent_node)
    graph.add_node("tools", tool_node)
    graph.set_entry_point("agent")
    graph.add_conditional_edges("agent", should_continue, {"tools": "tools", END: END})
    graph.add_conditional_edges("tools", after_tools, {"agent": "agent", END: END})

    return graph


# ── Public entry point ───────────────────────────────────────────────


async def build_synthesis_impl(
    ctx: AgentContext,
    source_node_ids: list[str],
    concept: str,
    query_context: str = "",
) -> dict[str, Any]:
    """Generate a composite node definition by synthesizing multiple source nodes.

    Uses a LangGraph sub-agent that reads source nodes via tools and produces
    a comprehensive, self-contained definition.

    Args:
        ctx: Agent context with graph engine, model gateway, etc.
        source_node_ids: UUIDs of the source nodes to synthesize from.
        concept: The concept/name for the composite node being created.
        query_context: Optional query context explaining why this synthesis
            is being performed.

    Returns:
        Dict with ``"definition"`` (str) and ``"facts_referenced"`` (list[str]).
    """
    if not source_node_ids:
        logger.warning("build_synthesis_impl called with no source_node_ids")
        return {
            "definition": "No source nodes provided for synthesis.",
            "facts_referenced": [],
        }

    await ctx.emit(
        "activity_log",
        action=f"Synthesizing composite node '{concept}' from {len(source_node_ids)} source nodes",
        tool="composite_synthesis",
    )

    # Build the node list for the system message
    node_lines: list[str] = []
    for nid in source_node_ids:
        import uuid as _uuid

        node = await ctx.graph_engine.get_node(_uuid.UUID(nid))
        if node is not None:
            node_lines.append(f"- {nid} — {node.concept} [{node.node_type}]")
        else:
            node_lines.append(f"- {nid} — (node not found)")
            logger.warning("Composite synthesis: source node %s not found", nid)

    context_section = ""
    if query_context:
        context_section = f"\n## Context\n{query_context}\n"

    task_block = (
        f"\n\n# YOUR TASK\n\n"
        f"## Composite Concept\n{concept}\n"
        f"{context_section}"
        f"\n## Source Nodes\n"
        f"Read these nodes and their facts, then synthesize a comprehensive "
        f"definition for the composite concept '{concept}'.\n\n" + "\n".join(node_lines)
    )

    system_content = SYNTHESIS_SYSTEM_PROMPT + task_block

    initial_state = SynthesisAgentState(
        source_node_ids=source_node_ids,
        concept=concept,
        messages=[
            SystemMessage(content=system_content),
            HumanMessage(
                content=(
                    "Read the source nodes and their facts, then call "
                    "finish(definition=<your complete markdown definition>). "
                    "The definition argument must contain the COMPLETE text — "
                    "anything written outside finish() is discarded."
                )
            ),
        ],
    )

    try:
        graph = _build_synthesis_graph(ctx)
        compiled = graph.compile()

        # Each node: get_node + get_node_facts (+ optional dims/edges), each = 2 steps
        recursion_limit = max(len(source_node_ids) * 6 + 10, 30)
        final = await compiled.ainvoke(initial_state, config={"recursion_limit": recursion_limit})

        if isinstance(final, dict):
            definition = final.get("definition", "")
            facts_referenced = final.get("facts_referenced", [])
        else:
            definition = final.definition
            facts_referenced = final.facts_referenced

        # Detect back-reference answers
        if definition and len(definition) < 200:
            msgs = final.get("messages", []) if isinstance(final, dict) else final.messages
            for msg in reversed(msgs):
                if isinstance(msg, AIMessage):
                    text = _extract_text_content(msg.content)
                    if len(text) > len(definition):
                        logger.info("Detected short definition from finish() — using AIMessage content instead")
                        definition = text
                        break

        # Fallback: agent ended without calling finish
        if not definition:
            logger.info("Composite synthesis agent ended without calling finish — extracting from messages")
            msgs = final.get("messages", []) if isinstance(final, dict) else final.messages
            for msg in reversed(msgs):
                if isinstance(msg, AIMessage):
                    text = _extract_text_content(msg.content)
                    if text.strip():
                        definition = text
                        break

        if not definition:
            definition = "Synthesis completed but no definition was produced."

        # Extract any additional fact references from the definition text
        text_fact_ids = _extract_fact_uuids(definition)
        all_facts = list(set(facts_referenced + text_fact_ids))

    except Exception:
        logger.exception("Error in composite synthesis agent")
        definition = "Error occurred during composite synthesis. Source nodes were available but synthesis failed."
        all_facts = []

    return {"definition": definition, "facts_referenced": all_facts}
