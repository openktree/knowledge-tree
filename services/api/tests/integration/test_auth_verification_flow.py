"""End-to-end tests for the verification login gate.

Builds a standalone FastAPI app that wires fastapi-users with
`requires_verification=True`, so we can exercise the real 400
`LOGIN_USER_NOT_VERIFIED` path without reloading `kt_api.router`
(its flag is captured at module import time).
"""

from __future__ import annotations

from collections.abc import AsyncGenerator

import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from kt_api.auth._fastapi_users import fastapi_users
from kt_api.auth.backend import auth_backend
from kt_api.auth.schemas import UserCreate, UserRead
from kt_api.dependencies import get_db_session


@pytest_asyncio.fixture(loop_scope="session")
async def verification_app(engine) -> AsyncGenerator[FastAPI, None]:
    """FastAPI app with verification-gated login + register + verify routers."""
    app = FastAPI()
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def override_get_db_session() -> AsyncGenerator[AsyncSession, None]:
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_db_session] = override_get_db_session

    app.include_router(
        fastapi_users.get_register_router(UserRead, UserCreate),
        prefix="/auth",
    )
    app.include_router(fastapi_users.get_verify_router(UserRead), prefix="/auth")
    app.include_router(
        fastapi_users.get_auth_router(auth_backend, requires_verification=True),
        prefix="/auth",
    )
    yield app


@pytest_asyncio.fixture(loop_scope="session")
async def verification_client(verification_app) -> AsyncGenerator[AsyncClient, None]:
    transport = ASGITransport(app=verification_app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def _register(client: AsyncClient, email: str, password: str) -> dict:
    resp = await client.post(
        "/auth/register",
        json={"email": email, "password": password},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _login(client: AsyncClient, email: str, password: str):
    return await client.post(
        "/auth/login",
        data={"username": email, "password": password},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )


async def test_register_then_login_blocked_when_unverified(verification_client: AsyncClient) -> None:
    """Baseline contract: unverified user can't log in when gate is on."""
    await _register(verification_client, "gated-user@example.com", "hunter2hunter2")

    resp = await _login(verification_client, "gated-user@example.com", "hunter2hunter2")

    assert resp.status_code == 400, resp.text
    assert resp.json()["detail"] == "LOGIN_USER_NOT_VERIFIED"


async def test_request_verify_token_does_not_leak_existence(verification_client: AsyncClient) -> None:
    """Fastapi-users returns 202 even for unknown emails (anti-enumeration)."""
    resp = await verification_client.post(
        "/auth/request-verify-token",
        json={"email": "does-not-exist@example.com"},
    )
    assert resp.status_code == 202


async def test_bad_credentials_still_401_not_verification_error(verification_client: AsyncClient) -> None:
    """A wrong password for an unverified user must still say bad credentials
    (not leak that the user exists but is unverified)."""
    await _register(verification_client, "wrongpass-user@example.com", "correct-horse-battery")

    resp = await _login(verification_client, "wrongpass-user@example.com", "nope-nope-nope")
    assert resp.status_code == 400
    assert resp.json()["detail"] == "LOGIN_BAD_CREDENTIALS"
