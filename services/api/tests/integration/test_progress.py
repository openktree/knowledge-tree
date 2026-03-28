"""Integration tests for progress and report endpoints."""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from kt_api.dependencies import get_db_session
from kt_api.main import create_app
from kt_db.models import Conversation, ConversationMessage, ResearchReport

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
async def conversation_with_message(session_factory):
    """Create a conversation with an assistant message that has a workflow_run_id."""
    async with session_factory() as session:
        async with session.begin():
            conv = Conversation(id=uuid.uuid4(), title="test", mode="research")
            session.add(conv)
            await session.flush()

            msg = ConversationMessage(
                id=uuid.uuid4(),
                conversation_id=conv.id,
                turn_number=1,
                role="assistant",
                content="",
                status="running",
                workflow_run_id="hatchet-run-123",
                explore_budget=50,
            )
            session.add(msg)
            await session.flush()

        yield conv, msg


@pytest_asyncio.fixture()
async def conversation_with_report(session_factory):
    """Create a conversation with a completed message and a research report."""
    async with session_factory() as session:
        async with session.begin():
            conv = Conversation(id=uuid.uuid4(), title="test report", mode="research")
            session.add(conv)
            await session.flush()

            msg = ConversationMessage(
                id=uuid.uuid4(),
                conversation_id=conv.id,
                turn_number=1,
                role="assistant",
                content="done",
                status="completed",
                workflow_run_id="hatchet-run-456",
            )
            session.add(msg)
            await session.flush()

            report = ResearchReport(
                id=uuid.uuid4(),
                message_id=msg.id,
                conversation_id=conv.id,
                nodes_created=5,
                edges_created=3,
                waves_completed=2,
                explore_budget=50,
                explore_used=30,
                nav_budget=10,
                nav_used=8,
                scope_summaries=["Explored topic A"],
                total_prompt_tokens=1000,
                total_completion_tokens=500,
                total_cost_usd=0.05,
            )
            session.add(report)
            await session.flush()

        yield conv, msg


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_hatchet_details(
    run_status: str = "RUNNING",
    tasks: list | None = None,
    error_message: str | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        run=SimpleNamespace(
            status=SimpleNamespace(value=run_status),
            error_message=error_message,
        ),
        tasks=tasks or [],
    )


def _make_hatchet_task(
    task_external_id: str = "task-1",
    display_name: str = "create_node",
    status: str = "RUNNING",
    duration: int | None = None,
    started_at: object | None = None,
    children: list | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        task_external_id=task_external_id,
        display_name=display_name,
        status=SimpleNamespace(value=status),
        duration=duration,
        started_at=started_at,
        children=children,
    )


# ---------------------------------------------------------------------------
# GET /conversations/{id}/messages/{msgId}/progress
# ---------------------------------------------------------------------------


class TestGetMessageProgress:
    async def test_returns_progress_with_hatchet_tasks(self, client: AsyncClient, conversation_with_message):
        conv, msg = conversation_with_message
        hatchet_details = _make_hatchet_details(
            run_status="RUNNING",
            tasks=[
                _make_hatchet_task(status="COMPLETED", display_name="create_node", duration=1200),
                _make_hatchet_task(task_external_id="task-2", status="RUNNING", display_name="dimensions"),
            ],
        )

        with patch(
            "kt_hatchet.client.get_workflow_run_details",
            new_callable=AsyncMock,
            return_value=hatchet_details,
        ):
            resp = await client.get(f"/api/v1/conversations/{conv.id}/messages/{msg.id}/progress")

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "running"
        assert data["message_id"] == str(msg.id)
        assert data["explore_budget"] == 50
        assert len(data["tasks"]) == 2
        assert data["tasks"][0]["status"] == "SUCCEEDED"
        assert data["tasks"][0]["display_name"] == "create_node"
        assert data["tasks"][0]["duration_ms"] == 1200
        assert data["tasks"][1]["status"] == "RUNNING"

    async def test_fallback_to_db_when_hatchet_unavailable(self, client: AsyncClient, conversation_with_message):
        conv, msg = conversation_with_message

        with patch(
            "kt_hatchet.client.get_workflow_run_details",
            new_callable=AsyncMock,
            side_effect=RuntimeError("Hatchet down"),
        ):
            resp = await client.get(f"/api/v1/conversations/{conv.id}/messages/{msg.id}/progress")

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "running"  # Falls back to DB status
        assert data["tasks"] == []

    async def test_not_found_for_nonexistent_message(self, client: AsyncClient):
        fake_conv = str(uuid.uuid4())
        fake_msg = str(uuid.uuid4())
        resp = await client.get(f"/api/v1/conversations/{fake_conv}/messages/{fake_msg}/progress")
        assert resp.status_code == 404

    async def test_not_found_for_wrong_conversation(self, client: AsyncClient, conversation_with_message):
        _, msg = conversation_with_message
        wrong_conv = str(uuid.uuid4())
        resp = await client.get(f"/api/v1/conversations/{wrong_conv}/messages/{msg.id}/progress")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# GET /conversations/{id}/messages/{msgId}/report
# ---------------------------------------------------------------------------


class TestGetMessageReport:
    async def test_returns_report(self, client: AsyncClient, conversation_with_report):
        conv, msg = conversation_with_report
        resp = await client.get(f"/api/v1/conversations/{conv.id}/messages/{msg.id}/report")
        assert resp.status_code == 200
        data = resp.json()
        assert data["message_id"] == str(msg.id)
        assert data["nodes_created"] == 5
        assert data["edges_created"] == 3
        assert data["waves_completed"] == 2
        assert data["explore_budget"] == 50
        assert data["explore_used"] == 30
        assert data["scope_summaries"] == ["Explored topic A"]
        assert data["total_cost_usd"] == pytest.approx(0.05)

    async def test_not_found_for_nonexistent_report(self, client: AsyncClient):
        fake_conv = str(uuid.uuid4())
        fake_msg = str(uuid.uuid4())
        resp = await client.get(f"/api/v1/conversations/{fake_conv}/messages/{fake_msg}/report")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# GET /workflows/{workflow_run_id}/progress
# ---------------------------------------------------------------------------


class TestGetWorkflowProgress:
    async def test_returns_workflow_progress(self, client: AsyncClient):
        hatchet_details = _make_hatchet_details(
            run_status="COMPLETED",
            tasks=[
                _make_hatchet_task(status="COMPLETED", display_name="synthesize", duration=5000),
            ],
        )

        with patch(
            "kt_hatchet.client.get_workflow_run_details",
            new_callable=AsyncMock,
            return_value=hatchet_details,
        ):
            resp = await client.get("/api/v1/workflows/run-789/progress")

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "completed"
        assert data["workflow_run_id"] == "run-789"
        assert len(data["tasks"]) == 1
        assert data["tasks"][0]["status"] == "SUCCEEDED"

    async def test_502_when_hatchet_unavailable(self, client: AsyncClient):
        with patch(
            "kt_hatchet.client.get_workflow_run_details",
            new_callable=AsyncMock,
            side_effect=RuntimeError("Hatchet down"),
        ):
            resp = await client.get("/api/v1/workflows/run-bad/progress")

        assert resp.status_code == 502
