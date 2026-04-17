"""Tests for the ``tracked_task`` decorator.

We don't spin up a real Hatchet worker — a stub workflow is enough to
prove that the decorator sets ``ExpenseContext`` on the ContextVar for
the duration of ``run()`` and reads common IDs off the input.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from kt_hatchet.tracked_task import TrackedWorkflowTask, tracked_task
from kt_models.expense import ExpenseContext, get_current_expense


class _StubWorkflow:
    """Mimics the subset of the Hatchet workflow API we use."""

    def __init__(self) -> None:
        self.registered: list[dict[str, Any]] = []

    def task(self, **hatchet_kwargs: Any) -> Any:
        def decorator(fn: Any) -> Any:
            self.registered.append({"fn": fn, "kwargs": hatchet_kwargs})
            return fn

        return decorator


@dataclass
class _FakeInput:
    conversation_id: str = "conv-1"
    message_id: str = "msg-1"
    user_id: str = "user-1"
    graph_id: str | None = None


@dataclass
class _FakeCtx:
    workflow_run_id: str = "run-1"


async def test_tracked_task_sets_expense_from_input() -> None:
    wf = _StubWorkflow()
    captured: dict[str, ExpenseContext | None] = {}

    @tracked_task(wf, task_type="my_task", execution_timeout="5m")
    async def handler(input: _FakeInput, ctx: _FakeCtx) -> str:
        captured["expense"] = get_current_expense()
        return "ok"

    assert len(wf.registered) == 1
    assert wf.registered[0]["kwargs"] == {"execution_timeout": "5m"}

    result = await handler(_FakeInput(), _FakeCtx())
    assert result == "ok"

    expense = captured["expense"]
    assert expense is not None
    assert expense.task_type == "my_task"
    assert expense.conversation_id == "conv-1"
    assert expense.message_id == "msg-1"
    assert expense.user_id == "user-1"
    assert expense.workflow_run_id == "run-1"


async def test_tracked_task_resets_contextvar_after_run() -> None:
    wf = _StubWorkflow()

    @tracked_task(wf, task_type="t")
    async def handler(input: _FakeInput, ctx: _FakeCtx) -> None:
        assert get_current_expense() is not None

    assert get_current_expense() is None
    await handler(_FakeInput(), _FakeCtx())
    assert get_current_expense() is None


async def test_tracked_task_custom_expense_builder() -> None:
    wf = _StubWorkflow()
    captured: dict[str, ExpenseContext | None] = {}

    def build(input: _FakeInput, ctx: _FakeCtx) -> ExpenseContext:
        return ExpenseContext(
            task_type="override",
            synthesis_id=input.message_id,
        )

    @tracked_task(wf, task_type="ignored", expense_from_input=build)
    async def handler(input: _FakeInput, ctx: _FakeCtx) -> None:
        captured["expense"] = get_current_expense()

    await handler(_FakeInput(), _FakeCtx())
    expense = captured["expense"]
    assert expense is not None
    assert expense.task_type == "override"
    assert expense.synthesis_id == "msg-1"


async def test_tracked_workflow_task_subclass() -> None:
    wf = _StubWorkflow()
    captured: dict[str, ExpenseContext | None] = {}

    class MyTask(TrackedWorkflowTask[_FakeInput, str]):
        task_type = "subclass_task"

        async def run(self, input: _FakeInput, ctx: Any, expense: ExpenseContext) -> str:
            captured["expense"] = get_current_expense()
            return expense.task_type

    MyTask.register(wf)

    fn = wf.registered[0]["fn"]
    out = await fn(_FakeInput(), _FakeCtx())
    assert out == "subclass_task"
    expense = captured["expense"]
    assert expense is not None
    assert expense.task_type == "subclass_task"


def test_subclass_must_declare_task_type() -> None:
    with pytest.raises(TypeError, match="task_type"):

        class Bad(TrackedWorkflowTask[_FakeInput, str]):
            async def run(self, input: _FakeInput, ctx: Any, expense: ExpenseContext) -> str:
                return "nope"
