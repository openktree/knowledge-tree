"""LangChain ChatModel adapter wrapping our ModelGateway for LangGraph compatibility.

This bridges our LiteLLM-based ModelGateway with LangGraph's expectation of a
BaseChatModel. Tool-calling is handled by injecting tool schemas into the prompt
and parsing structured JSON responses.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from langchain_core.callbacks import AsyncCallbackManagerForLLMRun, CallbackManagerForLLMRun
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, ToolCall
from langchain_core.outputs import ChatGeneration, ChatResult

from kt_models.gateway import ModelGateway

logger = logging.getLogger(__name__)


def _build_tool_schema_text(tools: list[dict[str, Any]]) -> str:
    """Build a compact text description of available tools for the system prompt."""
    lines = ["You have these tools available. To call a tool, respond with JSON:", ""]
    lines.append('{"tool_calls": [{"name": "<tool_name>", "args": {<arguments>}}]}')
    lines.append("")
    lines.append("If you do NOT need to call a tool, respond with plain text (no JSON).")
    lines.append("")
    lines.append("Available tools:")

    for t in tools:
        fn = t.get("function", t)
        name = fn.get("name", "unknown")
        desc = fn.get("description", "")
        params = fn.get("parameters", {})
        props = params.get("properties", {})
        required = params.get("required", [])

        param_parts = []
        for pname, pschema in props.items():
            ptype = pschema.get("type", "any")
            req = " (required)" if pname in required else " (optional)"
            pdesc = pschema.get("description", "")
            param_parts.append(f"    - {pname}: {ptype}{req} — {pdesc}")

        lines.append(f"\n  {name}: {desc}")
        if param_parts:
            lines.extend(param_parts)

    return "\n".join(lines)


def _parse_tool_calls(text: str) -> list[ToolCall]:
    """Try to parse tool_calls JSON from model response text."""
    # Try to find JSON block
    # First try ```json blocks
    json_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if json_match:
        text_to_parse = json_match.group(1)
    else:
        # Try to find raw JSON object
        start = text.find("{")
        end = text.rfind("}") + 1
        if start >= 0 and end > start:
            text_to_parse = text[start:end]
        else:
            return []

    try:
        data = json.loads(text_to_parse)
    except json.JSONDecodeError:
        return []

    # Handle {"tool_calls": [...]} format
    if "tool_calls" in data:
        calls = data["tool_calls"]
        if isinstance(calls, list):
            result = []
            for i, call in enumerate(calls):
                if isinstance(call, dict) and "name" in call:
                    result.append(
                        ToolCall(
                            name=call["name"],
                            args=call.get("args", {}),
                            id=f"call_{i}",
                        )
                    )
            return result

    # Handle single tool call {"name": "...", "args": {...}}
    if "name" in data and "args" in data:
        return [ToolCall(name=data["name"], args=data.get("args", {}), id="call_0")]

    # Handle {"action": "tool_name", ...} format (legacy compat)
    if "action" in data and data["action"] not in ("synthesize",):
        action = data.pop("action")
        return [ToolCall(name=action, args=data, id="call_0")]

    return []


class GatewayChat(BaseChatModel):
    """LangChain ChatModel that delegates to our ModelGateway.

    This allows LangGraph to use our existing LiteLLM-based gateway
    for all LLM calls while maintaining tool-calling support.
    """

    gateway: Any  # ModelGateway — use Any to avoid pydantic validation issues
    model_id: str = ""
    temperature: float = 0.3
    max_tokens: int = 4000
    _bound_tools: list[dict[str, Any]] = []

    model_config = {"arbitrary_types_allowed": True}

    @property
    def _llm_type(self) -> str:
        return "gateway-chat"

    def bind_tools(self, tools: list[Any], **kwargs: Any) -> GatewayChat:  # type: ignore[override]
        """Return a copy of this model with tools bound for tool-calling."""
        schemas = []
        for t in tools:
            if hasattr(t, "tool_call_schema"):
                # LangChain BaseTool
                schema = {
                    "type": "function",
                    "function": {
                        "name": t.name,
                        "description": t.description or "",
                        "parameters": t.args_schema.model_json_schema() if t.args_schema else {},
                    },
                }
            elif isinstance(t, dict):
                schema = t
            else:
                continue
            schemas.append(schema)

        clone = GatewayChat(
            gateway=self.gateway,
            model_id=self.model_id,
            temperature=self.temperature,
            max_tokens=self.max_tokens,
        )
        clone._bound_tools = schemas
        return clone

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        raise NotImplementedError("Use async interface (_agenerate)")

    async def _agenerate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: AsyncCallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        """Async generation using our ModelGateway."""
        # Convert LangChain messages to our gateway format
        gateway_messages: list[dict[str, str]] = []
        system_prompt: str | None = None

        for msg in messages:
            role = msg.type
            content = str(msg.content) if msg.content else ""

            if role == "system":
                system_prompt = content
            elif role == "human":
                gateway_messages.append({"role": "user", "content": content})
            elif role == "ai":
                gateway_messages.append({"role": "assistant", "content": content})
            elif role == "tool":
                # Format tool results as user messages for models that
                # don't support native tool_result role
                tool_name = getattr(msg, "name", "tool")
                gateway_messages.append({"role": "user", "content": f"[Tool Result: {tool_name}]\n{content}"})
            else:
                gateway_messages.append({"role": "user", "content": content})

        # Inject tool descriptions into system prompt if tools are bound
        if self._bound_tools:
            tool_text = _build_tool_schema_text(self._bound_tools)
            if system_prompt:
                system_prompt = f"{system_prompt}\n\n{tool_text}"
            else:
                system_prompt = tool_text

        gw: ModelGateway = self.gateway
        response_text = await gw.generate(
            self.model_id,
            gateway_messages,
            system_prompt=system_prompt,
            temperature=self.temperature,
            max_tokens=self.max_tokens,
        )

        # Try to parse tool calls from response
        tool_calls = _parse_tool_calls(response_text) if self._bound_tools else []

        if tool_calls:
            ai_msg = AIMessage(content="", tool_calls=tool_calls)
        else:
            ai_msg = AIMessage(content=response_text)

        return ChatResult(generations=[ChatGeneration(message=ai_msg)])
