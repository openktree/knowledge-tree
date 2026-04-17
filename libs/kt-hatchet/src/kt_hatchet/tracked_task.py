"""Abstract base for Hatchet tasks that record LLM expense.

Every Hatchet task that invokes an LLM (directly or via a LangGraph
agent) should be wrapped with :class:`TrackedWorkflowTask` — or more
commonly, with the :func:`tracked_task` decorator. These entry points
set a process-wide :class:`~kt_models.expense.ExpenseContext` for the
duration of ``run()`` so the gateway's ``UsageSink`` can tag every LLM
call with the originating conversation / message / workflow IDs.

Two equivalent APIs are exposed:

``@tracked_task``
    Lightweight: takes the Hatchet workflow + an ``expense_from_input``
    callable. Useful for one-off tasks.

:class:`TrackedWorkflowTask`
    Subclass when a task needs shared helper methods or inheritance.
    Forces subclasses to declare ``task_type`` and implement
    ``expense_from_input`` via the ABC machinery.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from functools import wraps
from typing import Any, ClassVar, Generic, TypeVar

from kt_models.expense import ExpenseContext, reset_current_expense, set_current_expense

TInput = TypeVar("TInput")
TOutput = TypeVar("TOutput")


def tracked_task(
    wf: Any,
    *,
    task_type: str,
    expense_from_input: Callable[[Any, Any], ExpenseContext] | None = None,
    **hatchet_kwargs: Any,
) -> Callable[[Callable[..., Awaitable[TOutput]]], Any]:
    """Register a Hatchet task that runs inside an ``ExpenseContext``.

    Args:
        wf: The Hatchet workflow object (or ``hatchet`` singleton) to
            register against.
        task_type: The ``ExpenseContext.task_type`` stamped on every LLM
            call made inside this task. Surfaces in the usage dashboard.
        expense_from_input: Optional callback ``(input, ctx) ->
            ExpenseContext``. When omitted, a default context is built
            by reading common fields (``conversation_id``,
            ``message_id``, ``user_id``, ``graph_id``) off ``input`` if
            present.
        **hatchet_kwargs: Forwarded to ``wf.task(...)``.

    The return type is declared as ``Any`` because ``wf.task`` replaces
    the decorated callable with a Hatchet ``Task`` object — callers
    reference it in ``parents=`` / ``ctx.task_output(...)`` lists the
    same way they would reference a bare ``@wf.task`` function.
    """

    builder = expense_from_input or _default_expense_builder(task_type)

    def decorator(fn: Callable[..., Awaitable[TOutput]]) -> Any:
        @wf.task(**hatchet_kwargs)
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


class TrackedWorkflowTask(ABC, Generic[TInput, TOutput]):
    """Class-based alternative to :func:`tracked_task`.

    Subclasses declare ``task_type`` and implement
    :meth:`expense_from_input` + :meth:`run`. :meth:`register` wires the
    instance into a Hatchet workflow and ensures ``run()`` executes
    inside the right :class:`ExpenseContext`.
    """

    task_type: ClassVar[str]

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        if not getattr(cls, "__abstractmethods__", False) and "task_type" not in cls.__dict__:
            raise TypeError(f"{cls.__name__} must declare a 'task_type' ClassVar")

    def expense_from_input(self, input: TInput, ctx: Any) -> ExpenseContext:
        """Default: derive from common input fields. Override for custom mapping."""
        return _default_expense_builder(self.task_type)(input, ctx)

    @abstractmethod
    async def run(self, input: TInput, ctx: Any, expense: ExpenseContext) -> TOutput:
        """Task body. ``expense`` is already active on the ContextVar."""

    @classmethod
    def register(cls, wf: Any, **hatchet_kwargs: Any) -> Any:
        instance = cls()

        @wf.task(**hatchet_kwargs)
        async def _entry(input: TInput, ctx: Any) -> TOutput:
            expense = instance.expense_from_input(input, ctx)
            token = set_current_expense(expense)
            try:
                return await instance.run(input, ctx, expense)
            finally:
                reset_current_expense(token)

        return _entry
