"""Graph-scoped node read endpoints.

Mirrors /api/v1/nodes but scoped to a specific graph via
/api/v1/graphs/{graph_slug}/nodes. Uses GraphContext for session routing.
"""

from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select

from kt_api.graph_context import GraphContext, get_graph_context
from kt_api.schemas import NodeResponse, PaginatedNodesResponse
from kt_db.models import Node

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/graphs/{graph_slug}/nodes", tags=["graph-nodes"])


@router.get("", response_model=PaginatedNodesResponse)
async def list_graph_nodes(
    ctx: GraphContext = Depends(get_graph_context),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    node_type: str | None = Query(default=None),
    search: str | None = Query(default=None),
) -> PaginatedNodesResponse:
    """List nodes in a specific graph."""
    async with ctx.graph_session_factory() as session:
        stmt = select(Node)
        count_stmt = select(func.count(Node.id))

        if node_type:
            stmt = stmt.where(Node.node_type == node_type)
            count_stmt = count_stmt.where(Node.node_type == node_type)

        if search:
            # Escape ILIKE special characters to prevent pattern injection
            escaped = search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            stmt = stmt.where(Node.concept.ilike(f"%{escaped}%"))
            count_stmt = count_stmt.where(Node.concept.ilike(f"%{escaped}%"))

        total = (await session.execute(count_stmt)).scalar_one()

        stmt = stmt.order_by(Node.created_at.desc()).limit(limit).offset(offset)
        result = await session.execute(stmt)
        nodes = result.scalars().all()

        return PaginatedNodesResponse(
            nodes=[
                NodeResponse(
                    id=str(n.id),
                    concept=n.concept,
                    node_type=n.node_type,
                    entity_subtype=n.entity_subtype,
                    definition=n.definition,
                    parent_id=str(n.parent_id) if n.parent_id else None,
                    fact_count=n.fact_count,
                    edge_count=n.edge_count,
                    child_count=n.child_count,
                    dimension_count=n.dimension_count,
                    convergence_score=n.convergence_score,
                    created_at=n.created_at,
                    updated_at=n.updated_at,
                    visibility=n.visibility,
                )
                for n in nodes
            ],
            total=total,
            limit=limit,
            offset=offset,
        )


@router.get("/{node_id}", response_model=NodeResponse)
async def get_graph_node(
    node_id: str,
    ctx: GraphContext = Depends(get_graph_context),
) -> NodeResponse:
    """Get a single node from a specific graph."""
    try:
        nid = uuid.UUID(node_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid node ID")

    async with ctx.graph_session_factory() as session:
        result = await session.execute(select(Node).where(Node.id == nid))
        node = result.scalar_one_or_none()
        if node is None:
            raise HTTPException(status_code=404, detail="Node not found")

        return NodeResponse(
            id=str(node.id),
            concept=node.concept,
            node_type=node.node_type,
            entity_subtype=node.entity_subtype,
            definition=node.definition,
            parent_id=str(node.parent_id) if node.parent_id else None,
            fact_count=node.fact_count,
            edge_count=node.edge_count,
            child_count=node.child_count,
            dimension_count=node.dimension_count,
            convergence_score=node.convergence_score,
            created_at=node.created_at,
            updated_at=node.updated_at,
            visibility=node.visibility,
        )
