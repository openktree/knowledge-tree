"""Auto-build endpoint — promote seeds and enrich nodes.

POST /graph-builder/auto-build — Dispatch auto_build_graph for default graph
POST /graphs/{graph_slug}/graph-builder/auto-build — Dispatch for specific graph
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends

from kt_api.auth.permissions import require_graph_permission
from kt_api.graph_context import GraphContext
from kt_rbac import Permission

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["graph-builder"])


@router.post("/graph-builder/auto-build")
async def auto_build_graph() -> dict[str, str]:
    """Dispatch the auto-build graph task on the default graph.

    Promotes eligible seeds to stub nodes, creates co-occurrence edges,
    and dispatches enrichment for nodes with enough facts.
    """
    from kt_api.dispatch import dispatch_with_graph

    run_id = await dispatch_with_graph("auto_build_graph", {})
    return {"status": "started", "workflow_run_id": run_id}


@router.post("/graphs/{graph_slug}/graph-builder/auto-build")
async def auto_build_graph_scoped(
    ctx: GraphContext = Depends(require_graph_permission(Permission.GRAPH_WRITE)),
) -> dict[str, str]:
    """Dispatch auto-build on a specific graph."""
    from kt_api.dispatch import dispatch_with_graph

    run_id = await dispatch_with_graph(
        "auto_build_graph", {}, graph_id=str(ctx.graph.id)
    )
    return {"status": "started", "workflow_run_id": run_id, "graph": ctx.graph.slug}
