"""SynthesizerAgent — navigates the knowledge graph and produces synthesis documents.

Extends BaseAgent with 8 navigation tools + finish_synthesis. Uses an exploration
budget to control depth of investigation.
"""

from __future__ import annotations

import logging
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.tools import BaseTool

from kt_agents_core.base import BaseAgent
from kt_worker_synthesis.agents.synthesizer_state import SynthesizerState
from kt_worker_synthesis.agents.tools.navigation import build_navigation_tools
from kt_worker_synthesis.agents.tools.synthesis import build_synthesis_tools

logger = logging.getLogger(__name__)


class SynthesizerAgent(BaseAgent[SynthesizerState]):
    """Graph-navigating synthesis agent that produces research documents."""

    terminal_phase = "done"
    emit_tool_label = "synthesizer"
    max_trim_tokens = 200_000
    # Nudges go to END (not back to agent) to prevent infinite loops.
    # The old query agent used this pattern successfully.
    route_nudges_to_agent = False

    def get_model_id(self) -> str:
        return self._model_id_override or self.ctx.model_gateway.synthesis_model

    def get_reasoning_effort(self) -> str | None:
        return self.ctx.model_gateway.synthesis_thinking_level or None

    def get_model_kwargs(self) -> dict[str, Any]:
        return {"max_tokens": 32000}

    def get_state_type(self) -> type[SynthesizerState]:
        return SynthesizerState

    def create_tools(self) -> list[BaseTool]:
        nav_tools = build_navigation_tools(self.ctx, self._state_ref)
        synthesis_tools = build_synthesis_tools(self._state_ref)
        return nav_tools + synthesis_tools

    def check_budget_exhaustion(self, state: SynthesizerState) -> dict[str, Any] | None:
        """Nudge the agent once when budget is exhausted."""
        remaining = state.exploration_budget - state.nodes_visited_count
        logger.info(
            "[synthesizer] budget: %d/%d visited (%d remaining), messages: %d",
            state.nodes_visited_count,
            state.exploration_budget,
            remaining,
            len(state.messages),
        )
        if state.nodes_visited_count >= state.exploration_budget:
            if state.synthesis_text:
                return {"phase": "done"}
            # Send nudge only once — scan history to avoid loop
            # (pattern from old query agent that worked correctly)
            already_nudged = any(
                isinstance(m, HumanMessage) and isinstance(m.content, str) and "BUDGET EXHAUSTED" in m.content
                for m in state.messages
            )
            if not already_nudged:
                return {
                    "messages": [
                        HumanMessage(
                            content=(
                                "BUDGET EXHAUSTED. Call finish_synthesis(text) with "
                                "your complete synthesis document now."
                            )
                        )
                    ]
                }
        return None

    def propagate_state(self, state: SynthesizerState) -> dict[str, Any]:
        return {
            "nodes_visited": state.nodes_visited,
            "nodes_visited_count": state.nodes_visited_count,
            "facts_retrieved": state.facts_retrieved,
            "synthesis_text": state.synthesis_text,
            "phase": state.phase,
        }

    def post_llm_hook(self, state: SynthesizerState, response: AIMessage) -> dict[str, Any] | None:
        """If the agent responds without tool calls and hasn't submitted, remind it."""
        if not response.tool_calls and not state.synthesis_text and state.phase != "done":
            # With route_nudges_to_agent=False, this nudge goes to END.
            # The LLM will see it on the next invocation if the graph
            # routes back to agent via another path.
            return {
                "messages": [
                    response,
                    HumanMessage(content="Call finish_synthesis(text) with your complete markdown document."),
                ]
            }
        return None
