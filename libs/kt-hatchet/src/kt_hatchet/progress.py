"""Map Hatchet SDK workflow run details to progress dicts.

These functions transform Hatchet's native ``V1WorkflowRunDetails`` and
``V1TaskSummary`` objects into plain dicts that the API layer converts
to Pydantic response models (``PipelineTaskItem``, ``ProgressResponse``).

This module lives in ``kt-hatchet`` (not in the API service) because it
needs knowledge of Hatchet SDK types, but returns plain dicts so that
libs never import from services.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any


def map_task_summary(task: Any, *, has_children: bool = False) -> dict[str, Any]:
    """Map a Hatchet ``V1TaskSummary`` to a ``PipelineTaskItem``-shaped dict.

    ``has_children`` is set by callers that have probed for spawned child
    workflow runs separately (``V1TaskSummary.children`` is typically empty
    from ``aio_get``; spawned child workflow runs must be discovered via
    ``runs.aio_list(parent_task_external_id=...)``).
    """
    status_str: str = task.status.value  # e.g. "COMPLETED", "RUNNING"
    if status_str == "COMPLETED":
        status_str = "SUCCEEDED"  # Frontend expects "SUCCEEDED"

    started_at: str | None = None
    if task.started_at is not None:
        started_at = task.started_at.isoformat()

    children: list[dict[str, Any]] = []
    if getattr(task, "children", None):
        children = [map_task_summary(c) for c in task.children]

    return {
        "task_id": task.task_external_id,
        "display_name": task.display_name,
        "status": status_str,
        "duration_ms": task.duration,
        "started_at": started_at,
        "has_children": has_children or bool(children),
        "children": children,
        "wave_number": None,
        "node_type": None,
    }


async def annotate_has_children(items: list[dict[str, Any]], *, since: datetime | None = None) -> list[dict[str, Any]]:
    """Probe each item's ``task_id`` for spawned child workflow runs in parallel.

    Mutates ``has_children`` in place and returns the same list.
    """
    from kt_hatchet.client import has_child_runs

    async def _probe(item: dict[str, Any]) -> None:
        if item.get("has_children"):
            return
        item["has_children"] = await has_child_runs(item["task_id"], since=since)

    if items:
        await asyncio.gather(*(_probe(i) for i in items))
    return items


async def fetch_child_task_items(parent_task_id: str, *, since: datetime | None = None) -> list[dict[str, Any]]:
    """Fetch direct children of a task as ``PipelineTaskItem``-shaped dicts.

    Lists spawned child workflow runs via ``list_child_runs``, then for each
    calls ``aio_get`` to pull its tasks, mapping each to a dict. Populates
    ``has_children`` on every returned item via a parallel probe.
    """
    from kt_hatchet.client import get_workflow_run_details, list_child_runs

    runs = await list_child_runs(parent_task_id, since=since)

    async def _fetch_run(run: Any) -> list[dict[str, Any]]:
        run_id = run.metadata.id
        try:
            details = await get_workflow_run_details(run_id)
        except Exception:
            return []
        tasks = getattr(details, "tasks", []) or []
        return [map_task_summary(t) for t in tasks]

    nested = await asyncio.gather(*(_fetch_run(r) for r in runs))
    items: list[dict[str, Any]] = [t for group in nested for t in group]
    await annotate_has_children(items, since=since)
    return items


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
