"""Integration tests for GET /api/v1/graphs/database-connections.

The endpoint always returns a synthetic ``default`` entry first, followed by
every row from the ``database_connections`` table. Admin-only.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from kt_api.auth.tokens import require_auth
from kt_api.dependencies import get_db_session
from kt_api.main import create_app
from kt_db.models import DatabaseConnection, User
from kt_db.repositories.graphs import GraphRepository

SUPERUSER_ID = uuid.UUID("00000000-0000-0000-0000-000000000020")
REGULAR_USER_ID = uuid.UUID("00000000-0000-0000-0000-000000000021")


def _stub_user(user_id: uuid.UUID, *, is_superuser: bool) -> User:
    user = User()
    user.id = user_id
    user.email = f"user-{user_id}@example.com"
    user.is_active = True
    user.is_superuser = is_superuser
    user.is_verified = True
    return user


@pytest_asyncio.fixture(scope="function", loop_scope="session")
async def session_factory(engine):
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


@pytest_asyncio.fixture(scope="function", loop_scope="session")
async def stub_users_in_db(session_factory):
    """Insert the stub superuser/regular user into the User table so any
    create_graph call that writes ``created_by`` won't trip the FK constraint.
    """
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    async with session_factory() as s:
        for uid, is_super in ((SUPERUSER_ID, True), (REGULAR_USER_ID, False)):
            stmt = pg_insert(User).values(
                id=uid,
                email=f"user-{uid}@example.com",
                hashed_password="x",
                is_active=True,
                is_superuser=is_super,
                is_verified=True,
            )
            await s.execute(stmt.on_conflict_do_nothing(index_elements=[User.id]))
        await s.commit()


def _make_app(session_factory, *, is_superuser: bool, user_id: uuid.UUID):
    application = create_app()

    async def override_get_db_session() -> AsyncGenerator[AsyncSession, None]:
        async with session_factory() as session:
            yield session

    async def override_require_auth() -> User:
        return _stub_user(user_id, is_superuser=is_superuser)

    application.dependency_overrides[get_db_session] = override_get_db_session
    application.dependency_overrides[require_auth] = override_require_auth
    return application


@pytest_asyncio.fixture(scope="function", loop_scope="session")
async def superuser_client(session_factory):
    app = _make_app(session_factory, is_superuser=True, user_id=SUPERUSER_ID)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


@pytest_asyncio.fixture(scope="function", loop_scope="session")
async def regular_client(session_factory):
    app = _make_app(session_factory, is_superuser=False, user_id=REGULAR_USER_ID)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


@pytest_asyncio.fixture(scope="function", loop_scope="session")
async def cleanup_connections(session_factory):
    """Wipe database_connections after each test to keep the schema clean."""
    yield
    async with session_factory() as s:
        await s.execute(delete(DatabaseConnection))
        await s.commit()


@pytest.mark.asyncio
async def test_returns_only_default_when_table_empty(superuser_client: AsyncClient, cleanup_connections):
    resp = await superuser_client.get("/api/v1/graphs/database-connections")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["config_key"] == "default"
    assert body[0]["name"] == "default"
    assert body[0]["id"] is None
    assert body[0]["created_at"] is None


@pytest.mark.asyncio
async def test_returns_default_first_then_rows(
    superuser_client: AsyncClient,
    session_factory,
    cleanup_connections,
):
    # Insert in non-alphabetical order — repo orders by name
    async with session_factory() as s:
        repo = GraphRepository(s)
        await repo.create_database_connection(name="Zebra", config_key="zebra")
        await repo.create_database_connection(name="Apple", config_key="apple")
        await s.commit()

    resp = await superuser_client.get("/api/v1/graphs/database-connections")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 3
    assert body[0]["config_key"] == "default"
    assert body[0]["id"] is None
    assert body[1]["config_key"] == "apple"
    assert body[1]["name"] == "Apple"
    assert body[1]["id"] is not None
    assert body[2]["config_key"] == "zebra"


@pytest.mark.asyncio
async def test_blocked_for_non_admin(regular_client: AsyncClient):
    """Non-superusers must not be able to list database connections."""
    resp = await regular_client.get("/api/v1/graphs/database-connections")
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_create_database_connection_rejects_default_key(session_factory):
    """The repository must reject the reserved 'default' config_key."""
    async with session_factory() as s:
        repo = GraphRepository(s)
        with pytest.raises(ValueError, match="reserved"):
            await repo.create_database_connection(name="Bad", config_key="default")


@pytest.mark.asyncio
async def test_create_graph_with_default_key_uses_system_db(
    superuser_client: AsyncClient,
    session_factory,
    stub_users_in_db,
):
    """Passing ``database_connection_config_key="default"`` (the magic string)
    must store the graph with ``database_connection_id=NULL`` — i.e. the
    system DB. Sanity check the create-graph branch that treats "default"
    specially.
    """
    from unittest.mock import AsyncMock, patch

    from sqlalchemy import delete

    from kt_db.models import Graph

    # Patch the synchronous schema/alembic/qdrant work — we only want to
    # exercise the resolution branch in create_graph, not actually provision.
    with patch("kt_api.graphs._provision_graph", new=AsyncMock(return_value=None)):
        resp = await superuser_client.post(
            "/api/v1/graphs",
            json={
                "slug": "default_key_test",
                "name": "Default Key Test",
                "database_connection_config_key": "default",
            },
        )

    assert resp.status_code == 201, resp.text
    body = resp.json()
    # The graph row must NOT reference any DatabaseConnection
    assert body["database_connection_id"] is None
    assert body["database_connection_name"] is None

    # Cleanup so the test is repeatable
    async with session_factory() as s:
        await s.execute(delete(Graph).where(Graph.slug == "default_key_test"))
        await s.commit()
