"""Map Hatchet SDK workflow run details to progress dicts.

These functions transform Hatchet's native ``V1WorkflowRunDetails`` and
``V1TaskSummary`` objects into plain dicts that the API layer converts
to Pydantic response models (``PipelineTaskItem``, ``ProgressResponse``).

This module lives in ``kt-hatchet`` (not in the API service) because it
needs knowledge of Hatchet SDK types, but returns plain dicts so that
libs never import from services.
"""

from __future__ import annotations

from typing import Any


def map_task_summary(task: Any) -> dict[str, Any]:
    """Map a Hatchet ``V1TaskSummary`` to a ``PipelineTaskItem``-shaped dict.

    The dict keys match the ``PipelineTaskItem`` Pydantic model fields.
    """
    status_str: str = task.status.value  # e.g. "COMPLETED", "RUNNING"
    if status_str == "COMPLETED":
        status_str = "SUCCEEDED"  # Frontend expects "SUCCEEDED"

    started_at: str | None = None
    if task.started_at is not None:
        started_at = task.started_at.isoformat()

    children: list[dict[str, Any]] = []
    if task.children:
        children = [map_task_summary(c) for c in task.children]

    return {
        "task_id": task.task_external_id,
        "display_name": task.display_name,
        "status": status_str,
        "duration_ms": task.duration,
        "started_at": started_at,
        "children": children,
        "wave_number": None,
        "node_type": None,
    }


def map_run_status(details: Any) -> str:
    """Map ``V1WorkflowRunDetails.run.status`` to a progress status string.

    Returns one of: ``"pending"``, ``"running"``, ``"completed"``, ``"failed"``.
    """
    status_value: str = details.run.status.value
    if status_value == "QUEUED":
        return "pending"
    if status_value == "RUNNING":
        return "running"
    if status_value == "COMPLETED":
        return "completed"
    # FAILED, CANCELLED
    return "failed"


def map_run_error(details: Any) -> str | None:
    """Extract error message from a workflow run, if any."""
    return details.run.error_message
