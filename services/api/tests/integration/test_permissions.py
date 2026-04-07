"""Integration tests for kt-rbac FastAPI adapter (require_system_permission, require_graph_permission)."""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from kt_api.auth.tokens import require_auth
from kt_api.dependencies import get_db_session
from kt_api.main import create_app
from kt_db.models import User

# Distinct test user IDs (different from SKIP_AUTH stub)
SUPERUSER_ID = uuid.UUID("00000000-0000-0000-0000-000000000010")
REGULAR_USER_ID = uuid.UUID("00000000-0000-0000-0000-000000000011")


def _make_stub_user(user_id: uuid.UUID, *, is_superuser: bool) -> User:
    user = User()
    user.id = user_id
    user.email = f"user-{user_id}@example.com"
    user.is_active = True
    user.is_superuser = is_superuser
    user.is_verified = True
    return user


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def perm_session_factory(engine):
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


@pytest_asyncio.fixture(scope="function", loop_scope="session")
async def regular_user_app(perm_session_factory):
    """App with require_auth overridden to return a non-superuser."""
    application = create_app()

    async def override_get_db_session() -> AsyncGenerator[AsyncSession, None]:
        async with perm_session_factory() as session:
            yield session

    async def override_require_auth() -> User:
        return _make_stub_user(REGULAR_USER_ID, is_superuser=False)

    application.dependency_overrides[get_db_session] = override_get_db_session
    application.dependency_overrides[require_auth] = override_require_auth
    yield application
    application.dependency_overrides.clear()


@pytest_asyncio.fixture(scope="function", loop_scope="session")
async def superuser_app(perm_session_factory):
    """App with require_auth overridden to return a superuser."""
    application = create_app()

    async def override_get_db_session() -> AsyncGenerator[AsyncSession, None]:
        async with perm_session_factory() as session:
            yield session

    async def override_require_auth() -> User:
        return _make_stub_user(SUPERUSER_ID, is_superuser=True)

    application.dependency_overrides[get_db_session] = override_get_db_session
    application.dependency_overrides[require_auth] = override_require_auth
    yield application
    application.dependency_overrides.clear()


@pytest_asyncio.fixture(scope="function", loop_scope="session")
async def regular_client(regular_user_app):
    transport = ASGITransport(app=regular_user_app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest_asyncio.fixture(scope="function", loop_scope="session")
async def superuser_client(superuser_app):
    transport = ASGITransport(app=superuser_app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


# ── require_system_permission ──────────────────────────────────────


class TestRequireSystemPermission:
    """Test that system-level permission endpoints reject non-superusers."""

    async def test_admin_reindex_blocked_for_regular_user(self, regular_client: AsyncClient):
        """CRITICAL: /api/v1/admin/reindex must require SYSTEM_ADMIN_OPS."""
        resp = await regular_client.post("/api/v1/admin/reindex")
        assert resp.status_code == 403
        assert "system:admin_ops" in resp.json()["detail"]

    async def test_admin_refresh_stale_blocked_for_regular_user(self, regular_client: AsyncClient):
        resp = await regular_client.post("/api/v1/admin/refresh-stale")
        assert resp.status_code == 403
        assert "system:admin_ops" in resp.json()["detail"]

    async def test_members_list_blocked_for_regular_user(self, regular_client: AsyncClient):
        """SYSTEM_MANAGE_USERS required for /api/v1/members."""
        resp = await regular_client.get("/api/v1/members")
        assert resp.status_code == 403
        assert "system:manage_users" in resp.json()["detail"]

    async def test_system_settings_get_blocked_for_regular_user(self, regular_client: AsyncClient):
        """SYSTEM_MANAGE_SETTINGS required."""
        resp = await regular_client.get("/api/v1/system-settings")
        assert resp.status_code == 403
        assert "system:manage_settings" in resp.json()["detail"]

    async def test_invites_list_blocked_for_regular_user(self, regular_client: AsyncClient):
        """SYSTEM_MANAGE_INVITES required."""
        resp = await regular_client.get("/api/v1/invites")
        assert resp.status_code == 403
        assert "system:manage_invites" in resp.json()["detail"]

    async def test_create_graph_blocked_for_regular_user(self, regular_client: AsyncClient):
        """SYSTEM_MANAGE_GRAPHS required to create a graph."""
        resp = await regular_client.post(
            "/api/v1/graphs",
            json={
                "slug": "test_graph_perm",
                "name": "Test",
            },
        )
        assert resp.status_code == 403
        assert "system:manage_graphs" in resp.json()["detail"]

    async def test_database_connections_list_blocked_for_regular_user(self, regular_client: AsyncClient):
        """SYSTEM_MANAGE_GRAPHS required for /api/v1/graphs/database-connections."""
        resp = await regular_client.get("/api/v1/graphs/database-connections")
        assert resp.status_code == 403
        assert "system:manage_graphs" in resp.json()["detail"]

    async def test_usage_summary_blocked_for_regular_user(self, regular_client: AsyncClient):
        """SYSTEM_ADMIN_OPS required for usage endpoints."""
        resp = await regular_client.get("/api/v1/usage/summary")
        assert resp.status_code == 403
        assert "system:admin_ops" in resp.json()["detail"]


class TestSuperuserBypass:
    """Test that superusers can access all system permission endpoints."""

    async def test_admin_reindex_allowed_for_superuser(self, superuser_client: AsyncClient):
        # Note: may return 200 with 'no nodes' or fail on Qdrant — we just verify auth passes
        resp = await superuser_client.post("/api/v1/admin/reindex")
        assert resp.status_code != 403

    async def test_members_list_allowed_for_superuser(self, superuser_client: AsyncClient):
        resp = await superuser_client.get("/api/v1/members")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    async def test_invites_list_allowed_for_superuser(self, superuser_client: AsyncClient):
        resp = await superuser_client.get("/api/v1/invites")
        assert resp.status_code == 200

    async def test_database_connections_list_allowed_for_superuser(self, superuser_client: AsyncClient):
        resp = await superuser_client.get("/api/v1/graphs/database-connections")
        assert resp.status_code == 200


# ── require_graph_permission via _require_graph_access ──────────────


class TestGraphAccessHelper:
    """Test graph endpoints that use _require_graph_access (member management, metadata)."""

    async def test_get_nonexistent_graph_returns_404(self, regular_client: AsyncClient):
        resp = await regular_client.get("/api/v1/graphs/nonexistent_slug_xyz")
        assert resp.status_code == 404

    async def test_update_graph_blocked_for_non_member(self, regular_client: AsyncClient):
        """Updating a graph the user isn't a member of returns 403 (not a member)."""
        resp = await regular_client.put(
            "/api/v1/graphs/some_graph",
            json={"name": "Renamed"},
        )
        # 404 (graph doesn't exist) or 403 (not a member) — both are valid
        assert resp.status_code in (403, 404)
