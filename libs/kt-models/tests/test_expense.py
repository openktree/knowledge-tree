"""Tests for ExpenseContext and the resolution logic."""

from __future__ import annotations

from kt_models.expense import (
    ExpenseContext,
    get_current_expense,
    reset_current_expense,
    resolve_expense,
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
    assert child.conversation_id == "c1"  # inherited
    assert child.message_id == "m1"


def test_expense_context_frozen() -> None:
    ec = ExpenseContext(task_type="x")
    try:
        ec.task_type = "y"  # type: ignore[misc]
    except Exception:
        return
    raise AssertionError("ExpenseContext should be frozen")


def test_resolve_expense_prefers_explicit_over_ambient() -> None:
    ambient = ExpenseContext(task_type="ambient")
    explicit = ExpenseContext(task_type="explicit")
    token = set_current_expense(ambient)
    try:
        assert resolve_expense(explicit) is explicit
    finally:
        reset_current_expense(token)


def test_resolve_expense_falls_back_to_ambient() -> None:
    ambient = ExpenseContext(task_type="ambient")
    token = set_current_expense(ambient)
    try:
        assert resolve_expense(None) is ambient
    finally:
        reset_current_expense(token)


def test_resolve_expense_unknown_when_nothing_set() -> None:
    assert get_current_expense() is None
    result = resolve_expense(None)
    assert result.task_type == "unknown"


def test_reset_restores_previous() -> None:
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
