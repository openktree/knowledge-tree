"""Tests for ExpenseContext and ambient-scope helpers."""

from __future__ import annotations

import pytest

from kt_models.expense import (
    ExpenseContext,
    expense_scope,
    expense_subtask,
    get_current_expense,
    require_current_expense,
    reset_current_expense,
    set_current_expense,
)


def test_expense_context_requires_task_type() -> None:
    ec = ExpenseContext(task_type="synthesis")
    assert ec.task_type == "synthesis"
    assert ec.conversation_id is None
    assert ec.message_id is None


def test_expense_context_child_overrides_fields() -> None:
    parent = ExpenseContext(task_type="synthesis", conversation_id="c1")
    child = parent.child(task_type="synthesis_tool", message_id="m1")
    assert child.task_type == "synthesis_tool"
    assert child.conversation_id == "c1"
    assert child.message_id == "m1"


def test_expense_context_frozen() -> None:
    ec = ExpenseContext(task_type="x")
    with pytest.raises(Exception):
        ec.task_type = "y"  # type: ignore[misc]


def test_require_current_expense_raises_when_unset() -> None:
    # Override the autouse fixture's ambient context so we can observe None.
    token = set_current_expense(None)
    try:
        assert get_current_expense() is None
        with pytest.raises(RuntimeError, match="outside a tracked expense context"):
            require_current_expense()
    finally:
        reset_current_expense(token)


def test_require_current_expense_returns_ambient() -> None:
    ambient = ExpenseContext(task_type="ambient")
    token = set_current_expense(ambient)
    try:
        assert require_current_expense() is ambient
    finally:
        reset_current_expense(token)


def test_expense_scope_context_manager() -> None:
    outer = set_current_expense(None)
    try:
        assert get_current_expense() is None
        with expense_scope(ExpenseContext(task_type="scoped")) as ec:
            assert get_current_expense() is ec
            assert ec.task_type == "scoped"
        assert get_current_expense() is None
    finally:
        reset_current_expense(outer)


def test_expense_subtask_derives_child() -> None:
    with expense_scope(ExpenseContext(task_type="parent", conversation_id="c1")):
        with expense_subtask("sub") as child:
            assert child.task_type == "sub"
            assert child.conversation_id == "c1"
            assert get_current_expense() is child
        # Parent restored
        assert require_current_expense().task_type == "parent"


def test_expense_subtask_requires_ambient() -> None:
    token = set_current_expense(None)
    try:
        with pytest.raises(RuntimeError):
            with expense_subtask("orphan"):
                pass
    finally:
        reset_current_expense(token)


def test_reset_restores_previous() -> None:
    start = set_current_expense(None)
    try:
        outer = ExpenseContext(task_type="outer")
        inner = ExpenseContext(task_type="inner")
        t1 = set_current_expense(outer)
        try:
            t2 = set_current_expense(inner)
            try:
                assert get_current_expense() is inner
            finally:
                reset_current_expense(t2)
            assert get_current_expense() is outer
        finally:
            reset_current_expense(t1)
        assert get_current_expense() is None
    finally:
        reset_current_expense(start)
