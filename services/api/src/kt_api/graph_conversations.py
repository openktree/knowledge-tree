"""Graph-scoped conversation endpoints.

Mirrors /api/v1/conversations scoped to a specific graph via
/api/v1/graphs/{graph_slug}/conversations.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from kt_api.conversations import _get_conversation_impl, _list_conversations_impl
from kt_api.graph_context import GraphContext, get_graph_context
from kt_api.schemas import (
    ConversationResponse,
    PaginatedConversationsResponse,
)

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
        return await _list_conversations_impl(session, mode, limit, offset)


@router.get("/{conversation_id}", response_model=ConversationResponse)
async def get_graph_conversation(
    conversation_id: str,
    ctx: GraphContext = Depends(get_graph_context),
) -> ConversationResponse:
    """Get a single conversation from a specific graph."""
    async with ctx.graph_session_factory() as session:
        return await _get_conversation_impl(session, conversation_id)
