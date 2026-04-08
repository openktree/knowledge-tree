"""Integration tests for the public-cache toggle surface on /api/v1/graphs.

Covers the full PR6 contract:

* ``GraphCreateRequest`` accepts ``contribute_to_public`` /
  ``use_public_cache`` and persists them on the row.
* ``GraphUpdateRequest`` patches them on a non-default graph and
  invalidates the resolver cache so the next workflow resolution sees
  the new values.
* The endpoint **rejects** toggle edits on the default graph with HTTP
  400 (the default graph has no upstream).
* ``GraphResponse`` echoes both fields back so the frontend can render
  the current state.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from kt_api.auth.tokens import require_auth
from kt_api.dependencies import get_db_session, get_graph_session_resolver
from kt_api.main import create_app
from kt_db.models import Graph, GraphMember, User

SUPERUSER_ID = uuid.UUID("00000000-0000-0000-0000-000000000040")


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
async def stub_user(session_factory):
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    async with session_factory() as s:
        stmt = pg_insert(User).values(
            id=SUPERUSER_ID,
            email=f"user-{SUPERUSER_ID}@example.com",
            hashed_password="x",
            is_active=True,
            is_superuser=True,
            is_verified=True,
        )
        await s.execute(stmt.on_conflict_do_nothing(index_elements=[User.id]))
        await s.commit()
    yield


@pytest_asyncio.fixture(scope="function", loop_scope="session")
async def superuser_client(session_factory):
    fake_resolver = AsyncMock()
    fake_resolver.invalidate = AsyncMock(return_value=None)

    app = create_app()

    async def _override_session() -> AsyncGenerator[AsyncSession, None]:
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_db_session] = _override_session
    app.dependency_overrides[require_auth] = lambda: _stub_user(SUPERUSER_ID, is_superuser=True)
    app.dependency_overrides[get_graph_session_resolver] = lambda: fake_resolver

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        # Stash the resolver mock on the client so tests can assert against it.
        c.fake_resolver = fake_resolver  # type: ignore[attr-defined]
        yield c
    app.dependency_overrides.clear()


@pytest_asyncio.fixture(scope="function", loop_scope="session")
async def default_graph(session_factory):
    """Insert a row representing the public default graph.

    The default-graph rejection branch in ``update_graph`` needs an
    actual ``is_default=True`` row to load. Created lazily per-test so
    test isolation stays clean.
    """
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    async with session_factory() as s:
        stmt = pg_insert(Graph).values(
            id=uuid.UUID("00000000-0000-0000-0000-000000000001"),
            slug="default",
            name="Public",
            description=None,
            graph_type="v1",
            byok_enabled=False,
            storage_mode="schema",
            schema_name="public",
            is_default=True,
            status="active",
            contribute_to_public=True,
            use_public_cache=True,
        )
        await s.execute(stmt.on_conflict_do_nothing(index_elements=[Graph.slug]))
        await s.commit()
    yield
    async with session_factory() as s:
        await s.execute(delete(Graph).where(Graph.slug == "default"))
        await s.commit()


@pytest_asyncio.fixture(scope="function", loop_scope="session")
async def cleanup(session_factory):
    yield
    async with session_factory() as s:
        await s.execute(delete(GraphMember))
        await s.execute(delete(Graph).where(Graph.slug.like("toggles_%")))
        await s.commit()


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_graph_defaults_both_toggles_to_true(
    superuser_client: AsyncClient,
    session_factory,
    stub_user,
    cleanup,
):
    """Omitting both toggles must persist them as True (the new column default)."""
    with patch("kt_api.graphs._provision_graph", new=AsyncMock(return_value=None)):
        resp = await superuser_client.post(
            "/api/v1/graphs",
            json={"slug": "toggles_default", "name": "Toggles Default"},
        )

    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["contribute_to_public"] is True
    assert body["use_public_cache"] is True


@pytest.mark.asyncio
async def test_create_graph_persists_explicit_false_toggles(
    superuser_client: AsyncClient,
    session_factory,
    stub_user,
    cleanup,
):
    """Operators must be able to opt a brand-new graph out of either side."""
    with patch("kt_api.graphs._provision_graph", new=AsyncMock(return_value=None)):
        resp = await superuser_client.post(
            "/api/v1/graphs",
            json={
                "slug": "toggles_optout",
                "name": "Toggles Opt-out",
                "contribute_to_public": False,
                "use_public_cache": False,
            },
        )

    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["contribute_to_public"] is False
    assert body["use_public_cache"] is False

    async with session_factory() as s:
        graph = (await s.execute(_select_graph("toggles_optout"))).scalar_one()
        assert graph.contribute_to_public is False
        assert graph.use_public_cache is False


# ---------------------------------------------------------------------------
# Update
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_graph_patches_toggles_and_invalidates_resolver(
    superuser_client: AsyncClient,
    session_factory,
    stub_user,
    cleanup,
):
    """Toggle flips must hit the row AND invalidate the resolver cache.

    Without the invalidation, an in-flight worker would keep using the
    old toggle for as long as the GraphSessions row stays cached — that
    would defeat the purpose of the API surface.
    """
    with patch("kt_api.graphs._provision_graph", new=AsyncMock(return_value=None)):
        await superuser_client.post(
            "/api/v1/graphs",
            json={"slug": "toggles_update", "name": "Toggles Update"},
        )

    superuser_client.fake_resolver.invalidate.reset_mock()  # type: ignore[attr-defined]

    resp = await superuser_client.put(
        "/api/v1/graphs/toggles_update",
        json={"contribute_to_public": False, "use_public_cache": False},
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["contribute_to_public"] is False
    assert body["use_public_cache"] is False

    superuser_client.fake_resolver.invalidate.assert_awaited_once()  # type: ignore[attr-defined]

    # The DB row matches the response.
    async with session_factory() as s:
        graph = (await s.execute(_select_graph("toggles_update"))).scalar_one()
        assert graph.contribute_to_public is False
        assert graph.use_public_cache is False


@pytest.mark.asyncio
async def test_update_graph_name_only_does_not_invalidate(
    superuser_client: AsyncClient,
    session_factory,
    stub_user,
    cleanup,
):
    """Renaming a graph shouldn't churn the resolver cache.

    The resolver invalidation pops the cached engine pools — for non-
    default graphs that means tearing down a connection pool. We only
    want to pay that cost when something the resolver actually
    snapshots changes.
    """
    with patch("kt_api.graphs._provision_graph", new=AsyncMock(return_value=None)):
        await superuser_client.post(
            "/api/v1/graphs",
            json={"slug": "toggles_rename", "name": "Old Name"},
        )

    superuser_client.fake_resolver.invalidate.reset_mock()  # type: ignore[attr-defined]

    resp = await superuser_client.put(
        "/api/v1/graphs/toggles_rename",
        json={"name": "New Name"},
    )

    assert resp.status_code == 200, resp.text
    superuser_client.fake_resolver.invalidate.assert_not_called()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_update_default_graph_rejects_toggle_edit(
    superuser_client: AsyncClient,
    session_factory,
    stub_user,
    default_graph,
):
    """The default graph has no upstream — toggle edits must 400."""
    # The conftest sets up a default graph with slug "default" already.
    resp = await superuser_client.put(
        "/api/v1/graphs/default",
        json={"contribute_to_public": False},
    )

    assert resp.status_code == 400
    assert "default graph" in resp.json()["detail"].lower()
    superuser_client.fake_resolver.invalidate.assert_not_called()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_update_default_graph_rename_still_allowed(
    superuser_client: AsyncClient,
    session_factory,
    stub_user,
    default_graph,
):
    """Renaming the default graph stays a valid operation — only the
    toggle edits are rejected. Otherwise we'd break the existing
    metadata-edit UX for the public graph."""
    resp = await superuser_client.put(
        "/api/v1/graphs/default",
        json={"description": "Public knowledge graph"},
    )

    assert resp.status_code == 200, resp.text


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _select_graph(slug: str):
    from sqlalchemy import select

    return select(Graph).where(Graph.slug == slug)
