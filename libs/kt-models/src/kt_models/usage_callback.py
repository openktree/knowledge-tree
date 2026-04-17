"""LangChain callback that records usage from ``ChatOpenAI`` responses.

LangGraph agents invoke the chat model via ``ainvoke`` / ``astream``,
which bypasses :meth:`ModelGateway._call_with_retry` — so the LiteLLM
path's ``_record_llm_usage`` never fires for them. This callback closes
that gap by hooking ``on_llm_end`` and forwarding token usage + cost to
:func:`record_llm_usage` with an optional bound :class:`ExpenseContext`.
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from langchain_core.callbacks.base import BaseCallbackHandler
from langchain_core.outputs import LLMResult

from kt_models.expense import require_current_expense
from kt_models.usage_sink import record_llm_usage

logger = logging.getLogger(__name__)


class UsageTrackingCallback(BaseCallbackHandler):
    """Emit usage records from LangChain chat-model invocations.

    ``on_llm_end`` runs inside the same async context as the
    originating ``ainvoke`` / ``astream`` call, so it can read the
    ambient :class:`ExpenseContext` via ``require_current_expense()``.
    No constructor wiring — attaching this callback is enough.
    """

    raise_error: bool = False
    run_inline: bool = True

    def __init__(self, *, model_id: str) -> None:
        self._model_id = model_id

    def on_llm_end(
        self,
        response: LLMResult,
        *,
        run_id: UUID,  # noqa: ARG002 — required by BaseCallbackHandler signature
        parent_run_id: UUID | None = None,  # noqa: ARG002
        **kwargs: Any,  # noqa: ARG002
    ) -> None:
        prompt_tokens, completion_tokens, cost_usd = _extract_usage(response)
        if prompt_tokens == 0 and completion_tokens == 0:
            return
        record_llm_usage(
            model_id=self._model_id,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            cost_usd=cost_usd,
            expense=require_current_expense(),
        )


def _extract_usage(response: LLMResult) -> tuple[int, int, float]:
    """Pull prompt_tokens / completion_tokens / cost from an ``LLMResult``.

    Supports both the legacy ``llm_output["token_usage"]`` shape and the
    newer per-generation ``usage_metadata`` shape produced by
    ``langchain-openai`` against OpenRouter.
    """
    prompt_tokens = 0
    completion_tokens = 0
    cost_usd = 0.0

    llm_output = response.llm_output or {}
    token_usage = llm_output.get("token_usage") or {}
    if token_usage:
        prompt_tokens = int(token_usage.get("prompt_tokens") or 0)
        completion_tokens = int(token_usage.get("completion_tokens") or 0)
        cost_usd = float(token_usage.get("cost") or token_usage.get("total_cost") or 0.0)

    if prompt_tokens == 0 and completion_tokens == 0:
        for gen_list in response.generations or []:
            for gen in gen_list:
                msg = getattr(gen, "message", None)
                meta = getattr(msg, "usage_metadata", None) if msg is not None else None
                if not meta:
                    continue
                prompt_tokens += int(meta.get("input_tokens") or 0)
                completion_tokens += int(meta.get("output_tokens") or 0)

    if cost_usd == 0.0:
        for gen_list in response.generations or []:
            for gen in gen_list:
                msg = getattr(gen, "message", None)
                resp_meta = getattr(msg, "response_metadata", None) if msg is not None else None
                if not resp_meta:
                    continue
                # OpenRouter returns cost in response_metadata.usage.cost when
                # extra_body={"usage": {"include": True}} is set.
                usage_field = resp_meta.get("token_usage") or resp_meta.get("usage") or {}
                maybe_cost = usage_field.get("cost") if isinstance(usage_field, dict) else None
                if maybe_cost is not None:
                    try:
                        cost_usd = float(maybe_cost)
                        break
                    except (TypeError, ValueError):
                        pass

    return prompt_tokens, completion_tokens, cost_usd
