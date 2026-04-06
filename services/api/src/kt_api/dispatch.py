"""Workflow dispatch helpers with optional graph_id injection."""

from __future__ import annotations

from typing import Any


async def dispatch_with_graph(
    workflow_name: str,
    input_dict: dict[str, Any] | Any,
    *,
    graph_id: str | None = None,
    additional_metadata: dict[str, Any] | None = None,
) -> str:
    """Dispatch a Hatchet workflow, injecting graph_id when non-None.

    When ``graph_id`` is None the input dict passes through unchanged
    (default-graph behaviour).  Graph-scoped endpoint wrappers supply
    ``graph_id=str(ctx.graph.id)`` to route workflows to the correct
    per-graph session factories.
    """
    from kt_hatchet.client import dispatch_workflow

    if graph_id is not None:
        input_dict = {**input_dict, "graph_id": graph_id}
    return await dispatch_workflow(workflow_name, input_dict, additional_metadata=additional_metadata)
