"""Integration tests for REST API endpoints."""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator
from unittest.mock import AsyncMock, patch

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from kt_api.dependencies import get_db_session
from kt_api.main import create_app
from kt_db.models import (
    Conversation,
    ConversationMessage,
    Edge,
    Fact,
    FactSource,
    Node,
    NodeFact,
    RawSource,
)


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def api_session_factory(engine):
    """Session factory for API integration tests, session-scoped."""
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def api_app(api_session_factory):
    """Create a test FastAPI app with DB session overridden to use the test DB."""
    application = create_app()

    async def override_get_db_session() -> AsyncGenerator[AsyncSession, None]:
        async with api_session_factory() as session:
            yield session

    application.dependency_overrides[get_db_session] = override_get_db_session
    return application


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def api_client(api_app):
    """Create an async HTTP client for testing."""
    transport = ASGITransport(app=api_app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


# ── Conversation endpoints ───────────────────────────────────────────


@patch("kt_worker_query.workflows.query.query_wf.aio_run_no_wait", new_callable=AsyncMock)
async def test_create_conversation(mock_run, api_client: AsyncClient):
    mock_run.return_value.workflow_run_id = str(uuid.uuid4())
    resp = await api_client.post(
        "/api/v1/conversations",
        json={"message": "what is water"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "id" in data
    assert data["title"] == "what is water"
    assert len(data["messages"]) == 2
    assert data["messages"][0]["role"] == "user"
    assert data["messages"][0]["content"] == "what is water"
    assert data["messages"][1]["role"] == "assistant"
    assert data["messages"][1]["status"] == "pending"


@patch("kt_worker_query.workflows.query.query_wf.aio_run_no_wait", new_callable=AsyncMock)
async def test_create_conversation_custom_budgets(mock_run, api_client: AsyncClient):
    mock_run.return_value.workflow_run_id = str(uuid.uuid4())
    resp = await api_client.post(
        "/api/v1/conversations",
        json={"message": "test", "nav_budget": 50, "explore_budget": 5},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["messages"][1]["nav_budget"] == 50
    assert data["messages"][1]["explore_budget"] == 0  # query mode always sets explore_budget=0


async def test_get_conversation_not_found(api_client: AsyncClient):
    fake_id = str(uuid.uuid4())
    resp = await api_client.get(f"/api/v1/conversations/{fake_id}")
    assert resp.status_code == 404


async def test_get_conversation_invalid_id(api_client: AsyncClient):
    resp = await api_client.get("/api/v1/conversations/not-a-uuid")
    assert resp.status_code == 400


async def test_list_conversations(api_client: AsyncClient):
    resp = await api_client.get("/api/v1/conversations", params={"limit": 5})
    assert resp.status_code == 200
    data = resp.json()
    assert "items" in data
    assert "total" in data
    assert "offset" in data
    assert "limit" in data


async def test_get_conversation_with_db(api_session_factory):
    """Create a conversation in DB and fetch via API."""
    async with api_session_factory() as session:
        conv = Conversation(id=uuid.uuid4(), title="API test conv")
        session.add(conv)
        await session.flush()

        msg = ConversationMessage(
            id=uuid.uuid4(),
            conversation_id=conv.id,
            turn_number=0,
            role="user",
            content="Hello",
        )
        session.add(msg)
        await session.flush()

        app = create_app()

        async def override() -> AsyncGenerator[AsyncSession, None]:
            yield session

        app.dependency_overrides[get_db_session] = override

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            resp = await c.get(f"/api/v1/conversations/{conv.id}")
            assert resp.status_code == 200
            data = resp.json()
            assert data["title"] == "API test conv"
            assert len(data["messages"]) == 1
            assert data["messages"][0]["content"] == "Hello"

        await session.rollback()


# ── Delete conversation endpoints ────────────────────────────────────


async def test_delete_conversation_not_found(api_client: AsyncClient):
    fake_id = str(uuid.uuid4())
    resp = await api_client.delete(f"/api/v1/conversations/{fake_id}")
    assert resp.status_code == 404


async def test_delete_conversation_invalid_id(api_client: AsyncClient):
    resp = await api_client.delete("/api/v1/conversations/not-a-uuid")
    assert resp.status_code == 400


async def test_delete_conversation_with_db(api_session_factory):
    """Create a conversation and delete it, verify it's gone."""
    async with api_session_factory() as session:
        conv = Conversation(id=uuid.uuid4(), title="Delete me")
        session.add(conv)
        msg = ConversationMessage(
            id=uuid.uuid4(),
            conversation_id=conv.id,
            turn_number=0,
            role="user",
            content="Hello",
        )
        session.add(msg)
        await session.commit()

        app = create_app()

        async def override() -> AsyncGenerator[AsyncSession, None]:
            yield session

        app.dependency_overrides[get_db_session] = override

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            resp = await c.delete(f"/api/v1/conversations/{conv.id}")
            assert resp.status_code == 200
            data = resp.json()
            assert data["deleted"] is True
            assert data["id"] == str(conv.id)

            # Verify it's gone
            resp2 = await c.get(f"/api/v1/conversations/{conv.id}")
            assert resp2.status_code == 404

        await session.rollback()


async def test_delete_conversation_running_rejected(api_session_factory):
    """Cannot delete a conversation with a running message."""
    async with api_session_factory() as session:
        conv = Conversation(id=uuid.uuid4(), title="Running conv")
        session.add(conv)
        msg = ConversationMessage(
            id=uuid.uuid4(),
            conversation_id=conv.id,
            turn_number=1,
            role="assistant",
            content="",
            status="running",
        )
        session.add(msg)
        await session.commit()

        app = create_app()

        async def override() -> AsyncGenerator[AsyncSession, None]:
            yield session

        app.dependency_overrides[get_db_session] = override

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            resp = await c.delete(f"/api/v1/conversations/{conv.id}")
            assert resp.status_code == 409

        await session.rollback()


# ── Graph endpoints ──────────────────────────────────────────────────


async def test_graph_stats(api_client: AsyncClient):
    resp = await api_client.get("/api/v1/graph/stats")
    assert resp.status_code == 200
    data = resp.json()
    assert "node_count" in data
    assert "edge_count" in data
    assert "fact_count" in data
    assert "source_count" in data


async def test_graph_subgraph_empty(api_client: AsyncClient):
    fake_id = str(uuid.uuid4())
    resp = await api_client.get(f"/api/v1/graph/subgraph?node_ids={fake_id}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["nodes"] == []
    assert data["edges"] == []


async def test_graph_subgraph_invalid_ids(api_client: AsyncClient):
    resp = await api_client.get("/api/v1/graph/subgraph?node_ids=not-a-uuid")
    assert resp.status_code == 200
    data = resp.json()
    assert data["nodes"] == []


# ── Node endpoints ───────────────────────────────────────────────────


async def test_node_search(api_client: AsyncClient):
    resp = await api_client.get("/api/v1/nodes/search", params={"query": "nonexistent-concept-xyz"})
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)


async def test_get_node_not_found(api_client: AsyncClient):
    fake_id = str(uuid.uuid4())
    resp = await api_client.get(f"/api/v1/nodes/{fake_id}")
    assert resp.status_code == 404


async def test_get_node_invalid_id(api_client: AsyncClient):
    # Non-UUID strings are treated as write-db keys and resolved via key_to_uuid.
    # The derived UUID won't exist, so we get 404 instead of 400.
    resp = await api_client.get("/api/v1/nodes/not-a-uuid")
    assert resp.status_code == 404


async def test_get_node_with_db(api_session_factory):
    """Create a node in the DB and fetch it via API using a shared session."""
    async with api_session_factory() as session:
        node = Node(
            id=uuid.uuid4(),
            concept="api_test_concept",
            max_content_tokens=500,
        )
        session.add(node)
        await session.flush()

        # Create app that shares this session
        app = create_app()

        async def override() -> AsyncGenerator[AsyncSession, None]:
            yield session

        app.dependency_overrides[get_db_session] = override

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            resp = await c.get(f"/api/v1/nodes/{node.id}")
            assert resp.status_code == 200
            data = resp.json()
            assert data["concept"] == "api_test_concept"
            assert data["id"] == str(node.id)

        await session.rollback()


async def test_get_node_dimensions(api_client: AsyncClient):
    fake_id = str(uuid.uuid4())
    resp = await api_client.get(f"/api/v1/nodes/{fake_id}/dimensions")
    assert resp.status_code == 404


async def test_get_node_facts(api_client: AsyncClient):
    fake_id = str(uuid.uuid4())
    resp = await api_client.get(f"/api/v1/nodes/{fake_id}/facts")
    assert resp.status_code == 404


async def test_get_node_edges(api_client: AsyncClient):
    fake_id = str(uuid.uuid4())
    resp = await api_client.get(f"/api/v1/nodes/{fake_id}/edges")
    assert resp.status_code == 404


async def test_get_node_history(api_client: AsyncClient):
    fake_id = str(uuid.uuid4())
    resp = await api_client.get(f"/api/v1/nodes/{fake_id}/history")
    assert resp.status_code == 404


async def test_get_node_convergence(api_client: AsyncClient):
    fake_id = str(uuid.uuid4())
    resp = await api_client.get(f"/api/v1/nodes/{fake_id}/convergence")
    assert resp.status_code == 404


async def test_node_dimensions_with_db(api_session_factory):
    """Get dimensions for a node that exists but has none."""
    async with api_session_factory() as session:
        node = Node(id=uuid.uuid4(), concept="api_dim_test", max_content_tokens=500)
        session.add(node)
        await session.flush()

        app = create_app()

        async def override() -> AsyncGenerator[AsyncSession, None]:
            yield session

        app.dependency_overrides[get_db_session] = override

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            resp = await c.get(f"/api/v1/nodes/{node.id}/dimensions")
            assert resp.status_code == 200
            assert resp.json() == []

        await session.rollback()


async def test_node_facts_with_db(api_session_factory):
    """Create a node with linked facts (including source provenance) and verify the endpoint."""
    async with api_session_factory() as session:
        node = Node(id=uuid.uuid4(), concept="api_fact_test", max_content_tokens=500)
        session.add(node)

        fact = Fact(id=uuid.uuid4(), content="Test fact content", fact_type="claim")
        session.add(fact)

        raw_source = RawSource(
            id=uuid.uuid4(),
            uri="https://example.com/fact-source",
            title="Fact Source Title",
            raw_content="some raw content",
            content_hash="api_fact_test_hash_" + str(uuid.uuid4()),
            provider_id="brave_search",
        )
        session.add(raw_source)
        await session.flush()

        link = NodeFact(node_id=node.id, fact_id=fact.id, relevance_score=1.0)
        session.add(link)

        fact_source = FactSource(
            id=uuid.uuid4(),
            fact_id=fact.id,
            raw_source_id=raw_source.id,
            context_snippet="relevant snippet",
            attribution="Test Author",
        )
        session.add(fact_source)
        await session.flush()

        app = create_app()

        async def override() -> AsyncGenerator[AsyncSession, None]:
            yield session

        app.dependency_overrides[get_db_session] = override

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            resp = await c.get(f"/api/v1/nodes/{node.id}/facts")
            assert resp.status_code == 200
            data = resp.json()
            assert len(data) == 1
            assert data[0]["content"] == "Test fact content"
            assert len(data[0]["sources"]) == 1
            src = data[0]["sources"][0]
            assert src["uri"] == "https://example.com/fact-source"
            assert src["title"] == "Fact Source Title"
            assert src["provider_id"] == "brave_search"
            assert src["context_snippet"] == "relevant snippet"
            assert src["attribution"] == "Test Author"
            assert "retrieved_at" in src
            assert src["source_id"] == str(raw_source.id)

        await session.rollback()


async def test_node_convergence_with_db(api_session_factory):
    """Test convergence for a node with no dimensions."""
    async with api_session_factory() as session:
        node = Node(id=uuid.uuid4(), concept="api_conv_test", max_content_tokens=500)
        session.add(node)
        await session.flush()

        app = create_app()

        async def override() -> AsyncGenerator[AsyncSession, None]:
            yield session

        app.dependency_overrides[get_db_session] = override

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            resp = await c.get(f"/api/v1/nodes/{node.id}/convergence")
            assert resp.status_code == 200
            data = resp.json()
            assert data["convergence_score"] == 0.0

        await session.rollback()


# ── Fact endpoints ───────────────────────────────────────────────────


async def test_get_fact_not_found(api_client: AsyncClient):
    fake_id = str(uuid.uuid4())
    resp = await api_client.get(f"/api/v1/facts/{fake_id}")
    assert resp.status_code == 404


async def test_get_fact_invalid_id(api_client: AsyncClient):
    resp = await api_client.get("/api/v1/facts/bad-id")
    assert resp.status_code == 400


async def test_get_fact_with_db(api_session_factory):
    """Create a fact in the DB and fetch it via API, verifying sources are included."""
    async with api_session_factory() as session:
        fact = Fact(id=uuid.uuid4(), content="Test API fact", fact_type="claim")
        session.add(fact)

        raw_source = RawSource(
            id=uuid.uuid4(),
            uri="https://example.com/get-fact-test",
            title="Get Fact Source",
            raw_content="content",
            content_hash="get_fact_test_hash_" + str(uuid.uuid4()),
            provider_id="brave_search",
        )
        session.add(raw_source)
        await session.flush()

        fact_source = FactSource(
            id=uuid.uuid4(),
            fact_id=fact.id,
            raw_source_id=raw_source.id,
            context_snippet="get fact snippet",
        )
        session.add(fact_source)
        await session.flush()

        app = create_app()

        async def override() -> AsyncGenerator[AsyncSession, None]:
            yield session

        app.dependency_overrides[get_db_session] = override

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            resp = await c.get(f"/api/v1/facts/{fact.id}")
            assert resp.status_code == 200
            data = resp.json()
            assert data["content"] == "Test API fact"
            assert data["fact_type"] == "claim"
            assert len(data["sources"]) == 1
            assert data["sources"][0]["uri"] == "https://example.com/get-fact-test"
            assert data["sources"][0]["context_snippet"] == "get fact snippet"

        await session.rollback()


async def test_search_facts(api_client: AsyncClient):
    resp = await api_client.get("/api/v1/facts/search", params={"fact_type": "claim"})
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


# ── Source endpoints ─────────────────────────────────────────────────


async def test_get_source_not_found(api_client: AsyncClient):
    fake_id = str(uuid.uuid4())
    resp = await api_client.get(f"/api/v1/sources/{fake_id}")
    assert resp.status_code == 404


async def test_get_source_invalid_id(api_client: AsyncClient):
    resp = await api_client.get("/api/v1/sources/bad-id")
    assert resp.status_code == 400


async def test_get_source_with_db(api_session_factory):
    """Create a source in the DB and fetch it via API."""
    async with api_session_factory() as session:
        source = RawSource(
            id=uuid.uuid4(),
            uri="https://example.com/test",
            title="Test Source",
            raw_content="Some content",
            content_hash="abc123unique" + str(uuid.uuid4()),
            provider_id="brave_search",
        )
        session.add(source)
        await session.flush()

        app = create_app()

        async def override() -> AsyncGenerator[AsyncSession, None]:
            yield session

        app.dependency_overrides[get_db_session] = override

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            resp = await c.get(f"/api/v1/sources/{source.id}")
            assert resp.status_code == 200
            data = resp.json()
            assert data["uri"] == "https://example.com/test"
            assert data["title"] == "Test Source"
            assert data["provider_id"] == "brave_search"

        await session.rollback()


# ── Config endpoints ─────────────────────────────────────────────────


async def test_config_models(api_client: AsyncClient):
    resp = await api_client.get("/api/v1/config/models")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)
    assert len(data) > 0
    assert "model_id" in data[0]


async def test_config_filters(api_client: AsyncClient):
    resp = await api_client.get("/api/v1/config/filters")
    assert resp.status_code == 200
    data = resp.json()
    assert "filters" in data


# ── Admin endpoints ──────────────────────────────────────────────────


async def test_admin_reindex(api_client: AsyncClient):
    resp = await api_client.post("/api/v1/admin/reindex")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"


async def test_admin_refresh_stale(api_client: AsyncClient):
    resp = await api_client.post("/api/v1/admin/refresh-stale")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"


# ── Node list/update/delete endpoints ────────────────────────────────


async def test_list_nodes(api_client: AsyncClient):
    resp = await api_client.get("/api/v1/nodes", params={"limit": 5})
    assert resp.status_code == 200
    data = resp.json()
    assert "items" in data
    assert "total" in data
    assert "offset" in data
    assert "limit" in data
    assert isinstance(data["items"], list)


async def test_list_nodes_with_search(api_client: AsyncClient):
    resp = await api_client.get("/api/v1/nodes", params={"search": "nonexistent_xyz_concept"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 0
    assert data["items"] == []


async def test_update_node_with_db(api_session_factory):
    """Create a node and update it via PATCH."""
    async with api_session_factory() as session:
        node = Node(id=uuid.uuid4(), concept="api_update_test", max_content_tokens=500)
        session.add(node)
        await session.commit()

        app = create_app()

        async def override() -> AsyncGenerator[AsyncSession, None]:
            yield session

        app.dependency_overrides[get_db_session] = override

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            resp = await c.patch(
                f"/api/v1/nodes/{node.id}",
                json={"concept": "updated_concept", "max_content_tokens": 1000},
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["concept"] == "updated_concept"
            assert data["max_content_tokens"] == 1000

        await session.rollback()


async def test_update_node_no_fields(api_session_factory):
    """PATCH with empty body returns 400."""
    async with api_session_factory() as session:
        node = Node(id=uuid.uuid4(), concept="api_update_empty_test", max_content_tokens=500)
        session.add(node)
        await session.commit()

        app = create_app()

        async def override() -> AsyncGenerator[AsyncSession, None]:
            yield session

        app.dependency_overrides[get_db_session] = override

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            resp = await c.patch(f"/api/v1/nodes/{node.id}", json={})
            assert resp.status_code == 400

        await session.rollback()


async def test_update_node_not_found(api_client: AsyncClient):
    fake_id = str(uuid.uuid4())
    resp = await api_client.patch(f"/api/v1/nodes/{fake_id}", json={"concept": "test"})
    assert resp.status_code == 404


async def test_delete_node_with_db(api_session_factory):
    """Create a node and delete it, verify facts survive."""
    async with api_session_factory() as session:
        node = Node(id=uuid.uuid4(), concept="api_delete_node_test", max_content_tokens=500)
        session.add(node)
        fact = Fact(id=uuid.uuid4(), content="Surviving fact", fact_type="claim")
        session.add(fact)
        await session.flush()
        link = NodeFact(node_id=node.id, fact_id=fact.id, relevance_score=1.0)
        session.add(link)
        await session.commit()

        app = create_app()

        async def override() -> AsyncGenerator[AsyncSession, None]:
            yield session

        app.dependency_overrides[get_db_session] = override

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            # Delete the node
            resp = await c.delete(f"/api/v1/nodes/{node.id}")
            assert resp.status_code == 200
            assert resp.json()["deleted"] is True

            # Verify node is gone
            resp2 = await c.get(f"/api/v1/nodes/{node.id}")
            assert resp2.status_code == 404

            # Verify fact still exists
            resp3 = await c.get(f"/api/v1/facts/{fact.id}")
            assert resp3.status_code == 200
            assert resp3.json()["content"] == "Surviving fact"

        await session.rollback()


async def test_delete_node_not_found(api_client: AsyncClient):
    fake_id = str(uuid.uuid4())
    resp = await api_client.delete(f"/api/v1/nodes/{fake_id}")
    assert resp.status_code == 404


# ── Fact list/update/delete endpoints ────────────────────────────────


async def test_list_facts(api_client: AsyncClient):
    resp = await api_client.get("/api/v1/facts", params={"limit": 5})
    assert resp.status_code == 200
    data = resp.json()
    assert "items" in data
    assert "total" in data
    assert isinstance(data["items"], list)


async def test_list_facts_with_type_filter(api_client: AsyncClient):
    resp = await api_client.get("/api/v1/facts", params={"fact_type": "claim"})
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data["items"], list)


async def test_update_fact_with_db(api_session_factory):
    """Create a fact and update it via PATCH."""
    async with api_session_factory() as session:
        fact = Fact(id=uuid.uuid4(), content="Original fact", fact_type="claim")
        session.add(fact)
        await session.commit()

        app = create_app()

        async def override() -> AsyncGenerator[AsyncSession, None]:
            yield session

        app.dependency_overrides[get_db_session] = override

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            resp = await c.patch(
                f"/api/v1/facts/{fact.id}",
                json={"content": "Updated fact", "fact_type": "account"},
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["content"] == "Updated fact"
            assert data["fact_type"] == "account"

        await session.rollback()


async def test_delete_fact_with_db(api_session_factory):
    """Create a fact and delete it."""
    async with api_session_factory() as session:
        fact = Fact(id=uuid.uuid4(), content="Delete me fact", fact_type="claim")
        session.add(fact)
        await session.commit()

        app = create_app()

        async def override() -> AsyncGenerator[AsyncSession, None]:
            yield session

        app.dependency_overrides[get_db_session] = override

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            resp = await c.delete(f"/api/v1/facts/{fact.id}")
            assert resp.status_code == 200
            assert resp.json()["deleted"] is True

            resp2 = await c.get(f"/api/v1/facts/{fact.id}")
            assert resp2.status_code == 404

        await session.rollback()


async def test_delete_fact_not_found(api_client: AsyncClient):
    fake_id = str(uuid.uuid4())
    resp = await api_client.delete(f"/api/v1/facts/{fake_id}")
    assert resp.status_code == 404


# ── Export endpoints ─────────────────────────────────────────────────────


async def test_export_nodes_empty(api_client: AsyncClient):
    resp = await api_client.get("/api/v1/export/nodes")
    assert resp.status_code == 200
    data = resp.json()
    assert "metadata" in data
    assert data["metadata"]["export_type"] == "nodes"
    assert data["metadata"]["version"] == "1.1"
    assert "exported_at" in data["metadata"]
    assert isinstance(data["nodes"], list)


async def test_export_facts_empty(api_client: AsyncClient):
    resp = await api_client.get("/api/v1/export/facts")
    assert resp.status_code == 200
    data = resp.json()
    assert "metadata" in data
    assert data["metadata"]["export_type"] == "facts"
    assert data["metadata"]["version"] == "1.1"
    assert isinstance(data["facts"], list)


async def test_export_conversation_not_found(api_client: AsyncClient):
    fake_id = str(uuid.uuid4())
    resp = await api_client.get(f"/api/v1/export/conversations/{fake_id}")
    assert resp.status_code == 404


async def test_export_conversation_invalid_id(api_client: AsyncClient):
    resp = await api_client.get("/api/v1/export/conversations/not-a-uuid")
    assert resp.status_code == 400


async def test_export_nodes_includes_facts(api_session_factory):
    """Verify that export_nodes includes facts and node_fact_links."""
    async with api_session_factory() as session:
        node = Node(id=uuid.uuid4(), concept="export_fact_test", max_content_tokens=500)
        session.add(node)
        fact = Fact(id=uuid.uuid4(), content="Exported fact", fact_type="claim")
        session.add(fact)
        raw_source = RawSource(
            id=uuid.uuid4(),
            uri="https://example.com/export-test",
            title="Export Source",
            raw_content="content",
            content_hash="export_test_hash_" + str(uuid.uuid4()),
            provider_id="brave_search",
        )
        session.add(raw_source)
        await session.flush()

        link = NodeFact(node_id=node.id, fact_id=fact.id, relevance_score=1.0)
        session.add(link)
        fact_source = FactSource(
            id=uuid.uuid4(),
            fact_id=fact.id,
            raw_source_id=raw_source.id,
        )
        session.add(fact_source)
        await session.flush()

        app = create_app()

        async def override() -> AsyncGenerator[AsyncSession, None]:
            yield session

        app.dependency_overrides[get_db_session] = override

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            resp = await c.get("/api/v1/export/nodes")
            assert resp.status_code == 200
            data = resp.json()
            assert len(data["facts"]) >= 1
            assert len(data["node_fact_links"]) >= 1
            # Verify the link references the correct node and fact
            link_found = any(
                lnk["node_id"] == str(node.id) and lnk["fact_id"] == str(fact.id) for lnk in data["node_fact_links"]
            )
            assert link_found

        await session.rollback()


async def test_export_nodes_includes_edges(api_session_factory):
    """Verify that export_nodes includes edges with justification and supporting_fact_ids."""
    async with api_session_factory() as session:
        node_a = Node(id=uuid.uuid4(), concept="export_edge_test_a", max_content_tokens=500)
        node_b = Node(id=uuid.uuid4(), concept="export_edge_test_b", max_content_tokens=500)
        session.add(node_a)
        session.add(node_b)
        await session.flush()

        edge = Edge(
            id=uuid.uuid4(),
            source_node_id=node_a.id,
            target_node_id=node_b.id,
            relationship_type="related",
            weight=0.8,
            justification="Both are related test concepts",
        )
        session.add(edge)
        await session.flush()

        app = create_app()

        async def override() -> AsyncGenerator[AsyncSession, None]:
            yield session

        app.dependency_overrides[get_db_session] = override

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            resp = await c.get("/api/v1/export/nodes")
            assert resp.status_code == 200
            data = resp.json()
            assert "edges" in data
            edge_found = any(
                e["id"] == str(edge.id)
                and e["relationship_type"] == "related"
                and e["justification"] == "Both are related test concepts"
                and e["weight"] == 0.8
                for e in data["edges"]
            )
            assert edge_found, f"Expected edge {edge.id} not found in export"

        await session.rollback()


async def test_export_import_roundtrip_preserves_edges(api_session_factory):
    """Verify that export→import round-trip preserves edges."""
    async with api_session_factory() as session:
        node_a = Node(id=uuid.uuid4(), concept="roundtrip_edge_a_unique_xyz", max_content_tokens=500)
        node_b = Node(id=uuid.uuid4(), concept="roundtrip_edge_b_unique_xyz", max_content_tokens=500)
        session.add(node_a)
        session.add(node_b)
        await session.flush()

        edge = Edge(
            id=uuid.uuid4(),
            source_node_id=node_a.id,
            target_node_id=node_b.id,
            relationship_type="related",
            weight=0.7,
            justification="A causes B in tests",
        )
        session.add(edge)
        await session.flush()

        app = create_app()

        async def override() -> AsyncGenerator[AsyncSession, None]:
            yield session

        app.dependency_overrides[get_db_session] = override

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            # Export
            export_resp = await c.get("/api/v1/export/nodes")
            assert export_resp.status_code == 200
            export_data = export_resp.json()
            assert len(export_data["edges"]) >= 1

            # Import
            import_resp = await c.post(
                "/api/v1/import/nodes",
                json={
                    "nodes": export_data["nodes"],
                    "edges": export_data["edges"],
                    "facts": export_data["facts"],
                    "node_fact_links": export_data["node_fact_links"],
                },
            )
            assert import_resp.status_code == 200
            import_data = import_resp.json()
            assert import_data["imported_edges"] >= 1

        await session.rollback()


async def test_export_conversation_includes_node_fact_links(api_session_factory):
    """Verify that export_conversation includes node_fact_links."""
    async with api_session_factory() as session:
        # Create conversation with a message referencing a node
        conv = Conversation(id=uuid.uuid4(), title="Link export test")
        session.add(conv)
        node = Node(id=uuid.uuid4(), concept="conv_link_test", max_content_tokens=500)
        session.add(node)
        fact = Fact(id=uuid.uuid4(), content="Conv fact", fact_type="claim")
        session.add(fact)
        raw_source = RawSource(
            id=uuid.uuid4(),
            uri="https://example.com/conv-link-test",
            title="Conv Link Source",
            raw_content="content",
            content_hash="conv_link_hash_" + str(uuid.uuid4()),
            provider_id="brave_search",
        )
        session.add(raw_source)
        await session.flush()

        link = NodeFact(node_id=node.id, fact_id=fact.id, relevance_score=1.0)
        session.add(link)
        fact_source = FactSource(
            id=uuid.uuid4(),
            fact_id=fact.id,
            raw_source_id=raw_source.id,
        )
        session.add(fact_source)

        msg = ConversationMessage(
            id=uuid.uuid4(),
            conversation_id=conv.id,
            turn_number=0,
            role="user",
            content="test",
            created_nodes=[str(node.id)],
        )
        session.add(msg)
        await session.flush()

        app = create_app()

        async def override() -> AsyncGenerator[AsyncSession, None]:
            yield session

        app.dependency_overrides[get_db_session] = override

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            resp = await c.get(f"/api/v1/export/conversations/{conv.id}")
            assert resp.status_code == 200
            data = resp.json()
            assert "node_fact_links" in data
            assert len(data["node_fact_links"]) >= 1

        await session.rollback()


# ── Import endpoints ─────────────────────────────────────────────────────


async def test_import_facts_empty(api_client: AsyncClient):
    resp = await api_client.post("/api/v1/import/facts", json={"facts": []})
    assert resp.status_code == 200
    data = resp.json()
    assert data["imported_facts"] == []


async def test_import_facts_creates_new(api_session_factory):
    """Import a fact and verify it exists."""
    import uuid as _uuid

    unique_content = f"Imported test fact for API test {_uuid.uuid4()}"
    async with api_session_factory() as session:
        app = create_app()

        async def override() -> AsyncGenerator[AsyncSession, None]:
            yield session

        app.dependency_overrides[get_db_session] = override

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            resp = await c.post(
                "/api/v1/import/facts",
                json={
                    "facts": [
                        {
                            "id": "old-fact-id",
                            "content": unique_content,
                            "fact_type": "claim",
                            "created_at": "2025-01-01T00:00:00Z",
                            "sources": [],
                        }
                    ],
                },
            )
            assert resp.status_code == 200
            data = resp.json()
            assert len(data["imported_facts"]) == 1
            assert data["imported_facts"][0]["old_id"] == "old-fact-id"
            assert data["imported_facts"][0]["is_new"] is True

            # Verify the fact exists
            new_id = data["imported_facts"][0]["new_id"]
            fact_resp = await c.get(f"/api/v1/facts/{new_id}")
            assert fact_resp.status_code == 200
            assert fact_resp.json()["content"] == unique_content

        await session.rollback()


async def test_import_nodes_empty(api_client: AsyncClient):
    resp = await api_client.post("/api/v1/import/nodes", json={"nodes": []})
    assert resp.status_code == 200
    data = resp.json()
    assert data["imported_nodes"] == []
    assert data["imported_facts"] == []
    assert data["imported_edges"] == 0


async def test_import_nodes_creates_new(api_session_factory):
    """Import a node and verify it exists."""
    async with api_session_factory() as session:
        app = create_app()

        async def override() -> AsyncGenerator[AsyncSession, None]:
            yield session

        app.dependency_overrides[get_db_session] = override

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            resp = await c.post(
                "/api/v1/import/nodes",
                json={
                    "nodes": [
                        {
                            "id": "old-node-id",
                            "concept": "Unique Imported Test Concept XYZ123",
                            "node_type": "concept",
                            "parent_id": None,
                            "attractor": None,
                            "filter_id": None,
                            "max_content_tokens": 500,
                            "created_at": "2025-01-01T00:00:00Z",
                            "updated_at": "2025-01-01T00:00:00Z",
                            "update_count": 0,
                            "access_count": 0,
                            "richness": 0.0,
                            "convergence_score": 0.0,
                        }
                    ],
                    "edges": [],
                    "facts": [],
                    "node_fact_links": [],
                },
            )
            assert resp.status_code == 200
            data = resp.json()
            assert len(data["imported_nodes"]) == 1
            assert data["imported_nodes"][0]["old_id"] == "old-node-id"
            assert data["imported_nodes"][0]["is_new"] is True

            # Verify the node exists
            new_id = data["imported_nodes"][0]["new_id"]
            node_resp = await c.get(f"/api/v1/nodes/{new_id}")
            assert node_resp.status_code == 200
            assert node_resp.json()["concept"] == "Unique Imported Test Concept XYZ123"

        await session.rollback()


async def test_export_includes_full_source_fields(api_session_factory):
    """Export includes content_hash, is_full_text, content_type, provider_metadata on sources."""
    async with api_session_factory() as session:
        node = Node(id=uuid.uuid4(), concept="export_source_fields_test", max_content_tokens=500)
        session.add(node)
        fact = Fact(id=uuid.uuid4(), content="Source fields fact", fact_type="claim")
        session.add(fact)
        raw_source = RawSource(
            id=uuid.uuid4(),
            uri="https://example.com/full-source",
            title="Full Source",
            raw_content="full content body",
            content_hash="full_source_hash_" + str(uuid.uuid4()),
            provider_id="brave_search",
            is_full_text=True,
            content_type="text/html",
            provider_metadata={"query": "test"},
        )
        session.add(raw_source)
        await session.flush()

        link = NodeFact(node_id=node.id, fact_id=fact.id, relevance_score=0.9, stance="supporting")
        session.add(link)
        fact_source = FactSource(
            id=uuid.uuid4(),
            fact_id=fact.id,
            raw_source_id=raw_source.id,
        )
        session.add(fact_source)
        await session.flush()

        app = create_app()

        async def override() -> AsyncGenerator[AsyncSession, None]:
            yield session

        app.dependency_overrides[get_db_session] = override

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            # Default: raw_content omitted
            resp = await c.get("/api/v1/export/nodes")
            assert resp.status_code == 200
            data = resp.json()
            assert data["metadata"]["version"] == "1.1"
            fact_data = next(f for f in data["facts"] if f["id"] == str(fact.id))
            src = fact_data["sources"][0]
            assert src["content_hash"] is not None
            assert src["is_full_text"] is True
            assert src["content_type"] == "text/html"
            assert src["provider_metadata"] == {"query": "test"}
            assert src["raw_content"] is None  # omitted by default

            # With include_raw_content=true
            resp2 = await c.get("/api/v1/export/nodes?include_raw_content=true")
            assert resp2.status_code == 200
            data2 = resp2.json()
            fact_data2 = next(f for f in data2["facts"] if f["id"] == str(fact.id))
            src2 = fact_data2["sources"][0]
            assert src2["raw_content"] == "full content body"

            # Verify node_fact_links include relevance_score and stance
            link_data = next(
                lnk
                for lnk in data["node_fact_links"]
                if lnk["node_id"] == str(node.id) and lnk["fact_id"] == str(fact.id)
            )
            assert link_data["relevance_score"] == 0.9
            assert link_data["stance"] == "supporting"

        await session.rollback()


async def test_import_v10_backwards_compat(api_session_factory):
    """v1.0 export format (without new fields) still imports correctly."""
    async with api_session_factory() as session:
        app = create_app()

        async def override() -> AsyncGenerator[AsyncSession, None]:
            yield session

        app.dependency_overrides[get_db_session] = override

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            resp = await c.post(
                "/api/v1/import/nodes",
                json={
                    "nodes": [
                        {
                            "id": "old-v10-node",
                            "concept": "V10 Compat Test Node Unique ABC",
                            "node_type": "concept",
                            "parent_id": None,
                            "attractor": None,
                            "filter_id": None,
                            "max_content_tokens": 500,
                            "created_at": "2025-01-01T00:00:00Z",
                            "updated_at": "2025-01-01T00:00:00Z",
                            "update_count": 0,
                            "access_count": 0,
                            "richness": 0.0,
                            "convergence_score": 0.0,
                        }
                    ],
                    "facts": [
                        {
                            "id": "old-v10-fact",
                            "content": "V10 compat fact unique XYZ",
                            "fact_type": "claim",
                            "created_at": "2025-01-01T00:00:00Z",
                            "sources": [
                                {
                                    "source_id": "old-source-id",
                                    "uri": "https://example.com/v10-source",
                                    "title": "V10 Source",
                                    "provider_id": "brave_search",
                                    "retrieved_at": "2025-01-01T00:00:00Z",
                                }
                            ],
                        }
                    ],
                    # v1.0 links: no relevance_score/stance
                    "node_fact_links": [
                        {"node_id": "old-v10-node", "fact_id": "old-v10-fact"},
                    ],
                    "edges": [],
                },
            )
            assert resp.status_code == 200
            data = resp.json()
            assert len(data["imported_nodes"]) == 1
            assert len(data["imported_facts"]) == 1
            # Seeds should be created even from v1.0 format
            assert data["imported_seeds"] >= 1

        await session.rollback()


async def test_import_with_content_hash_uses_real_hash(api_session_factory):
    """Import with content_hash from v1.1 export uses real hash instead of synthetic."""
    async with api_session_factory() as session:
        app = create_app()

        async def override() -> AsyncGenerator[AsyncSession, None]:
            yield session

        app.dependency_overrides[get_db_session] = override

        real_hash = "abc123" + str(uuid.uuid4()).replace("-", "")[:58]
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            resp = await c.post(
                "/api/v1/import/facts",
                json={
                    "facts": [
                        {
                            "id": "hash-test-fact",
                            "content": "Fact with real hash unique " + real_hash[:20],
                            "fact_type": "claim",
                            "created_at": "2025-01-01T00:00:00Z",
                            "sources": [
                                {
                                    "source_id": "hash-source-id",
                                    "uri": "https://example.com/real-hash",
                                    "title": "Real Hash Source",
                                    "provider_id": "brave_search",
                                    "retrieved_at": "2025-01-01T00:00:00Z",
                                    "content_hash": real_hash,
                                    "is_full_text": True,
                                    "content_type": "text/html",
                                }
                            ],
                        }
                    ],
                },
            )
            assert resp.status_code == 200
            data = resp.json()
            assert len(data["imported_facts"]) == 1

            # Verify the source was created with the real hash
            from kt_db.repositories.sources import SourceRepository

            source_repo = SourceRepository(session)
            source = await source_repo.get_by_hash(real_hash)
            assert source is not None
            assert source.uri == "https://example.com/real-hash"
            assert source.is_full_text is True

        await session.rollback()


async def test_import_creates_seeds(api_session_factory):
    """Import nodes should create WriteSeed and WriteSeedFact records."""
    async with api_session_factory() as session:
        app = create_app()

        async def override() -> AsyncGenerator[AsyncSession, None]:
            yield session

        app.dependency_overrides[get_db_session] = override

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            resp = await c.post(
                "/api/v1/import/nodes",
                json={
                    "nodes": [
                        {
                            "id": "seed-test-node",
                            "concept": "Seed Import Test Unique Node QRS",
                            "node_type": "concept",
                            "parent_id": None,
                            "attractor": None,
                            "filter_id": None,
                            "max_content_tokens": 500,
                            "created_at": "2025-01-01T00:00:00Z",
                            "updated_at": "2025-01-01T00:00:00Z",
                            "update_count": 0,
                            "access_count": 0,
                            "richness": 0.0,
                            "convergence_score": 0.0,
                        }
                    ],
                    "facts": [
                        {
                            "id": "seed-test-fact",
                            "content": "Seed test fact content unique QRS",
                            "fact_type": "claim",
                            "created_at": "2025-01-01T00:00:00Z",
                            "sources": [],
                        }
                    ],
                    "node_fact_links": [
                        {
                            "node_id": "seed-test-node",
                            "fact_id": "seed-test-fact",
                            "relevance_score": 0.85,
                        },
                    ],
                    "edges": [],
                },
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["imported_seeds"] == 1
            assert len(data["imported_nodes"]) == 1

        await session.rollback()
