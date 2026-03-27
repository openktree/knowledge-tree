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
    route_nudges_to_agent = True

    def get_model_id(self) -> str:
        return self.ctx.model_gateway.synthesis_model

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
        """Nudge the agent when exploration budget is exhausted."""
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
            return {
                "messages": [
                    HumanMessage(
                        content=(
                            f"You have visited {state.nodes_visited_count}/{state.exploration_budget} nodes. "
                            "Your exploration budget is exhausted. You MUST now call finish_synthesis(text) "
                            "with your complete synthesis document. Do NOT make any more navigation calls."
                        )
                    )
                ]
            }
        remaining = state.exploration_budget - state.nodes_visited_count
        if remaining <= 3 and remaining > 0:
            return {
                "messages": [
                    HumanMessage(
                        content=(
                            f"Budget warning: only {remaining} node visits remaining. "
                            "Start wrapping up your investigation and prepare to write."
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
        """Nudge the agent to keep exploring or to finish properly."""
        remaining = state.exploration_budget - state.nodes_visited_count
        used_ratio = state.nodes_visited_count / max(state.exploration_budget, 1)

        # If trying to end without tool calls and no synthesis submitted
        if not response.tool_calls and not state.synthesis_text and state.phase != "done":
            if used_ratio < 0.5:
                # Strong nudge: way too early
                return {
                    "messages": [
                        response,
                        HumanMessage(
                            content=(
                                f"You have only visited {state.nodes_visited_count}/{state.exploration_budget} nodes "
                                f"({remaining} remaining). Keep investigating — use get_edges() to discover "
                                "neighbors of the nodes you've visited, then visit the most relevant ones."
                            )
                        ),
                    ]
                }
            # Otherwise just remind to call finish_synthesis
            return {
                "messages": [
                    response,
                    HumanMessage(
                        content="Call finish_synthesis(text) with your complete markdown document."
                    ),
                ]
            }

        return None
