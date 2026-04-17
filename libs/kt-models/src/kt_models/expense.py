"""Typed metadata carrier for LLM expense tracking.

Every LLM call is scoped by an :class:`ExpenseContext` active on the
``_current_expense`` ContextVar. ``task_type`` is required; everything
else is optional flow-specific metadata.

Plumbing is **ContextVar-only** — there is no ``expense=`` kwarg on the
public gateway API. The decorator layer
(``kt_hatchet.tracked_task.tracked_task``) sets the context from task
input; the gateway reads it inside its recorder via
:func:`require_current_expense`, which raises if nothing is active.

Fail-fast by design. Writing a usage row with ``task_type="unknown"``
is the same shape as the shell_candidates incident: it looks green in
the dashboard and hides a whole category of untracked calls. Better to
crash loudly during a non-production run and force the missing
``@tracked_task`` / :func:`expense_scope` wrapping to be added.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, replace
from typing import Any


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


def require_current_expense() -> ExpenseContext:
    """Return the ambient ExpenseContext or raise if none is set.

    Gateway recorders call this. Missing context is a programming
    error — wrap the caller in ``@tracked_task`` or open an
    :func:`expense_scope` — not a silent data-quality gap.
    """
    ec = _current_expense.get()
    if ec is None:
        raise RuntimeError(
            "LLM call attempted outside a tracked expense context. "
            "Wrap the caller in @tracked_task, or open an "
            "expense_scope(ExpenseContext(task_type=...)) so the usage "
            "row lands with the right task_type."
        )
    return ec


@contextmanager
def expense_scope(expense: ExpenseContext) -> Iterator[ExpenseContext]:
    """Context manager that activates ``expense`` for its block.

    Use when a call site cannot be wrapped by ``@tracked_task`` — e.g.
    API handlers calling the gateway directly, test fixtures, CLI
    tools.
    """
    token = set_current_expense(expense)
    try:
        yield expense
    finally:
        reset_current_expense(token)


@contextmanager
def expense_subtask(task_type: str, **overrides: Any) -> Iterator[ExpenseContext]:
    """Scope a sub-operation under a more specific ``task_type``.

    Replaces the legacy ``set_usage_task`` / ``clear_usage_task`` pair.
    Requires an ambient :class:`ExpenseContext` — derives a child that
    inherits all IDs but overrides ``task_type`` (and anything else
    passed in ``overrides``) for the block.
    """
    parent = require_current_expense()
    child = parent.child(task_type=task_type, **overrides)
    token = set_current_expense(child)
    try:
        yield child
    finally:
        reset_current_expense(token)
