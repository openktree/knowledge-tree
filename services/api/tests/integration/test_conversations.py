"""Integration tests for conversation list and detail endpoints."""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from kt_api.dependencies import get_db_session
from kt_api.main import create_app
from kt_db.models import Conversation, ConversationMessage

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture(loop_scope="session")
async def session_factory(engine):
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


@pytest_asyncio.fixture(loop_scope="session")
async def app(session_factory):
    application = create_app()

    async def override_get_db_session() -> AsyncGenerator[AsyncSession, None]:
        async with session_factory() as session:
            yield session

    application.dependency_overrides[get_db_session] = override_get_db_session
    return application


@pytest_asyncio.fixture(loop_scope="session")
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest_asyncio.fixture()
async def bottom_up_conversations(session_factory):
    """Create a few conversations with different modes."""
    async with session_factory() as session:
        async with session.begin():
            conv1 = Conversation(id=uuid.uuid4(), title="Web research 1", mode="bottom_up_ingest")
            session.add(conv1)
            await session.flush()

            # Add 2 messages (user + assistant)
            session.add(
                ConversationMessage(
                    id=uuid.uuid4(),
                    conversation_id=conv1.id,
                    turn_number=0,
                    role="user",
                    content="research quantum computing",
                )
            )
            session.add(
                ConversationMessage(
                    id=uuid.uuid4(),
                    conversation_id=conv1.id,
                    turn_number=1,
                    role="assistant",
                    content="",
                    status="completed",
                )
            )

            conv2 = Conversation(id=uuid.uuid4(), title="Web research 2", mode="bottom_up_ingest")
            session.add(conv2)
            await session.flush()

            session.add(
                ConversationMessage(
                    id=uuid.uuid4(),
                    conversation_id=conv2.id,
                    turn_number=0,
                    role="user",
                    content="research AI safety",
                )
            )

            conv3 = Conversation(id=uuid.uuid4(), title="Document ingest", mode="ingest")
            session.add(conv3)

        yield conv1, conv2, conv3


# ---------------------------------------------------------------------------
# GET /conversations
# ---------------------------------------------------------------------------


class TestListConversations:
    async def test_returns_all_conversations(self, client: AsyncClient, bottom_up_conversations):
        resp = await client.get("/api/v1/conversations")
        assert resp.status_code == 200
        data = resp.json()
        assert "items" in data
        assert "total" in data
        assert data["total"] >= 3

    async def test_filters_by_mode(self, client: AsyncClient, bottom_up_conversations):
        resp = await client.get("/api/v1/conversations", params={"mode": "bottom_up_ingest"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] >= 2
        for item in data["items"]:
            assert item["mode"] == "bottom_up_ingest"

    async def test_includes_message_count(self, client: AsyncClient, bottom_up_conversations):
        conv1, conv2, _ = bottom_up_conversations
        resp = await client.get("/api/v1/conversations", params={"mode": "bottom_up_ingest"})
        assert resp.status_code == 200
        data = resp.json()
        items_by_id = {item["id"]: item for item in data["items"]}

        # conv1 has 2 messages, conv2 has 1 message
        assert items_by_id[str(conv1.id)]["message_count"] == 2
        assert items_by_id[str(conv2.id)]["message_count"] == 1

    async def test_pagination(self, client: AsyncClient, bottom_up_conversations):
        resp = await client.get("/api/v1/conversations", params={"limit": 1, "offset": 0})
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["items"]) == 1
        assert data["limit"] == 1
        assert data["offset"] == 0

    async def test_empty_result_for_unknown_mode(self, client: AsyncClient):
        resp = await client.get("/api/v1/conversations", params={"mode": "nonexistent_mode"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 0
        assert data["items"] == []


# ---------------------------------------------------------------------------
# GET /conversations/{id}
# ---------------------------------------------------------------------------


class TestGetConversation:
    async def test_returns_conversation_with_messages(self, client: AsyncClient, bottom_up_conversations):
        conv1, _, _ = bottom_up_conversations
        resp = await client.get(f"/api/v1/conversations/{conv1.id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == str(conv1.id)
        assert data["title"] == "Web research 1"
        assert data["mode"] == "bottom_up_ingest"
        assert len(data["messages"]) == 2
        assert data["messages"][0]["role"] == "user"
        assert data["messages"][1]["role"] == "assistant"

    async def test_returns_404_for_unknown_id(self, client: AsyncClient):
        fake_id = str(uuid.uuid4())
        resp = await client.get(f"/api/v1/conversations/{fake_id}")
        assert resp.status_code == 404

    async def test_returns_400_for_invalid_id(self, client: AsyncClient):
        resp = await client.get("/api/v1/conversations/not-a-uuid")
        assert resp.status_code == 400
