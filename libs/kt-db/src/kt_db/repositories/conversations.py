"""Repository for Conversation and ConversationMessage CRUD."""

import uuid
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from kt_db.models import Conversation, ConversationMessage


class ConversationRepository:
    """Repository for Conversation and ConversationMessage CRUD."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # -- Conversation CRUD ------------------------------------------------

    async def create(self, title: str | None = None, mode: str = "research") -> Conversation:
        """Create a new conversation."""
        conv = Conversation(id=uuid.uuid4(), title=title, mode=mode)
        self._session.add(conv)
        await self._session.flush()
        return conv

    async def get_by_id(self, conversation_id: uuid.UUID) -> Conversation | None:
        """Get a conversation by ID (without messages)."""
        result = await self._session.execute(select(Conversation).where(Conversation.id == conversation_id))
        return result.scalar_one_or_none()

    async def get_with_messages(self, conversation_id: uuid.UUID) -> Conversation | None:
        """Get a conversation with all messages eagerly loaded, ordered by turn_number."""
        result = await self._session.execute(
            select(Conversation).options(selectinload(Conversation.messages)).where(Conversation.id == conversation_id)
        )
        return result.scalar_one_or_none()

    async def list_recent(
        self,
        limit: int = 20,
        offset: int = 0,
        mode: str | None = None,
    ) -> list[Conversation]:
        """List conversations ordered by most recently updated.

        If *mode* is provided, only conversations with that mode are returned.
        """
        stmt = select(Conversation)
        if mode is not None:
            stmt = stmt.where(Conversation.mode == mode)
        stmt = stmt.order_by(Conversation.updated_at.desc()).offset(offset).limit(limit)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def count(self, mode: str | None = None) -> int:
        """Count total conversations, optionally filtered by mode."""
        stmt = select(func.count(Conversation.id))
        if mode is not None:
            stmt = stmt.where(Conversation.mode == mode)
        result = await self._session.execute(stmt)
        return result.scalar_one()

    async def update_title(self, conversation_id: uuid.UUID, title: str) -> None:
        """Update the title of a conversation."""
        conv = await self.get_by_id(conversation_id)
        if conv:
            conv.title = title
            await self._session.flush()

    async def delete(self, conversation_id: uuid.UUID) -> bool:
        """Delete a conversation and all related data (cascade).

        Returns True if deleted, False if not found.
        """
        conv = await self.get_by_id(conversation_id)
        if conv is None:
            return False
        await self._session.delete(conv)
        await self._session.flush()
        return True

    # -- Message CRUD -----------------------------------------------------

    async def add_message(
        self,
        conversation_id: uuid.UUID,
        turn_number: int,
        role: str,
        content: str,
        **kwargs: Any,
    ) -> ConversationMessage:
        """Add a message to a conversation."""
        msg = ConversationMessage(
            id=uuid.uuid4(),
            conversation_id=conversation_id,
            turn_number=turn_number,
            role=role,
            content=content,
            **kwargs,
        )
        self._session.add(msg)
        await self._session.flush()
        return msg

    async def update_message(self, message_id: uuid.UUID, **kwargs: Any) -> None:
        """Update fields on a message."""
        result = await self._session.execute(select(ConversationMessage).where(ConversationMessage.id == message_id))
        msg = result.scalar_one_or_none()
        if msg:
            for key, value in kwargs.items():
                setattr(msg, key, value)
            await self._session.flush()

    async def get_message(self, message_id: uuid.UUID) -> ConversationMessage | None:
        """Get a single message by ID."""
        result = await self._session.execute(select(ConversationMessage).where(ConversationMessage.id == message_id))
        return result.scalar_one_or_none()

    async def get_messages(self, conversation_id: uuid.UUID) -> list[ConversationMessage]:
        """Get all messages for a conversation ordered by turn_number."""
        result = await self._session.execute(
            select(ConversationMessage)
            .where(ConversationMessage.conversation_id == conversation_id)
            .order_by(ConversationMessage.turn_number)
        )
        return list(result.scalars().all())

    # -- Derived queries --------------------------------------------------

    async def get_all_visited_nodes(self, conversation_id: uuid.UUID) -> list[str]:
        """Union of visited_nodes from all completed assistant messages."""
        result = await self._session.execute(
            select(ConversationMessage.visited_nodes).where(
                ConversationMessage.conversation_id == conversation_id,
                ConversationMessage.role == "assistant",
                ConversationMessage.status == "completed",
                ConversationMessage.visited_nodes.isnot(None),
            )
        )
        all_nodes: set[str] = set()
        for (nodes,) in result.all():
            if nodes:
                all_nodes.update(nodes)
        return list(all_nodes)

    async def get_all_created_nodes(self, conversation_id: uuid.UUID) -> list[str]:
        """Union of created_nodes from all completed assistant messages."""
        result = await self._session.execute(
            select(ConversationMessage.created_nodes).where(
                ConversationMessage.conversation_id == conversation_id,
                ConversationMessage.role == "assistant",
                ConversationMessage.status == "completed",
                ConversationMessage.created_nodes.isnot(None),
            )
        )
        all_nodes: set[str] = set()
        for (nodes,) in result.all():
            if nodes:
                all_nodes.update(nodes)
        return list(all_nodes)

    async def get_latest_answer(self, conversation_id: uuid.UUID) -> str | None:
        """Content of most recent completed assistant message."""
        result = await self._session.execute(
            select(ConversationMessage.content)
            .where(
                ConversationMessage.conversation_id == conversation_id,
                ConversationMessage.role == "assistant",
                ConversationMessage.status == "completed",
            )
            .order_by(ConversationMessage.turn_number.desc())
            .limit(1)
        )
        row = result.scalar_one_or_none()
        return row

    async def get_next_turn_number(self, conversation_id: uuid.UUID) -> int:
        """Get the next turn number for a conversation."""
        result = await self._session.execute(
            select(func.coalesce(func.max(ConversationMessage.turn_number), -1)).where(
                ConversationMessage.conversation_id == conversation_id
            )
        )
        max_turn: int = result.scalar_one()
        return max_turn + 1

    async def get_message_count(self, conversation_id: uuid.UUID) -> int:
        """Count messages in a conversation."""
        result = await self._session.execute(
            select(func.count(ConversationMessage.id)).where(ConversationMessage.conversation_id == conversation_id)
        )
        return result.scalar_one()
