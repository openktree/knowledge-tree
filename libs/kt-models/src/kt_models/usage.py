"""ContextVar-based LLM usage accumulator.

Transparently tracks token usage across async tasks without modifying
return types. Each Hatchet task starts tracking, LLM calls auto-record,
and the task collects the summary at the end.

Sub-task labeling: Use ``set_usage_task(label)`` / ``clear_usage_task()``
or the ``usage_task(label)`` context manager to tag LLM calls with a
sub-task label (e.g. "decomposition", "entity_extraction"). The
accumulator groups records by label via ``by_task()``.
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class ModelUsageRecord:
    """Single LLM call usage record."""

    model: str
    prompt_tokens: int
    completion_tokens: int
    cost_usd: float = 0.0
    task_label: str | None = None


@dataclass
class UsageAccumulator:
    """Accumulates usage records across multiple LLM calls."""

    records: list[ModelUsageRecord] = field(default_factory=list)

    def record(
        self,
        model: str,
        prompt_tokens: int,
        completion_tokens: int,
        cost_usd: float = 0.0,
        task_label: str | None = None,
    ) -> None:
        self.records.append(
            ModelUsageRecord(
                model=model,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                cost_usd=cost_usd,
                task_label=task_label,
            )
        )

    @property
    def total_prompt_tokens(self) -> int:
        return sum(r.prompt_tokens for r in self.records)

    @property
    def total_completion_tokens(self) -> int:
        return sum(r.completion_tokens for r in self.records)

    @property
    def total_cost_usd(self) -> float:
        return sum(r.cost_usd for r in self.records)

    def by_model(self) -> dict[str, dict[str, int | float]]:
        """Aggregate usage per model."""
        agg: dict[str, dict[str, int | float]] = {}
        for r in self.records:
            if r.model not in agg:
                agg[r.model] = {
                    "prompt_tokens": 0,
                    "completion_tokens": 0,
                    "cost_usd": 0.0,
                }
            agg[r.model]["prompt_tokens"] += r.prompt_tokens
            agg[r.model]["completion_tokens"] += r.completion_tokens
            agg[r.model]["cost_usd"] += r.cost_usd
        return agg

    def by_task(self) -> dict[str, dict[str, int | float]]:
        """Aggregate usage per task label.

        Records without a task_label are grouped under ``"other"``.
        """
        agg: dict[str, dict[str, int | float]] = {}
        for r in self.records:
            key = r.task_label or "other"
            if key not in agg:
                agg[key] = {
                    "prompt_tokens": 0,
                    "completion_tokens": 0,
                    "cost_usd": 0.0,
                }
            agg[key]["prompt_tokens"] += r.prompt_tokens
            agg[key]["completion_tokens"] += r.completion_tokens
            agg[key]["cost_usd"] += r.cost_usd
        return agg

    def to_dict(self) -> dict[str, object]:
        return {
            "total_prompt_tokens": self.total_prompt_tokens,
            "total_completion_tokens": self.total_completion_tokens,
            "total_cost_usd": self.total_cost_usd,
            "by_model": self.by_model(),
            "by_task": self.by_task(),
        }


_usage_ctx: ContextVar[UsageAccumulator | None] = ContextVar("llm_usage_accumulator", default=None)

_task_label_ctx: ContextVar[str | None] = ContextVar("llm_usage_task_label", default=None)


def start_usage_tracking() -> UsageAccumulator:
    """Start tracking LLM usage in the current async context."""
    acc = UsageAccumulator()
    _usage_ctx.set(acc)
    return acc


def get_current_accumulator() -> UsageAccumulator | None:
    """Get the current accumulator, or None if not tracking."""
    return _usage_ctx.get(None)


def stop_usage_tracking() -> UsageAccumulator | None:
    """Stop tracking and return the accumulated usage."""
    acc = _usage_ctx.get(None)
    _usage_ctx.set(None)
    return acc


def set_usage_task(label: str) -> None:
    """Set the current task label for subsequent LLM calls."""
    _task_label_ctx.set(label)


def clear_usage_task() -> None:
    """Clear the current task label."""
    _task_label_ctx.set(None)


def get_current_task_label() -> str | None:
    """Get the current task label, or None if not set."""
    return _task_label_ctx.get(None)


@contextmanager
def usage_task(label: str):
    """Context manager to tag LLM calls with a sub-task label.

    Usage::

        with usage_task("decomposition"):
            await gateway.generate_json(...)
    """
    set_usage_task(label)
    try:
        yield
    finally:
        clear_usage_task()


def record_usage(
    model: str,
    prompt_tokens: int,
    completion_tokens: int,
    cost_usd: float = 0.0,
) -> None:
    """Record usage if tracking is active. No-op otherwise."""
    acc = _usage_ctx.get(None)
    if acc is not None:
        task_label = _task_label_ctx.get(None)
        acc.record(model, prompt_tokens, completion_tokens, cost_usd, task_label)
