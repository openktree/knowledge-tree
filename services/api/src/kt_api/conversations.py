"""Conversation CRUD endpoints.

Provides listing and retrieval of conversations, used by the research
history UI to display past bottom-up research sessions.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from kt_api.dependencies import get_db_session
from kt_api.schemas import (
    ConversationListItem,
    ConversationMessageResponse,
    ConversationResponse,
    PaginatedConversationsResponse,
    SubgraphResponse,
)
from kt_db.repositories.conversations import ConversationRepository

router = APIRouter(prefix="/api/v1", tags=["conversations"])


@router.get("/conversations", response_model=PaginatedConversationsResponse)
async def list_conversations(
    mode: str | None = Query(None, description="Filter by conversation mode"),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_db_session),
) -> PaginatedConversationsResponse:
    """List conversations, optionally filtered by mode."""
    repo = ConversationRepository(session)
    conversations = await repo.list_recent(limit=limit, offset=offset, mode=mode)
    total = await repo.count(mode=mode)

    items = []
    for conv in conversations:
        msg_count = await repo.get_message_count(conv.id)
        items.append(
            ConversationListItem(
                id=str(conv.id),
                title=conv.title,
                mode=conv.mode,
                message_count=msg_count,
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


@router.get("/conversations/{conversation_id}", response_model=ConversationResponse)
async def get_conversation(
    conversation_id: str,
    session: AsyncSession = Depends(get_db_session),
) -> ConversationResponse:
    """Get a single conversation with all messages."""
    try:
        conv_uuid = uuid.UUID(conversation_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid conversation ID")

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
