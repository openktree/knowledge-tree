"""Auto-build endpoint — promote seeds and enrich nodes.

POST /graph-builder/auto-build — Dispatch auto_build_graph task
"""

from __future__ import annotations

import logging

from fastapi import APIRouter

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["graph-builder"])


@router.post("/graph-builder/auto-build")
async def auto_build_graph() -> dict[str, str]:
    """Dispatch the auto-build graph task.

    Promotes eligible seeds to stub nodes, creates co-occurrence edges,
    and dispatches enrichment for nodes with enough facts.
    """
    from kt_hatchet.client import dispatch_workflow

    run_id = await dispatch_workflow("auto_build_task", {})
    return {"status": "started", "workflow_run_id": run_id}
