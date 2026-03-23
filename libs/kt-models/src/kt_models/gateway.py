import asyncio
import json
import logging
import re
from typing import Any

from langchain_openai import ChatOpenAI
from langsmith import traceable
from litellm import acompletion
from pydantic import SecretStr

from kt_config.settings import get_settings

logger = logging.getLogger(__name__)

_MAX_RETRIES = 3
_BASE_DELAY = 1.0  # seconds
_RATE_LIMIT_MAX_RETRIES = 6
_RATE_LIMIT_BASE_DELAY = 5.0  # seconds — 429s need longer backoff
_JSON_PARSE_MAX_RETRIES = 4
_JSON_RETRY_BASE_DELAY = 5.0  # seconds — exponential backoff between JSON retries
_JSON_RETRY_MAX_DELAY = 120.0  # seconds — cap for backoff (2 minutes)

# Regex to strip markdown code fences: ```json ... ``` or ``` ... ```
_CODE_FENCE_RE = re.compile(r"^```(?:json)?\s*\n?", re.MULTILINE)
_CODE_FENCE_END_RE = re.compile(r"\n?```\s*$", re.MULTILINE)


def _extract_json(raw: str) -> str:
    """Best-effort cleanup of LLM output to extract a JSON object or array.

    Handles common issues:
    - Markdown code fences (```json ... ```)
    - Leading/trailing prose around the JSON
    - Truncated JSON (attempts to close open braces/brackets)
    """
    text = raw.strip()
    if not text:
        return text

    # Strip markdown code fences
    text = _CODE_FENCE_RE.sub("", text)
    text = _CODE_FENCE_END_RE.sub("", text)
    text = text.strip()

    # Find the outermost JSON structure
    # Try object first, then array
    for open_ch, close_ch in (("{", "}"), ("[", "]")):
        start = text.find(open_ch)
        if start < 0:
            continue
        end = text.rfind(close_ch)
        if end > start:
            return text[start : end + 1]
        # No closing bracket found — JSON is likely truncated.
        # Take from the opening bracket to the end and try to close it.
        partial = text[start:]
        partial = _close_truncated_json(partial, open_ch, close_ch)
        return partial

    return text


def _close_truncated_json(text: str, open_ch: str, close_ch: str) -> str:
    """Attempt to close truncated JSON by balancing braces/brackets.

    Strips any trailing incomplete value (cut-off string/number), then
    appends closing delimiters to balance the structure.
    """
    # Strip trailing incomplete string value (unclosed quote)
    # Look for last complete JSON value boundary
    stripped = text.rstrip()
    if stripped and stripped[-1] not in (
        close_ch,
        "}",
        "]",
        '"',
        "0",
        "1",
        "2",
        "3",
        "4",
        "5",
        "6",
        "7",
        "8",
        "9",
        "true"[-1],
        "false"[-1],
        "null"[-1],
    ):
        # Likely mid-value — backtrack to last comma, closing bracket, or colon
        for i in range(len(stripped) - 1, -1, -1):
            if stripped[i] in (",", open_ch, "{", "["):
                stripped = stripped[: i + 1]
                break

    # Count unbalanced braces and brackets
    depth_brace = 0
    depth_bracket = 0
    in_string = False
    escape = False
    for ch in stripped:
        if escape:
            escape = False
            continue
        if ch == "\\":
            escape = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == "{":
            depth_brace += 1
        elif ch == "}":
            depth_brace -= 1
        elif ch == "[":
            depth_bracket += 1
        elif ch == "]":
            depth_bracket -= 1

    # Remove trailing comma before closing
    result = stripped.rstrip()
    if result and result[-1] == ",":
        result = result[:-1]

    # Close in reverse order of nesting (brackets first, then braces — rough heuristic)
    result += "]" * max(0, depth_bracket)
    result += "}" * max(0, depth_brace)

    return result


def _is_retryable(exc: Exception) -> bool:
    """Return True for transient errors that should be retried."""
    from litellm.exceptions import (
        InternalServerError,
        RateLimitError,
        ServiceUnavailableError,
        Timeout,
    )

    return isinstance(exc, (RateLimitError, ServiceUnavailableError, InternalServerError, Timeout))


def _record_llm_usage(response: Any, model: str) -> None:
    """Record token usage from a LiteLLM response to the ContextVar accumulator."""
    from kt_models.usage import record_usage

    usage = getattr(response, "usage", None)
    if usage is None:
        return
    prompt_tokens = getattr(usage, "prompt_tokens", 0) or 0
    completion_tokens = getattr(usage, "completion_tokens", 0) or 0
    if prompt_tokens == 0 and completion_tokens == 0:
        return

    # OpenRouter returns cost directly on the usage object
    cost_usd = float(getattr(usage, "cost", 0) or 0)

    # Fallback: try litellm's cost calculator
    if cost_usd == 0.0:
        try:
            from litellm import completion_cost

            cost_usd = float(completion_cost(completion_response=response) or 0.0)
        except Exception:
            pass

    record_usage(model, prompt_tokens, completion_tokens, cost_usd)


def _format_usage(response: Any, max_tokens: int) -> str:
    """Format token usage from a LiteLLM response for logging."""
    usage = getattr(response, "usage", None)
    if usage is None:
        return ""
    prompt_tokens = getattr(usage, "prompt_tokens", None)
    completion_tokens = getattr(usage, "completion_tokens", None)
    if prompt_tokens is None and completion_tokens is None:
        return ""
    return f" [tokens: prompt={prompt_tokens}, completion={completion_tokens}, max={max_tokens}]"


class ModelGateway:
    """Gateway for calling AI models via LiteLLM/OpenRouter.

    Supports single and parallel model generation.
    """

    def __init__(self, api_key: str | None = None) -> None:
        settings = get_settings()
        self._api_key = api_key or settings.openrouter_api_key
        self.default_model: str = settings.default_model
        self.decomposition_model: str = settings.decomposition_model or settings.default_model
        self.entity_extraction_model: str = settings.entity_extraction_model or self.decomposition_model
        self.file_decomposition_model: str = settings.file_decomposition_model or settings.default_model
        self.synthesis_model: str = settings.synthesis_model or settings.default_model
        self.dimension_model: str = settings.dimension_model or settings.default_model
        self.chat_model: str = settings.chat_model or settings.default_model
        self.orchestrator_model: str = settings.orchestrator_model or settings.default_model
        self.scope_model: str = settings.scope_model or self.orchestrator_model
        self.agent_select_model: str = settings.agent_select_model or self.orchestrator_model
        self.prioritization_model: str = settings.prioritization_model or settings.default_model
        self.parent_selection_model: str = settings.parent_selection_model or settings.default_model
        self.definition_model: str = settings.definition_model or settings.default_model
        self.crystallization_model: str = (
            settings.crystallization_model or settings.ontology_model or settings.default_model
        )

        # Per-role thinking/reasoning effort levels
        _default_tl = settings.default_thinking_level
        self.decomposition_thinking_level: str = settings.decomposition_thinking_level or _default_tl
        self.entity_extraction_thinking_level: str = (
            settings.entity_extraction_thinking_level or self.decomposition_thinking_level
        )
        self.file_decomposition_thinking_level: str = settings.file_decomposition_thinking_level or _default_tl
        self.synthesis_thinking_level: str = settings.synthesis_thinking_level or _default_tl
        self.dimension_thinking_level: str = settings.dimension_thinking_level or _default_tl
        self.chat_thinking_level: str = settings.chat_thinking_level or _default_tl
        self.orchestrator_thinking_level: str = settings.orchestrator_thinking_level or _default_tl
        self.scope_thinking_level: str = settings.scope_thinking_level or self.orchestrator_thinking_level
        self.agent_select_thinking_level: str = settings.agent_select_thinking_level or self.orchestrator_thinking_level
        self.parent_selection_thinking_level: str = settings.parent_selection_thinking_level or _default_tl
        self.definition_thinking_level: str = settings.definition_thinking_level or _default_tl
        self.crystallization_thinking_level: str = settings.crystallization_thinking_level or _default_tl

    async def _call_with_retry(self, **kwargs: Any) -> Any:
        """Call acompletion with exponential backoff on retryable errors.

        Each attempt is wrapped in ``asyncio.wait_for`` so a hung
        LLM provider cannot block the pipeline indefinitely.

        Rate limit errors (429) get more retries and longer backoff
        since they typically resolve within 30-60s.
        """
        from litellm.exceptions import RateLimitError

        timeout = get_settings().llm_call_timeout_seconds
        last_exc: Exception | None = None
        max_retries = _MAX_RETRIES
        base_delay = _BASE_DELAY

        for attempt in range(_RATE_LIMIT_MAX_RETRIES):
            # After initial retries exhausted, only continue for rate limits
            if attempt >= max_retries and not isinstance(last_exc, RateLimitError):
                break
            try:
                response = await asyncio.wait_for(
                    acompletion(**kwargs),
                    timeout=timeout,
                )
                # Record usage if tracking is active
                _record_llm_usage(response, kwargs.get("model", "unknown"))
                return response
            except asyncio.TimeoutError:
                model = kwargs.get("model", "?")
                logger.warning(
                    "LLM call to %s timed out after %ds (attempt %d/%d)",
                    model,
                    timeout,
                    attempt + 1,
                    max_retries,
                )
                last_exc = TimeoutError(f"LLM call to {model} timed out after {timeout}s")
                delay = _BASE_DELAY * (2**attempt)
                await asyncio.sleep(delay)
            except Exception as e:
                if not _is_retryable(e):
                    raise
                last_exc = e
                # Rate limit errors get longer backoff and more retries
                if isinstance(e, RateLimitError):
                    max_retries = _RATE_LIMIT_MAX_RETRIES
                    base_delay = _RATE_LIMIT_BASE_DELAY
                delay = base_delay * (2 ** min(attempt, 4))  # cap multiplier at 16x
                logger.warning(
                    "%s (attempt %d/%d), retrying in %.1fs",
                    "Rate limited" if isinstance(e, RateLimitError) else "Transient error",
                    attempt + 1,
                    max_retries,
                    delay,
                )
                await asyncio.sleep(delay)
        raise last_exc  # type: ignore[misc]

    def get_chat_model(self, model_id: str | None = None, **kwargs: Any) -> ChatOpenAI:
        """Return a LangChain ChatModel for use with bind_tools.

        Uses ChatOpenAI pointed at the OpenRouter API, which provides
        native tool-calling support across all hosted models.

        Pass reasoning_effort="low"|"medium"|"high" to enable model thinking.
        """
        mid = model_id or self.chat_model
        # OpenRouter models use "openrouter/" prefix — strip for ChatOpenAI
        model_name = mid.removeprefix("openrouter/")
        extra_kwargs: dict[str, Any] = {}
        reasoning_effort = kwargs.pop("reasoning_effort", None)
        if reasoning_effort:
            extra_kwargs["reasoning_effort"] = reasoning_effort
        return ChatOpenAI(
            model=model_name,
            api_key=SecretStr(self._api_key) if self._api_key else None,
            base_url="https://openrouter.ai/api/v1",
            temperature=float(kwargs.get("temperature", 0.3)),
            max_tokens=int(kwargs.get("max_tokens", 1000)),
            model_kwargs=extra_kwargs,
        )

    @traceable(name="ModelGateway.generate_with_tools")
    async def generate_with_tools(
        self,
        model_id: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        system_prompt: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 2000,
    ) -> list[dict[str, Any]]:
        """Generate a response with tool calling and return parsed tool calls.

        Args:
            model_id: The model identifier.
            messages: List of message dicts with "role" and "content".
            tools: OpenAI-format tool definitions.
            system_prompt: Optional system message prepended to messages.
            temperature: Sampling temperature.
            max_tokens: Maximum tokens in the response.

        Returns:
            List of dicts with "name" and "arguments" (parsed dict) for each
            tool call the model made. Empty list if no tool calls.
        """
        msgs: list[dict[str, Any]] = []
        if system_prompt:
            msgs.append({"role": "system", "content": system_prompt})
        msgs.extend(messages)

        response = await self._call_with_retry(
            model=model_id,
            messages=msgs,
            tools=tools,
            api_key=self._api_key,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        choice = response.choices[0]  # type: ignore[union-attr]
        tool_calls = getattr(choice.message, "tool_calls", None) or []

        results: list[dict[str, Any]] = []
        for tc in tool_calls:
            fn = tc.function
            try:
                args = json.loads(fn.arguments) if isinstance(fn.arguments, str) else fn.arguments
            except json.JSONDecodeError:
                logger.warning(
                    "Failed to parse tool call arguments from %s: %.200s",
                    model_id,
                    fn.arguments,
                )
                continue
            results.append({"name": fn.name, "arguments": args})
        return results

    @traceable(name="ModelGateway.generate")
    async def generate(
        self,
        model_id: str,
        messages: list[dict[str, Any]],
        system_prompt: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 2000,
        reasoning_effort: str | None = None,
    ) -> str:
        """Generate a response from a single model.

        Args:
            model_id: The model identifier (e.g. "openrouter/google/gemini-2.0-flash-001").
            messages: List of message dicts with "role" and "content" (content may be a list for multimodal).
            system_prompt: Optional system message prepended to messages.
            temperature: Sampling temperature.
            max_tokens: Maximum tokens in the response.
            reasoning_effort: Optional thinking level ("none", "low", "medium", "high").

        Returns:
            The model's response text.
        """
        msgs: list[dict[str, Any]] = []
        if system_prompt:
            msgs.append({"role": "system", "content": system_prompt})
        msgs.extend(messages)

        extra: dict[str, Any] = {}
        if reasoning_effort:
            extra["reasoning_effort"] = reasoning_effort
            extra["allowed_openai_params"] = ["reasoning_effort"]

        response = await self._call_with_retry(
            model=model_id,
            messages=msgs,
            api_key=self._api_key,
            temperature=temperature,
            max_tokens=max_tokens,
            **extra,
        )
        return response.choices[0].message.content or ""  # type: ignore[union-attr]

    @traceable(name="ModelGateway.generate_json")
    async def generate_json(
        self,
        model_id: str,
        messages: list[dict[str, Any]],
        system_prompt: str | None = None,
        temperature: float = 0.0,
        max_tokens: int = 16000,
        reasoning_effort: str | None = None,
    ) -> dict:  # type: ignore[type-arg]
        """Generate a JSON response from a model.

        Uses response_format={"type": "json_object"} for reliable JSON output.

        Args:
            model_id: The model identifier.
            messages: List of message dicts with "role" and "content" (content may be a list for multimodal).
            system_prompt: Optional system message prepended to messages.
            temperature: Sampling temperature (default 0.0 for deterministic JSON).
            max_tokens: Maximum tokens in the response (default 16k to handle large fact extractions).
            reasoning_effort: Optional thinking level ("none", "low", "medium", "high").

        Returns:
            Parsed JSON dict from the model's response.
        """
        msgs: list[dict[str, Any]] = []
        if system_prompt:
            msgs.append({"role": "system", "content": system_prompt})
        msgs.extend(messages)

        original_msgs = list(msgs)

        extra: dict[str, Any] = {}
        if reasoning_effort:
            extra["reasoning_effort"] = reasoning_effort
            extra["allowed_openai_params"] = ["reasoning_effort"]

        for attempt in range(1 + _JSON_PARSE_MAX_RETRIES):
            response = await self._call_with_retry(
                model=model_id,
                messages=msgs,
                api_key=self._api_key,
                temperature=temperature,
                max_tokens=max_tokens,
                response_format={"type": "json_object"},
                plugins=[{"id": "response-healing"}],
                **extra,
            )
            raw_text: str = response.choices[0].message.content or ""  # type: ignore[union-attr]
            finish_reason: str = response.choices[0].finish_reason or ""  # type: ignore[union-attr]
            truncated = finish_reason == "length"
            usage_str = _format_usage(response, max_tokens)

            try:
                return json.loads(raw_text)  # type: ignore[no-any-return]
            except json.JSONDecodeError:
                pass

            # Try cleaning up the output (markdown fences, preamble, truncation)
            cleaned = _extract_json(raw_text)
            if cleaned != raw_text:
                try:
                    result = json.loads(cleaned)  # type: ignore[no-any-return]
                    logger.info(
                        "JSON cleanup recovered response from %s (raw=%d chars, cleaned=%d chars)",
                        model_id,
                        len(raw_text),
                        len(cleaned),
                    )
                    return result
                except json.JSONDecodeError:
                    pass

            cause = "token_limit" if truncated else "format_error"
            is_last_attempt = attempt >= _JSON_PARSE_MAX_RETRIES

            # Always dump full response at DEBUG level for troubleshooting
            logger.debug(
                "JSON parse failure (attempt %d/%d) from %s (%d chars, finish_reason=%s, cause=%s)%s:\n%s",
                attempt + 1,
                1 + _JSON_PARSE_MAX_RETRIES,
                model_id,
                len(raw_text),
                finish_reason,
                cause,
                usage_str,
                raw_text,
            )
            if cleaned != raw_text:
                logger.debug(
                    "Cleaned response (attempt %d, %d chars):\n%s",
                    attempt + 1,
                    len(cleaned),
                    cleaned,
                )

            # Detect transient upstream truncation: content exists but
            # usage reports 0 tokens — likely an OpenRouter transport glitch.
            usage = getattr(response, "usage", None)
            prompt_tok = getattr(usage, "prompt_tokens", None) if usage else None
            completion_tok = getattr(usage, "completion_tokens", None) if usage else None
            is_transient = raw_text and prompt_tok == 0 and completion_tok == 0

            if is_last_attempt:
                logger.warning(
                    "Failed to parse JSON from %s after %d attempt(s) (last cause: %s)%s. Preview: %.200s",
                    model_id,
                    attempt + 1,
                    cause,
                    usage_str,
                    raw_text,
                )
                return {}

            # Exponential backoff — longer for transient upstream issues
            if is_transient:
                delay = min(
                    _JSON_RETRY_BASE_DELAY * (2**attempt),
                    _JSON_RETRY_MAX_DELAY,
                )
                logger.info(
                    "Transient upstream error from %s (0 tokens reported), backing off %.1fs before retry %d/%d",
                    model_id,
                    delay,
                    attempt + 1,
                    _JSON_PARSE_MAX_RETRIES,
                )
                await asyncio.sleep(delay)

            if is_transient:
                # Upstream glitch — retry same request, no message modification
                logger.warning(
                    "JSON parse retry %d/%d for %s — cause: transient_upstream (0 tokens, %d chars received)%s",
                    attempt + 1,
                    _JSON_PARSE_MAX_RETRIES,
                    model_id,
                    len(raw_text),
                    usage_str,
                )
                msgs = list(original_msgs)
            elif truncated:
                logger.warning(
                    "JSON parse retry %d/%d for %s — cause: token_limit "
                    "(finish_reason='length')%s. Requesting concise output.",
                    attempt + 1,
                    _JSON_PARSE_MAX_RETRIES,
                    model_id,
                    usage_str,
                )
                msgs = [
                    *original_msgs,
                    {"role": "assistant", "content": raw_text[:500] + "...[truncated]"},
                    {
                        "role": "user",
                        "content": (
                            "Your previous response was truncated because it exceeded the "
                            "token limit, resulting in invalid JSON. Please respond again "
                            "with FEWER items and SHORTER descriptions. Prioritize the most "
                            "important facts. You MUST return complete, valid JSON."
                        ),
                    },
                ]
            else:
                logger.warning(
                    "JSON parse retry %d/%d for %s — cause: format_error "
                    "(model returned non-JSON output)%s. Preview: %.200s",
                    attempt + 1,
                    _JSON_PARSE_MAX_RETRIES,
                    model_id,
                    usage_str,
                    raw_text,
                )
                msgs = [
                    *original_msgs,
                    {"role": "assistant", "content": raw_text[:1000]},
                    {
                        "role": "user",
                        "content": (
                            "Your previous response was not valid JSON. "
                            "Please respond with ONLY a valid JSON object. "
                            "No markdown code fences, no explanatory text — just the JSON."
                        ),
                    },
                ]

        return {}  # unreachable, but satisfies type checker

    async def generate_parallel(
        self,
        model_ids: list[str],
        messages: list[dict[str, Any]],
        system_prompt: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 2000,
        reasoning_effort: str | None = None,
    ) -> dict[str, str]:
        """Generate responses from multiple models in parallel.

        Args:
            model_ids: List of model identifiers.
            messages: List of message dicts shared across all models (content may be a list for multimodal).
            system_prompt: Optional system message.
            temperature: Sampling temperature.
            max_tokens: Maximum tokens per response.
            reasoning_effort: Optional thinking level ("none", "low", "medium", "high").

        Returns:
            Dict mapping model_id to response text. On error, the value
            is a string starting with "Error: ".
        """

        async def _call(mid: str) -> tuple[str, str]:
            try:
                result = await self.generate(
                    mid,
                    messages,
                    system_prompt,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    reasoning_effort=reasoning_effort,
                )
                return (mid, result)
            except Exception as e:
                return (mid, f"Error: {e}")

        pairs = await asyncio.gather(*[_call(mid) for mid in model_ids])
        return dict(pairs)
