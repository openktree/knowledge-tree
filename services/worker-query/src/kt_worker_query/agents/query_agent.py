"""Query Agent — Lightweight, read-only graph navigation companion.

Navigates the existing knowledge graph to find relevant nodes. No external
API calls, no node/edge creation. After navigation, the QueryWorker runs
the same synthesis sub-agent used by the orchestrator for full-quality answers.

Supports multi-turn conversation with prior context.
"""

from __future__ import annotations

import logging
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.tools import BaseTool

from kt_agents_core.base import BaseAgent
from kt_worker_query.agents.query_agent_state import QueryAgentState
from kt_worker_query.agents.tools.query_tools import create_query_tools

logger = logging.getLogger(__name__)

# ── System prompt ─────────────────────────────────────────────────

QUERY_AGENT_SYSTEM_PROMPT = """\
You are an autonomous knowledge graph navigation agent. When given a query, you \
MUST immediately search the graph and read relevant nodes to gather information. \
You are read-only — you do not create or modify data.

A separate synthesis step will produce the final answer from the nodes you visit, \
so your primary goal is THOROUGH NAVIGATION — visit all nodes relevant to the query.

## CRITICAL RULES
- NEVER ask the user what to explore or what they want to know.
- NEVER respond with only a greeting or budget summary.
- ALWAYS begin by calling search_graph, then read the top results.
- If the graph has no relevant data, say so clearly and concisely.

## Your Tools
- **search_graph(queries)** — Search existing graph by text + embedding. FREE.
- **read_node(node_id)** — Read a node's dimensions, edges, facts count. \
Costs 1 nav if unvisited.
- **read_nodes(node_ids)** — Batch read up to 10 nodes. 1 nav each if unvisited.
- **get_node_facts(node_id)** — Get facts linked to a node. 1 nav if unvisited.
- **get_node_facts_batch(node_ids)** — Batch get facts for up to 10 nodes.
- **get_budget()** — Check remaining nav budget. FREE.
- **hide_nodes(node_ids)** — Hide irrelevant nodes from graph view. FREE.

## Strategy
1. Start with search_graph to find relevant nodes
2. Read the most promising nodes to understand their content
3. Follow edges to discover related nodes for broader coverage
4. Inspect facts for nodes central to the query
5. Hide nodes that aren't relevant to keep the graph clean
6. When you have visited enough relevant nodes, respond with a brief summary \
of what you found and which nodes are most relevant

## Budget Awareness
- You have a nav_budget that limits how many unvisited nodes you can read
- Already-visited nodes are free to re-read
- search_graph, get_budget, and hide_nodes are always free
- Use get_budget() to check before committing to expensive operations

ALWAYS prefer batch tools (read_nodes, get_node_facts_batch) over repeated \
single calls. You can call MULTIPLE tools in a single response when their \
inputs are independent.
"""


# ── QueryAgentImpl class ─────────────────────────────────────────


class QueryAgentImpl(BaseAgent[QueryAgentState]):
    """Query agent — lightweight, read-only graph navigation.

    No phase field, no terminal_phase. Routing is purely tool-call based.
    Tools always route back to agent (no phase-based END in after_tools).
    """

    max_trim_tokens = 6_000
    terminal_phase = None  # No phase-based exit
    emit_tool_label = "query_agent"

    def create_tools(self) -> list[BaseTool]:
        return create_query_tools(self.ctx, self._get_state)

    def get_model_id(self) -> str:
        from kt_config.settings import get_settings

        settings = get_settings()
        if settings.query_agent_model:
            return settings.query_agent_model
        return self.ctx.model_gateway.chat_model

    def get_state_type(self) -> type[QueryAgentState]:
        return QueryAgentState

    def get_reasoning_effort(self) -> str | None:
        return self.ctx.model_gateway.chat_thinking_level or None

    def check_budget_exhaustion(self, state: QueryAgentState) -> dict[str, Any] | None:
        if state.nav_remaining <= 0:
            advisory_sent = any(
                isinstance(m, HumanMessage)
                and "NAV BUDGET EXHAUSTED" in (m.content if isinstance(m.content, str) else "")
                for m in state.messages
            )
            if not advisory_sent:
                return {
                    "messages": [
                        HumanMessage(
                            content="NAV BUDGET EXHAUSTED. You can still use free tools "
                            "(search_graph, get_budget, hide_nodes) or respond now with "
                            "what you've learned."
                        )
                    ]
                }
        return None

    def on_llm_error(self, state: QueryAgentState) -> dict[str, Any]:
        return {
            "messages": [
                AIMessage(
                    content="I encountered an error processing your query. "
                    "Please try again."
                )
            ]
        }

    def propagate_state(self, state: QueryAgentState) -> dict[str, Any]:
        return {
            "nav_used": state.nav_used,
            "visited_nodes": state.visited_nodes,
            "hidden_nodes": state.hidden_nodes,
        }

    def emit_budget_data(self, state: QueryAgentState) -> dict[str, Any]:
        return {
            "nav_remaining": state.nav_remaining,
            "nav_total": state.nav_budget,
            "explore_remaining": 0,
            "explore_total": 0,
        }


# Phrases that indicate the agent is asking instead of answering
_NON_ANSWER_PHRASES = [
    "what would you like to explore",
    "what topic would you like",
    "what would you like me to",
    "how can i help you",
    "what are you interested in",
    "what do you want to know",
    "navigation points remaining",
    "nav points remaining",
    "nav budget remaining",
]


def _is_non_answer(answer: str) -> bool:
    """Detect if the agent produced a non-answer (greeting, budget recital, etc.)."""
    if not answer or len(answer.strip()) < 40:
        return True
    lower = answer.lower()
    for phrase in _NON_ANSWER_PHRASES:
        if phrase in lower:
            return True
    return False
