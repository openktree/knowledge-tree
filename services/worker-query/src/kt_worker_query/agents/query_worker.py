"""QueryWorker — workflow initiation for the query agent."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Any, cast

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage

from kt_agents_core.results import AgentResult, build_subgraph, extract_final_state
from kt_agents_core.state import ConversationState, PipelineState
from kt_agents_core.worker_base import BaseWorker
from kt_worker_orchestrator.agents.tools.synthesize_answer import synthesize_answer_impl
from kt_worker_query.agents.query_agent import (
    QUERY_AGENT_SYSTEM_PROMPT,
    QueryAgentImpl,
)
from kt_worker_query.agents.query_agent_state import QueryAgentState

logger = logging.getLogger(__name__)


class QueryWorker(BaseWorker):
    """Encapsulates workflow initiation for the query agent."""

    async def run(
        self,
        query: str,
        nav_budget: int = 50,
        *,
        original_query: str = "",
        prior_answer: str = "",
        prior_visited_nodes: list[str] | None = None,
    ) -> AgentResult:
        """Run the query agent for graph navigation, then synthesize the answer.

        The query agent navigates the graph read-only to find relevant nodes.
        After navigation, synthesize_answer_impl produces a full-quality answer
        from the visited nodes — same synthesis quality as the orchestrator.
        """
        ctx = self.ctx
        prior_nodes = prior_visited_nodes or []

        # Build context message
        if original_query and prior_answer:
            # Follow-up turn — give navigation agent enough context
            prior_answer_truncated = prior_answer[:2000]
            if len(prior_answer) > 2000:
                prior_answer_truncated += "\n[...truncated...]"

            context_msg = (
                f'User query: "{query}"\n\n'
                f"This is a follow-up question. The original conversation was about: "
                f'"{original_query}"\n\n'
                f"Prior answer summary:\n{prior_answer_truncated}\n\n"
                f"Already visited {len(prior_nodes)} nodes in prior turns.\n\n"
                f"Nav budget: {nav_budget}\n\n"
                f"IMPORTANT: Search the graph NOW for nodes relevant to the user's "
                f"current query. Visit and read the most relevant nodes so the "
                f"synthesis step has good material to work with."
            )
        else:
            context_msg = (
                f'User query: "{query}"\n\n'
                f"Nav budget: {nav_budget}\n\n"
                f"Search the graph NOW for relevant nodes, read the best matches, "
                f"and visit as many relevant nodes as your budget allows. A synthesis "
                f"step will produce the final answer from what you find."
            )

        messages: list[BaseMessage] = [
            SystemMessage(content=QUERY_AGENT_SYSTEM_PROMPT),
            HumanMessage(content=context_msg),
        ]

        state = QueryAgentState(
            query=query,
            nav_budget=nav_budget,
            original_query=original_query,
            prior_answer=prior_answer,
            prior_visited_nodes=prior_nodes,
            messages=messages,
        )

        await self._emit_start("Starting query navigation", "query_agent", nav_budget)

        agent = QueryAgentImpl(ctx)
        graph, _tools = agent.build_graph()
        compiled = graph.compile()

        max_steps = min(nav_budget, 50) * 2 + 10
        config = {"recursion_limit": max(max_steps, 50)}

        try:
            final_state = await compiled.ainvoke(state, config=config)
        except Exception:
            logger.exception("Error in query agent loop")
            final_state = state

        # Extract navigation results
        fs = extract_final_state(
            final_state,
            state,
            ["messages", "visited_nodes", "hidden_nodes", "nav_used"],
        )
        msgs = cast(Sequence[BaseMessage], fs["messages"])
        visited = cast(list[str], fs["visited_nodes"])
        hidden = cast(list[str], fs["hidden_nodes"])
        nav_used = cast(int, fs["nav_used"])

        # If no nodes visited and budget remains, retry navigation once
        if not visited and nav_used < nav_budget:
            logger.warning("Query agent visited no nodes, retrying with push message")
            push_msg = HumanMessage(
                content=(
                    "You have not visited any nodes yet. You MUST search the graph "
                    "and read relevant nodes. If the graph has no relevant data, "
                    "say so explicitly after searching. Search NOW."
                )
            )
            retry_messages = list(msgs) + [push_msg]
            retry_state = QueryAgentState(
                query=query,
                nav_budget=nav_budget,
                original_query=original_query,
                prior_answer=prior_answer,
                prior_visited_nodes=prior_nodes,
                messages=retry_messages,
                nav_used=nav_used,
                visited_nodes=list(visited),
                hidden_nodes=list(hidden),
            )
            try:
                retry_result = await compiled.ainvoke(retry_state, config=config)
                rfs = extract_final_state(
                    retry_result,
                    retry_state,
                    ["messages", "visited_nodes", "hidden_nodes", "nav_used"],
                )
                visited = rfs["visited_nodes"]
                hidden = rfs["hidden_nodes"]
                nav_used = rfs["nav_used"]
            except Exception:
                logger.exception("Error in query agent retry loop")

        # ── Synthesis step ────────────────────────────────────────────
        # Build a temporary state and run the same synthesis sub-agent
        # used by the orchestrator, producing a full-quality answer.
        answer = ""
        all_node_ids = list(set(list(visited) + prior_nodes))

        if visited:
            try:
                if original_query and prior_answer:
                    # Follow-up turn — ConversationState carries prior context
                    synth_state: PipelineState | ConversationState = ConversationState(
                        query=query,
                        original_query=original_query,
                        prior_answer=prior_answer,
                        prior_visited_nodes=prior_nodes,
                        nav_budget=nav_budget,
                        visited_nodes=list(visited),
                        created_nodes=[],
                    )
                else:
                    # Initial query
                    synth_state = PipelineState(
                        query=query,
                        nav_budget=nav_budget,
                        explore_budget=0,
                        visited_nodes=list(visited),
                        created_nodes=[],
                    )

                result = await synthesize_answer_impl(ctx, synth_state)
                answer = synth_state.answer or str(result.get("answer", "") if isinstance(result, dict) else "")
            except Exception:
                logger.exception("Error in query synthesis — falling back to agent response")
                # Fall back to extracting from last AIMessage
                answer = self._extract_agent_answer(msgs)
        else:
            answer = "No relevant nodes found in the knowledge graph for this query."

        # Build subgraph
        subgraph = await build_subgraph(all_node_ids, ctx)

        return AgentResult(
            answer=answer,
            visited_nodes=list(visited),
            hidden_nodes=list(hidden),
            nav_used=nav_used,
            explore_used=0,
            subgraph=subgraph,
        )

    @staticmethod
    def _extract_agent_answer(msgs: Any) -> str:
        """Extract the last AIMessage text as a fallback answer."""
        try:
            for msg in reversed(list(msgs)):
                if isinstance(msg, AIMessage) and msg.content and not msg.tool_calls:
                    return msg.content if isinstance(msg.content, str) else str(msg.content)
        except (TypeError, ValueError):
            pass
        return ""
