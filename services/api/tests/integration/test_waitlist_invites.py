"""Integration tests for waitlist and invite endpoints."""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator
from unittest.mock import patch

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from kt_api.dependencies import get_db_session
from kt_api.main import create_app
from kt_db.models import User

# The SKIP_AUTH stub user UUID
STUB_USER_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def wi_session_factory(engine):
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


@pytest_asyncio.fixture(scope="session", loop_scope="session", autouse=True)
async def _ensure_stub_user(wi_session_factory):
    """Ensure the SKIP_AUTH stub user exists in the DB for FK constraints."""
    async with wi_session_factory() as session:
        from sqlalchemy import select
        from sqlalchemy.dialects.postgresql import insert as pg_insert

        existing = await session.execute(select(User).where(User.id == STUB_USER_ID))
        if existing.scalar_one_or_none() is None:
            stmt = (
                pg_insert(User)
                .values(
                    id=STUB_USER_ID,
                    email="test@example.com",
                    hashed_password="stub",
                    is_active=True,
                    is_superuser=True,
                    is_verified=True,
                )
                .on_conflict_do_nothing()
            )
            await session.execute(stmt)
            await session.commit()


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def wi_app(wi_session_factory, _ensure_stub_user):
    application = create_app()

    async def override_get_db_session() -> AsyncGenerator[AsyncSession, None]:
        async with wi_session_factory() as session:
            yield session

    application.dependency_overrides[get_db_session] = override_get_db_session
    return application


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def wi_client(wi_app):
    transport = ASGITransport(app=wi_app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


# ── Waitlist ──────────────────────────────────────────────────────


async def test_waitlist_submit_rejected_when_registration_open(wi_client: AsyncClient):
    """Waitlist submission should fail when registration is open."""
    resp = await wi_client.post(
        "/api/v1/waitlist",
        json={"email": "test@example.com"},
    )
    # Registration is open by default (SKIP_AUTH mode), so waitlist should reject
    assert resp.status_code == 400
    assert "register directly" in resp.json()["detail"].lower()


async def test_waitlist_submit_succeeds_when_registration_disabled(wi_client: AsyncClient):
    """Waitlist submission should succeed when registration is disabled."""
    with patch("kt_api.waitlist.get_settings") as mock_settings:
        mock_settings.return_value.disable_self_registration = True
        resp = await wi_client.post(
            "/api/v1/waitlist",
            json={
                "email": "waitlist@example.com",
                "display_name": "Test User",
                "message": "I want access please",
            },
        )
    assert resp.status_code == 200
    assert resp.json()["status"] == "submitted"


async def test_waitlist_duplicate_pending_rejected(wi_client: AsyncClient):
    """Duplicate pending waitlist entry should be rejected."""
    email = f"dup-{uuid.uuid4().hex[:6]}@example.com"
    with patch("kt_api.waitlist.get_settings") as mock_settings:
        mock_settings.return_value.disable_self_registration = True
        # First submission
        resp = await wi_client.post("/api/v1/waitlist", json={"email": email})
        assert resp.status_code == 200
        # Second submission — should fail
        resp = await wi_client.post("/api/v1/waitlist", json={"email": email})
        assert resp.status_code == 409


async def test_waitlist_list_requires_admin(wi_client: AsyncClient):
    """GET /waitlist requires admin auth (SKIP_AUTH gives admin)."""
    resp = await wi_client.get("/api/v1/waitlist")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


async def test_waitlist_review_approve_creates_invite(wi_client: AsyncClient):
    """Approving a waitlist entry should auto-create an invite."""
    email = f"approve-{uuid.uuid4().hex[:6]}@example.com"
    # Create entry
    with patch("kt_api.waitlist.get_settings") as mock_settings:
        mock_settings.return_value.disable_self_registration = True
        resp = await wi_client.post(
            "/api/v1/waitlist",
            json={"email": email, "display_name": "Approvee"},
        )
    assert resp.status_code == 200

    # List and find entry
    resp = await wi_client.get("/api/v1/waitlist?status=pending")
    entries = resp.json()
    entry = next((e for e in entries if e["email"] == email), None)
    assert entry is not None

    # Approve
    resp = await wi_client.patch(
        f"/api/v1/waitlist/{entry['id']}",
        json={"status": "approved"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["entry"]["status"] == "approved"
    assert data["invite"] is not None
    assert data["invite"]["email"] == email
    assert len(data["invite"]["code"]) > 0


async def test_waitlist_review_reject(wi_client: AsyncClient):
    """Rejecting a waitlist entry should not create an invite."""
    email = f"reject-{uuid.uuid4().hex[:6]}@example.com"
    with patch("kt_api.waitlist.get_settings") as mock_settings:
        mock_settings.return_value.disable_self_registration = True
        resp = await wi_client.post("/api/v1/waitlist", json={"email": email})
    assert resp.status_code == 200

    resp = await wi_client.get("/api/v1/waitlist?status=pending")
    entry = next((e for e in resp.json() if e["email"] == email), None)
    assert entry is not None

    resp = await wi_client.patch(
        f"/api/v1/waitlist/{entry['id']}",
        json={"status": "rejected"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["entry"]["status"] == "rejected"
    assert data["invite"] is None


# ── Invites ───────────────────────────────────────────────────────


async def test_invite_create(wi_client: AsyncClient):
    """Admin should be able to create an invite."""
    resp = await wi_client.post(
        "/api/v1/invites",
        json={"email": "invited@example.com", "expires_in_days": 7},
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["email"] == "invited@example.com"
    assert len(data["code"]) > 0
    assert data["redeemed_at"] is None


async def test_invite_list(wi_client: AsyncClient):
    """Admin should be able to list invites."""
    resp = await wi_client.get("/api/v1/invites")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


async def test_invite_validate_valid(wi_client: AsyncClient):
    """Validating a correct email+code should return valid=True."""
    # Create invite
    resp = await wi_client.post(
        "/api/v1/invites",
        json={"email": f"valid-{uuid.uuid4().hex[:6]}@example.com"},
    )
    invite = resp.json()

    # Validate
    resp = await wi_client.post(
        "/api/v1/invites/validate",
        json={"email": invite["email"], "code": invite["code"]},
    )
    assert resp.status_code == 200
    assert resp.json()["valid"] is True


async def test_invite_validate_wrong_code(wi_client: AsyncClient):
    """Validating with a wrong code should return valid=False."""
    resp = await wi_client.post(
        "/api/v1/invites/validate",
        json={"email": "nobody@example.com", "code": "wrong-code"},
    )
    assert resp.status_code == 200
    assert resp.json()["valid"] is False


async def test_invite_validate_wrong_email(wi_client: AsyncClient):
    """Validating with a wrong email should return valid=False."""
    # Create invite for one email
    resp = await wi_client.post(
        "/api/v1/invites",
        json={"email": f"real-{uuid.uuid4().hex[:6]}@example.com"},
    )
    invite = resp.json()

    # Try to validate with different email
    resp = await wi_client.post(
        "/api/v1/invites/validate",
        json={"email": "other@example.com", "code": invite["code"]},
    )
    assert resp.status_code == 200
    assert resp.json()["valid"] is False


async def test_invite_revoke(wi_client: AsyncClient):
    """Admin should be able to revoke an unredeemed invite."""
    resp = await wi_client.post(
        "/api/v1/invites",
        json={"email": f"revoke-{uuid.uuid4().hex[:6]}@example.com"},
    )
    invite = resp.json()

    # Revoke
    resp = await wi_client.delete(f"/api/v1/invites/{invite['id']}")
    assert resp.status_code == 204

    # Validate should now fail
    resp = await wi_client.post(
        "/api/v1/invites/validate",
        json={"email": invite["email"], "code": invite["code"]},
    )
    assert resp.json()["valid"] is False


# ── Registration status ──────────────────────────────────────────


async def test_registration_status_includes_waitlist_enabled(wi_client: AsyncClient):
    """Registration status should include waitlist_enabled field."""
    resp = await wi_client.get("/api/v1/auth/registration-status")
    assert resp.status_code == 200
    data = resp.json()
    assert "waitlist_enabled" in data
