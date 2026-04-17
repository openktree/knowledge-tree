"""Decorator that wraps Hatchet tasks in an ``ExpenseContext``.

Every Hatchet task that can invoke an LLM (directly or via a
LangGraph agent) is registered with :func:`tracked_task`. The
decorator constructs an :class:`~kt_models.expense.ExpenseContext`
from the task's input Pydantic model, sets it on the ContextVar for
the duration of the body, then resets on exit. Gateway / callback
read the ambient context and forward usage to the sink.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from functools import wraps
from typing import Any, TypeVar

from kt_models.expense import ExpenseContext, reset_current_expense, set_current_expense

TOutput = TypeVar("TOutput")


def tracked_task(
    wf: Any,
    *,
    task_type: str,
    durable: bool = False,
    expense_from_input: Callable[[Any, Any], ExpenseContext] | None = None,
    **hatchet_kwargs: Any,
) -> Callable[[Callable[..., Awaitable[TOutput]]], Any]:
    """Register a Hatchet task that runs inside an ``ExpenseContext``.

    Args:
        wf: The Hatchet workflow object (or ``hatchet`` singleton) to
            register against.
        task_type: The ``ExpenseContext.task_type`` stamped on every LLM
            call made inside this task. Surfaces in the usage dashboard.
        durable: When True, registers via ``wf.durable_task`` so the
            Hatchet SDK uses durable-event semantics (long-running
            workflows, dedicated slot pool, replay safety). Must match
            the original task's decorator — a durable task silently
            demoted to a regular task loses its long-running guarantees.
        expense_from_input: Optional callback ``(input, ctx) ->
            ExpenseContext``. When omitted, a default context is built
            by reading common fields (``conversation_id``,
            ``message_id``, ``user_id``, ``graph_id``) off ``input`` if
            present.
        **hatchet_kwargs: Forwarded to the chosen registrar.

    The return type is declared as ``Any`` because the registrar
    replaces the decorated callable with a Hatchet ``Task`` object —
    callers reference it in ``parents=`` / ``ctx.task_output(...)``
    lists the same way they would a bare ``@wf.task`` function.
    """

    builder = expense_from_input or _default_expense_builder(task_type)
    registrar = wf.durable_task if durable else wf.task

    def decorator(fn: Callable[..., Awaitable[TOutput]]) -> Any:
        @registrar(**hatchet_kwargs)
        @wraps(fn)
        async def _entry(input: Any, ctx: Any) -> TOutput:
            expense = builder(input, ctx)
            token = set_current_expense(expense)
            try:
                return await fn(input, ctx)
            finally:
                reset_current_expense(token)

        return _entry

    return decorator


def _default_expense_builder(task_type: str) -> Callable[[Any, Any], ExpenseContext]:
    """Build an ExpenseContext from an input Pydantic model by best-effort.

    Reads ``conversation_id``, ``message_id``, ``user_id``, ``graph_id``,
    ``workflow_run_id`` from ``input`` if present. Always stamps
    ``task_type`` — which is the one field the dashboard groups by.
    """

    def build(input: Any, ctx: Any) -> ExpenseContext:
        return ExpenseContext(
            task_type=task_type,
            conversation_id=_str_or_none(getattr(input, "conversation_id", None)),
            message_id=_str_or_none(getattr(input, "message_id", None)),
            user_id=_str_or_none(getattr(input, "user_id", None)),
            graph_id=_str_or_none(getattr(input, "graph_id", None)),
            workflow_run_id=_str_or_none(getattr(ctx, "workflow_run_id", None)),
        )

    return build


def _str_or_none(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)
