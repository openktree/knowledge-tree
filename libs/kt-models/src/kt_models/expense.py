"""Typed metadata carrier for LLM expense tracking.

Every LLM call in the system is scoped by an :class:`ExpenseContext`.
``task_type`` is the only strictly required field and is always known at
the call site. Other IDs are optional per flow (synthesis flows may lack
a conversation_id; node pipelines always carry one).

``ExpenseContext`` is plumbed via three mechanisms:

1. Hatchet task layer — ``kt_hatchet.tracked_task.TrackedWorkflowTask``
   subclasses construct the context from task input and set a ContextVar
   for the duration of ``run()``.
2. Agent layer — ``kt_agents_core.state.AgentContext`` carries it as a
   required field so agents cannot be constructed without one.
3. Gateway layer — ``ModelGateway`` methods accept ``expense`` as a
   kw-only argument. When not passed, the gateway falls back to the
   ContextVar. If both are absent, the call is tagged ``task_type=
   "unknown"`` so the gap is visible in the dashboard rather than lost.
"""

from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass, replace
from typing import Any

_UNKNOWN_TASK_TYPE = "unknown"


@dataclass(frozen=True, kw_only=True, slots=True)
class ExpenseContext:
    """Metadata scoping every LLM call to its originating work unit."""

    task_type: str
    conversation_id: str | None = None
    message_id: str | None = None
    workflow_run_id: str | None = None
    user_id: str | None = None
    graph_id: str | None = None
    synthesis_id: str | None = None

    def child(self, **overrides: Any) -> "ExpenseContext":
        """Derive a sub-context with selected fields overridden."""
        return replace(self, **overrides)

    @classmethod
    def unknown(cls) -> "ExpenseContext":
        """Fallback context for calls outside a tracked scope."""
        return cls(task_type=_UNKNOWN_TASK_TYPE)


_current_expense: ContextVar[ExpenseContext | None] = ContextVar("kt_expense_context", default=None)


def set_current_expense(expense: ExpenseContext | None) -> object:
    """Set the ambient ExpenseContext. Returns a token for ``reset_current_expense``."""
    return _current_expense.set(expense)


def reset_current_expense(token: Any) -> None:
    """Reset the ambient ExpenseContext to its previous value."""
    _current_expense.reset(token)


def get_current_expense() -> ExpenseContext | None:
    """Return the ambient ExpenseContext, or None if no scope is active."""
    return _current_expense.get()


def resolve_expense(explicit: ExpenseContext | None) -> ExpenseContext:
    """Resolve the effective expense for an LLM call.

    Precedence: explicit argument → ContextVar → ``ExpenseContext.unknown()``.
    Gateway methods call this to decide how to tag a usage record.
    """
    if explicit is not None:
        return explicit
    ambient = _current_expense.get()
    if ambient is not None:
        return ambient
    return ExpenseContext.unknown()
