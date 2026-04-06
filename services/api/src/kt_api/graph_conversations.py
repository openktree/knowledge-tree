"""Graph-scoped conversation endpoints.

Mirrors /api/v1/conversations scoped to a specific graph via
/api/v1/graphs/{graph_slug}/conversations.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query

from kt_api.graph_context import GraphContext, get_graph_context
from kt_api.schemas import (
    ConversationListItem,
    ConversationMessageResponse,
    ConversationResponse,
    PaginatedConversationsResponse,
    SubgraphResponse,
)
from kt_db.repositories.conversations import ConversationRepository

router = APIRouter(prefix="/api/v1/graphs/{graph_slug}/conversations", tags=["graph-conversations"])


@router.get("", response_model=PaginatedConversationsResponse)
async def list_graph_conversations(
    ctx: GraphContext = Depends(get_graph_context),
    mode: str | None = Query(None, description="Filter by conversation mode"),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
) -> PaginatedConversationsResponse:
    """List conversations in a specific graph."""
    async with ctx.graph_session_factory() as session:
        repo = ConversationRepository(session)
        rows = await repo.list_with_stats(limit=limit, offset=offset, mode=mode)
        total = await repo.count(mode=mode)

        items = []
        for row in rows:
            conv = row["conversation"]
            items.append(
                ConversationListItem(
                    id=str(conv.id),
                    title=conv.title,
                    mode=conv.mode,
                    message_count=row["message_count"],
                    latest_status=row["latest_status"],
                    created_at=conv.created_at,
                    updated_at=conv.updated_at,
                )
            )

        return PaginatedConversationsResponse(
            items=items,
            total=total,
            offset=offset,
            limit=limit,
        )


@router.get("/{conversation_id}", response_model=ConversationResponse)
async def get_graph_conversation(
    conversation_id: str,
    ctx: GraphContext = Depends(get_graph_context),
) -> ConversationResponse:
    """Get a single conversation from a specific graph."""
    try:
        conv_uuid = uuid.UUID(conversation_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid conversation ID")

    async with ctx.graph_session_factory() as session:
        repo = ConversationRepository(session)
        conv = await repo.get_with_messages(conv_uuid)
        if conv is None:
            raise HTTPException(status_code=404, detail="Conversation not found")

        messages = [
            ConversationMessageResponse(
                id=str(msg.id),
                turn_number=msg.turn_number,
                role=msg.role,
                content=msg.content,
                nav_budget=msg.nav_budget,
                explore_budget=msg.explore_budget,
                nav_used=msg.nav_used,
                explore_used=msg.explore_used,
                visited_nodes=msg.visited_nodes,
                created_nodes=msg.created_nodes,
                created_edges=msg.created_edges,
                subgraph=SubgraphResponse(**msg.subgraph) if msg.subgraph else None,
                status=msg.status,
                error=msg.error,
                workflow_run_id=getattr(msg, "workflow_run_id", None),
                created_at=msg.created_at,
            )
            for msg in conv.messages
        ]

        return ConversationResponse(
            id=str(conv.id),
            title=conv.title,
            mode=conv.mode,
            messages=messages,
            created_at=conv.created_at,
            updated_at=conv.updated_at,
        )
