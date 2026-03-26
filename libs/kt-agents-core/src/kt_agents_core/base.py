"""BaseAgent -- Template-method base class for LangGraph agents.

All agents share the same LangGraph architecture:
  StateGraph with "agent" -> "tools" -> routing loop.

Subclasses configure behavior via class attributes and hook methods,
while BaseAgent handles the wiring, message trimming, tool execution,
state propagation, and routing.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from collections.abc import Sequence
from typing import Any, Generic, TypeVar

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from langchain_core.messages.utils import trim_messages
from langchain_core.tools import BaseTool
from langgraph.graph import END, StateGraph  # type: ignore[import-untyped]
from pydantic import BaseModel

from kt_agents_core.state import AgentContext

logger = logging.getLogger(__name__)

S = TypeVar("S", bound=BaseModel)


def approx_tokens(messages: Sequence[BaseMessage]) -> int:
    """Approximate token count for message trimming (chars / 4).

    Shared utility -- replaces the duplicated ``_approx_tokens`` in each
    agent module.
    """
    total = 0
    for m in messages:
        if isinstance(m.content, str):
            total += len(m.content) // 4
    return total


class BaseAgent(ABC, Generic[S]):
    """Template-method base class for LangGraph agents.

    Subclasses must set class attributes and implement abstract methods.
    Hook methods have sensible defaults that can be overridden for
    agent-specific behavior.
    """

    # -- Class attributes (set by subclass) --

    max_trim_tokens: int = 100_000
    """Token limit for ``trim_messages``."""

    terminal_phase: str | None = None
    """Phase string that signals END. ``None`` means no phase-based exit."""

    emit_tool_label: str = "agent"
    """Label for ``activity_log`` events."""

    # -- Constructor --

    def __init__(self, ctx: AgentContext) -> None:
        self.ctx = ctx
        self._state_ref: list[S | None] = [None]

    # -- Abstract methods (must override) --

    @abstractmethod
    def create_tools(self) -> list[BaseTool]:
        """Return the tool list for this agent."""
        ...

    @abstractmethod
    def get_model_id(self) -> str:
        """Return the LiteLLM model ID string."""
        ...

    @abstractmethod
    def get_state_type(self) -> type[S]:
        """Return the Pydantic state class for ``StateGraph()``."""
        ...

    # -- Hook methods (override for special behavior) --

    def get_reasoning_effort(self) -> str | None:
        """Thinking level for the chat model. Default: ``None``."""
        return None

    def get_model_kwargs(self) -> dict[str, Any]:
        """Extra kwargs passed to ``get_chat_model``. Override to set max_tokens etc."""
        return {}

    def check_budget_exhaustion(self, state: S) -> dict[str, Any] | None:
        """Inject advisory/hard-stop messages based on budget state.

        Return a dict (e.g. ``{"messages": [HumanMessage(...)]}`` or
        ``{"phase": "synthesizing"}``) to short-circuit the agent_node,
        or ``None`` to proceed to LLM invocation.
        """
        return None

    async def execute_tool_calls(
        self,
        state: S,
        tool_calls: list[dict[str, Any]],
        tools_by_name: dict[str, BaseTool],
    ) -> list[ToolMessage]:
        """Execute tool calls. Default: sequential with savepoints.

        Override for concurrent execution (orchestrator) or commit-based
        execution (ingest, sub-explorer).
        """
        tool_messages: list[ToolMessage] = []

        for tc in tool_calls:
            name = tc["name"]
            args_summary = ", ".join(
                f"{k}={v!r}" if len(repr(v)) < 80 else f"{k}=<{type(v).__name__}>"
                for k, v in tc.get("args", {}).items()
            )
            logger.info("[%s] tool_call: %s(%s)", self.emit_tool_label, name, args_summary)
            try:
                async with self.ctx.session.begin_nested():
                    tool_fn = tools_by_name[name]
                    result = await tool_fn.ainvoke(tc["args"])
                result_str = str(result)
                result_preview = result_str[:200] + "..." if len(result_str) > 200 else result_str
                logger.info("[%s] tool_result: %s → %s", self.emit_tool_label, name, result_preview)
                tool_messages.append(
                    ToolMessage(
                        content=result_str,
                        tool_call_id=tc["id"],
                        name=name,
                    )
                )
            except Exception as exc:
                logger.exception("Error executing tool %s", name)
                tool_messages.append(
                    ToolMessage(
                        content=f"Error: {type(exc).__name__}: {exc}",
                        tool_call_id=tc["id"],
                        name=name,
                    )
                )

        try:
            await self.ctx.session.flush()
        except Exception:
            logger.exception("Error flushing after tool execution")

        return tool_messages

    def propagate_state(self, state: S) -> dict[str, Any]:
        """Fields to return from tool_node back to LangGraph.

        Must re-echo any mutable state fields so LangGraph preserves
        tool-side mutations. Default returns common orchestrator fields.
        """
        return {}

    def emit_budget_data(self, state: S) -> dict[str, Any]:
        """Budget data dict for ``budget_update`` events."""
        return {}

    def should_stop_after_tools(self, state: S) -> bool:
        """Extra stop condition checked in ``after_tools``.

        Default: ``False``.  Ingest overrides for MAX_ITERATIONS check.
        """
        return False

    def on_llm_error(self, state: S) -> dict[str, Any]:
        """Return dict when LLM invocation fails.

        Default: sets terminal phase if one exists.
        """
        if self.terminal_phase is not None:
            return {"phase": self.terminal_phase}
        return {"messages": [AIMessage(content="I encountered an error processing your request. Please try again.")]}

    route_nudges_to_agent: bool = False
    """When True, HumanMessages from check_budget_exhaustion/post_llm_hook
    are routed back to the agent node so the LLM sees them. Default False
    to preserve existing behavior (HumanMessage -> END)."""

    def pre_agent_hook(self, state: S) -> None:
        """Called at the start of agent_node. Override to set state_ref early."""
        pass

    def post_llm_hook(self, state: S, response: "AIMessage") -> dict[str, Any] | None:
        """Called after LLM responds, before returning from agent_node.

        Override to intercept the LLM response (e.g. nudge when the LLM
        tries to finish early without tool calls). Return a dict to
        replace the default ``{"messages": [response]}``, or ``None``
        to use the default.
        """
        return None

    # -- Core method: build_graph() --

    def build_graph(self) -> tuple[StateGraph, list[BaseTool]]:  # type: ignore[type-arg]
        """Build the LangGraph StateGraph with agent -> tools -> routing.

        Returns ``(graph, tools)`` -- a tuple matching the existing
        signature of ``build_*_graph()`` functions.
        """
        tools = self.create_tools()
        tools_by_name = {t.name: t for t in tools}

        chat_model = self.ctx.model_gateway.get_chat_model(
            model_id=self.get_model_id(),
            reasoning_effort=self.get_reasoning_effort() or None,
            **self.get_model_kwargs(),
        )
        llm_with_tools = chat_model.bind_tools(tools)

        # Capture self for closures
        agent = self

        async def agent_node(state: S) -> dict[str, Any]:  # type: ignore[type-var]
            """LLM decides next actions via native tool calling."""
            agent.pre_agent_hook(state)

            # Check budget exhaustion -- may short-circuit
            budget_result = agent.check_budget_exhaustion(state)
            if budget_result is not None:
                return budget_result

            trimmed = trim_messages(
                state.messages,
                max_tokens=agent.max_trim_tokens,
                token_counter=approx_tokens,
                strategy="last",
                include_system=True,
            )

            try:
                response = await llm_with_tools.ainvoke(trimmed)
            except Exception:
                logger.exception(
                    "Error in %s agent_node LLM call",
                    agent.emit_tool_label,
                )
                return agent.on_llm_error(state)

            # Allow subclass to intercept (e.g. nudge on early finish)
            if isinstance(response, AIMessage):
                override = agent.post_llm_hook(state, response)
                if override is not None:
                    return override

            return {"messages": [response]}

        async def tool_node(state: S) -> dict[str, Any]:  # type: ignore[type-var]
            """Execute tool calls from the last AIMessage."""
            agent._state_ref[0] = state
            ai_msg = state.messages[-1]

            if not isinstance(ai_msg, AIMessage) or not ai_msg.tool_calls:
                return {}

            tool_messages = await agent.execute_tool_calls(state, ai_msg.tool_calls, tools_by_name)

            # Emit budget update
            budget_data = agent.emit_budget_data(state)
            if budget_data:
                await agent.ctx.emit("budget_update", data=budget_data)

            # Build return dict: messages + propagated state
            result: dict[str, Any] = {"messages": tool_messages}
            result.update(agent.propagate_state(state))
            return result

        terminal = agent.terminal_phase

        def should_continue(state: S) -> str:  # type: ignore[type-var]
            """Route after agent_node: go to tools or end."""
            if terminal is not None and getattr(state, "phase", None) == terminal:
                return END
            last_msg = state.messages[-1] if state.messages else None
            if isinstance(last_msg, AIMessage) and last_msg.tool_calls:
                return "tools"
            # Nudge HumanMessages loop back so the LLM sees them
            if agent.route_nudges_to_agent and isinstance(last_msg, HumanMessage):
                return "agent"
            return END

        def after_tools(state: S) -> str:  # type: ignore[type-var]
            """Route after tool_node: back to agent or end."""
            if terminal is not None and getattr(state, "phase", None) == terminal:
                return END
            if agent.should_stop_after_tools(state):
                return END
            return "agent"

        state_type = self.get_state_type()
        graph = StateGraph(state_type)
        graph.add_node("agent", agent_node)
        graph.add_node("tools", tool_node)
        graph.set_entry_point("agent")

        # Build the routing map based on whether after_tools can END
        agent_routes: dict[str, str] = {"tools": "tools", END: END}
        if self.route_nudges_to_agent:
            agent_routes["agent"] = "agent"
        graph.add_conditional_edges("agent", should_continue, agent_routes)

        # If there's a terminal phase or should_stop_after_tools, tools can route to END
        if terminal is not None or type(self).should_stop_after_tools is not BaseAgent.should_stop_after_tools:
            graph.add_conditional_edges("tools", after_tools, {"agent": "agent", END: END})
        else:
            # Query agent pattern: tools always go back to agent
            graph.add_conditional_edges("tools", after_tools, {"agent": "agent"})

        return graph, tools

    # -- Utility: access state from tools --

    def _get_state(self) -> S:
        """Access the current mutable state from tool closures.

        Raises ``RuntimeError`` if called outside a tool_node execution.
        """
        state = self._state_ref[0]
        if state is None:
            raise RuntimeError("Agent state not available outside tool_node")
        return state
