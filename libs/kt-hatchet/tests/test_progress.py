"""Unit tests for kt_hatchet.progress mapping functions."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from kt_hatchet.progress import map_run_error, map_run_status, map_task_summary


def _make_status(value: str) -> SimpleNamespace:
    """Create a mock V1TaskStatus enum member."""
    return SimpleNamespace(value=value)


def _make_task(
    *,
    task_external_id: str = "abc-123",
    display_name: str = "create_node",
    status: str = "RUNNING",
    duration: int | None = None,
    started_at: object | None = None,
    children: list | None = None,
) -> SimpleNamespace:
    """Create a mock V1TaskSummary."""
    return SimpleNamespace(
        task_external_id=task_external_id,
        display_name=display_name,
        status=_make_status(status),
        duration=duration,
        started_at=started_at,
        children=children,
    )


def _make_details(
    run_status: str = "RUNNING",
    error_message: str | None = None,
    tasks: list | None = None,
) -> SimpleNamespace:
    """Create a mock V1WorkflowRunDetails."""
    return SimpleNamespace(
        run=SimpleNamespace(
            status=_make_status(run_status),
            error_message=error_message,
        ),
        tasks=tasks or [],
    )


# ---------------------------------------------------------------------------
# map_task_summary
# ---------------------------------------------------------------------------


class TestMapTaskSummary:
    def test_basic_running_task(self) -> None:
        task = _make_task(status="RUNNING", display_name="dimensions")
        result = map_task_summary(task)
        assert result["task_id"] == "abc-123"
        assert result["display_name"] == "dimensions"
        assert result["status"] == "RUNNING"
        assert result["duration_ms"] is None
        assert result["started_at"] is None
        assert result["children"] == []

    def test_completed_maps_to_succeeded(self) -> None:
        task = _make_task(status="COMPLETED", duration=1500)
        result = map_task_summary(task)
        assert result["status"] == "SUCCEEDED"
        assert result["duration_ms"] == 1500

    def test_failed_status_preserved(self) -> None:
        task = _make_task(status="FAILED")
        result = map_task_summary(task)
        assert result["status"] == "FAILED"

    def test_queued_status_preserved(self) -> None:
        task = _make_task(status="QUEUED")
        result = map_task_summary(task)
        assert result["status"] == "QUEUED"

    def test_cancelled_status_preserved(self) -> None:
        task = _make_task(status="CANCELLED")
        result = map_task_summary(task)
        assert result["status"] == "CANCELLED"

    def test_started_at_formatted(self) -> None:
        from datetime import datetime, timezone

        dt = datetime(2026, 3, 27, 12, 0, 0, tzinfo=timezone.utc)
        task = _make_task(started_at=dt)
        result = map_task_summary(task)
        assert result["started_at"] == "2026-03-27T12:00:00+00:00"

    def test_children_recursively_mapped(self) -> None:
        child = _make_task(
            task_external_id="child-1",
            display_name="child_task",
            status="COMPLETED",
            duration=500,
        )
        parent = _make_task(
            task_external_id="parent-1",
            display_name="parent_task",
            status="RUNNING",
            children=[child],
        )
        result = map_task_summary(parent)
        assert len(result["children"]) == 1
        assert result["children"][0]["task_id"] == "child-1"
        assert result["children"][0]["status"] == "SUCCEEDED"

    def test_wave_number_and_node_type_default_none(self) -> None:
        task = _make_task()
        result = map_task_summary(task)
        assert result["wave_number"] is None
        assert result["node_type"] is None


# ---------------------------------------------------------------------------
# map_run_status
# ---------------------------------------------------------------------------


class TestMapRunStatus:
    @pytest.mark.parametrize(
        ("hatchet_status", "expected"),
        [
            ("QUEUED", "pending"),
            ("RUNNING", "running"),
            ("COMPLETED", "completed"),
            ("FAILED", "failed"),
            ("CANCELLED", "failed"),
        ],
    )
    def test_status_mapping(self, hatchet_status: str, expected: str) -> None:
        details = _make_details(run_status=hatchet_status)
        assert map_run_status(details) == expected


# ---------------------------------------------------------------------------
# map_run_error
# ---------------------------------------------------------------------------


class TestMapRunError:
    def test_no_error(self) -> None:
        details = _make_details(error_message=None)
        assert map_run_error(details) is None

    def test_with_error(self) -> None:
        details = _make_details(error_message="task timed out")
        assert map_run_error(details) == "task timed out"
