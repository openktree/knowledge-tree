"""Edge candidate resolution pipeline.

Thin wrapper around EdgeResolver adding batch support for the
phased build pipeline.
"""

from __future__ import annotations

import logging
from typing import Any

from kt_agents_core.state import AgentContext
from kt_worker_nodes.pipelines.edges.resolver import EdgeResolver
from kt_worker_nodes.pipelines.models import CreateNodeTask

logger = logging.getLogger(__name__)


class EdgePipeline:
    """Edge resolution pipeline for single-node and batch operations."""

    def __init__(self, ctx: AgentContext) -> None:
        self._ctx = ctx
        self._resolver = EdgeResolver(ctx)

    async def resolve_from_candidates(self, node: Any) -> dict[str, Any]:
        """Resolve edges from pending candidates for this node."""
        return await self._resolver.resolve_from_candidates(node)

    async def resolve_from_candidates_batch(
        self,
        tasks: list[CreateNodeTask],
        state: Any,
    ) -> dict[str, Any]:
        """Batch: sequential per node, aggregate metrics.

        Filters to tasks that have a node and are not skipped/errored.
        Returns aggregated metrics.
        """
        edge_tasks = [t for t in tasks if t.node is not None and t.action not in ("skip", "error")]
        if not edge_tasks:
            return {"edges_created": 0, "edge_ids": [], "nodes": []}

        total_created = 0
        all_edge_ids: list[str] = []
        node_details: list[dict[str, Any]] = []

        for t in edge_tasks:
            try:
                result = await self._resolver.resolve_from_candidates(t.node)
                created = result.get("edges_created", 0)
                edge_ids = result.get("edge_ids", [])
                t.edges_created = created
                t.result = result
                state.created_edges.extend(edge_ids)
                total_created += created
                all_edge_ids.extend(edge_ids)
                if len(node_details) < 10:
                    node_details.append(
                        {
                            "name": t.name,
                            "edges_created": created,
                        }
                    )
            except Exception:
                logger.debug(
                    "resolve_from_candidates_batch: error processing '%s'",
                    t.name,
                    exc_info=True,
                )

        try:
            await self._ctx.graph_engine.commit()
        except Exception:
            logger.exception("Error committing edge candidate batch")
            try:
                await self._ctx.graph_engine._write_session.rollback()
            except Exception:
                logger.debug("Rollback failed", exc_info=True)

        return {
            "edges_created": total_created,
            "edge_ids": all_edge_ids,
            "nodes": node_details,
        }
