"""Tests for MCP OAuth 2.1 provider and login flow."""

from __future__ import annotations

import secrets
import time
from unittest.mock import AsyncMock, MagicMock, patch

import bcrypt
import pytest


def _hash_password(plain: str) -> str:
    """Hash a password with bcrypt (matching fastapi-users format)."""
    return bcrypt.hashpw(plain.encode(), bcrypt.gensalt()).decode()


# ── oauth_login._verify_password ──────────────────────────────────────


class TestVerifyPassword:
    def test_correct_password(self):
        from kt_mcp.oauth_login import _verify_password

        hashed = _hash_password("secret123")
        assert _verify_password("secret123", hashed) is True

    def test_wrong_password(self):
        from kt_mcp.oauth_login import _verify_password

        hashed = _hash_password("secret123")
        assert _verify_password("wrong", hashed) is False

    def test_invalid_hash(self):
        from kt_mcp.oauth_login import _verify_password

        assert _verify_password("anything", "not_a_hash") is False

    def test_empty_password(self):
        from kt_mcp.oauth_login import _verify_password

        hashed = _hash_password("")
        assert _verify_password("", hashed) is True
        assert _verify_password("notempty", hashed) is False


# ── OAuthProvider unit tests ─────────────────────────────────────────


def _make_mock_session():
    """Create a mock async session that works as an async context manager."""
    session = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    return session


def _make_mock_factory(session):
    """Create a mock session factory."""
    factory = MagicMock()
    factory.return_value = session
    return factory


class TestOAuthProviderGetClient:
    @pytest.mark.asyncio
    async def test_returns_none_for_unknown_client(self):
        from kt_mcp.oauth_provider import KnowledgeTreeOAuthProvider

        session = _make_mock_session()
        session.get = AsyncMock(return_value=None)

        with patch("kt_mcp.oauth_provider.get_session_factory_cached", return_value=_make_mock_factory(session)):
            provider = KnowledgeTreeOAuthProvider.__new__(KnowledgeTreeOAuthProvider)
            result = await provider.get_client("nonexistent")

        assert result is None

    @pytest.mark.asyncio
    async def test_returns_client_info(self):
        from kt_mcp.oauth_provider import KnowledgeTreeOAuthProvider

        row = MagicMock()
        row.client_id = "test_client"
        row.client_secret = "test_secret"
        row.client_id_issued_at = 1000
        row.client_secret_expires_at = None
        row.metadata_ = {
            "redirect_uris": ["http://localhost/callback"],
            "grant_types": ["authorization_code"],
            "response_types": ["code"],
            "token_endpoint_auth_method": "client_secret_post",
        }

        session = _make_mock_session()
        session.get = AsyncMock(return_value=row)

        with patch("kt_mcp.oauth_provider.get_session_factory_cached", return_value=_make_mock_factory(session)):
            provider = KnowledgeTreeOAuthProvider.__new__(KnowledgeTreeOAuthProvider)
            result = await provider.get_client("test_client")

        assert result is not None
        assert result.client_id == "test_client"
        assert result.client_secret == "test_secret"


class TestOAuthProviderRegisterClient:
    @pytest.mark.asyncio
    async def test_register_client_persists(self):
        from mcp.shared.auth import OAuthClientInformationFull
        from pydantic import AnyHttpUrl

        from kt_mcp.oauth_provider import KnowledgeTreeOAuthProvider

        session = _make_mock_session()
        session.merge = AsyncMock()
        session.commit = AsyncMock()

        client_info = OAuthClientInformationFull(
            client_id="new_client",
            client_secret="new_secret",
            client_id_issued_at=int(time.time()),
            redirect_uris=[AnyHttpUrl("http://localhost/callback")],
            grant_types=["authorization_code"],
            response_types=["code"],
            token_endpoint_auth_method="client_secret_post",
        )

        with patch("kt_mcp.oauth_provider.get_session_factory_cached", return_value=_make_mock_factory(session)):
            provider = KnowledgeTreeOAuthProvider.__new__(KnowledgeTreeOAuthProvider)
            provider.client_registration_options = None
            await provider.register_client(client_info)

        session.merge.assert_called_once()
        session.commit.assert_called_once()

    @pytest.mark.asyncio
    async def test_register_client_requires_client_id(self):
        from mcp.shared.auth import OAuthClientInformationFull
        from pydantic import AnyHttpUrl

        from kt_mcp.oauth_provider import KnowledgeTreeOAuthProvider

        client_info = OAuthClientInformationFull(
            client_id=None,
            redirect_uris=[AnyHttpUrl("http://localhost/callback")],
            grant_types=["authorization_code"],
            response_types=["code"],
            token_endpoint_auth_method="client_secret_post",
        )

        session = _make_mock_session()
        with patch("kt_mcp.oauth_provider.get_session_factory_cached", return_value=_make_mock_factory(session)):
            provider = KnowledgeTreeOAuthProvider.__new__(KnowledgeTreeOAuthProvider)
            with pytest.raises(ValueError, match="client_id is required"):
                await provider.register_client(client_info)


class TestOAuthProviderAuthorize:
    @pytest.mark.asyncio
    async def test_authorize_creates_pending_code(self):
        from mcp.server.auth.provider import AuthorizationParams
        from mcp.shared.auth import OAuthClientInformationFull
        from pydantic import AnyHttpUrl, AnyUrl

        from kt_mcp.oauth_provider import KnowledgeTreeOAuthProvider

        session = _make_mock_session()
        session.add = MagicMock()
        session.commit = AsyncMock()

        client = OAuthClientInformationFull(
            client_id="test_client",
            redirect_uris=[AnyHttpUrl("http://localhost/callback")],
            grant_types=["authorization_code"],
            response_types=["code"],
            token_endpoint_auth_method="client_secret_post",
        )

        params = AuthorizationParams(
            state="test_state",
            scopes=["read"],
            code_challenge="challenge123",
            redirect_uri=AnyUrl("http://localhost/callback"),
            redirect_uri_provided_explicitly=True,
        )

        mock_settings = MagicMock()
        mock_settings.mcp_oauth_base_url = "http://localhost:8001"

        with (
            patch("kt_mcp.oauth_provider.get_session_factory_cached", return_value=_make_mock_factory(session)),
            patch("kt_mcp.oauth_provider.get_settings", return_value=mock_settings),
        ):
            provider = KnowledgeTreeOAuthProvider.__new__(KnowledgeTreeOAuthProvider)
            redirect_url = await provider.authorize(client, params)

        assert "/oauth/login?code_id=pending_" in redirect_url
        session.add.assert_called_once()
        session.commit.assert_called_once()


class TestOAuthProviderVerifyToken:
    @pytest.mark.asyncio
    async def test_verify_valid_oauth_token(self):
        from kt_mcp.oauth_provider import KnowledgeTreeOAuthProvider

        row = MagicMock()
        row.token = "valid_token"
        row.client_id = "client1"
        row.scopes = []
        row.expires_at = int(time.time()) + 3600

        session = _make_mock_session()
        session.get = AsyncMock(return_value=row)

        with patch("kt_mcp.oauth_provider.get_session_factory_cached", return_value=_make_mock_factory(session)):
            provider = KnowledgeTreeOAuthProvider.__new__(KnowledgeTreeOAuthProvider)
            result = await provider.verify_token("valid_token")

        assert result is not None
        assert result.token == "valid_token"
        assert result.client_id == "client1"

    @pytest.mark.asyncio
    async def test_verify_expired_token_returns_none(self):
        from kt_mcp.oauth_provider import KnowledgeTreeOAuthProvider

        row = MagicMock()
        row.token = "expired_token"
        row.client_id = "client1"
        row.scopes = []
        row.expires_at = int(time.time()) - 100  # expired

        session = _make_mock_session()
        session.get = AsyncMock(return_value=row)
        session.delete = AsyncMock()
        session.commit = AsyncMock()

        mock_settings = MagicMock()
        mock_settings.skip_auth = False

        # Second session for legacy token check
        session2 = _make_mock_session()
        session2.get = AsyncMock(return_value=None)

        call_count = 0
        original_factory = _make_mock_factory(session)

        def factory_side_effect():
            nonlocal call_count
            call_count += 1
            if call_count <= 1:
                return session
            return session2

        mock_factory = MagicMock(side_effect=factory_side_effect)

        with (
            patch("kt_mcp.oauth_provider.get_session_factory_cached", return_value=mock_factory),
            patch("kt_mcp.oauth_provider.get_settings", return_value=mock_settings),
            patch("kt_mcp.oauth_provider.verify_bearer_token", new_callable=AsyncMock, return_value=False),
        ):
            provider = KnowledgeTreeOAuthProvider.__new__(KnowledgeTreeOAuthProvider)
            result = await provider.verify_token("expired_token")

        assert result is None

    @pytest.mark.asyncio
    async def test_verify_falls_back_to_legacy_token(self):
        from kt_mcp.oauth_provider import KnowledgeTreeOAuthProvider

        # First call: OAuth token not found
        session1 = _make_mock_session()
        session1.get = AsyncMock(return_value=None)

        # Second call: legacy token check
        session2 = _make_mock_session()

        call_count = 0

        def factory_side_effect():
            nonlocal call_count
            call_count += 1
            if call_count <= 1:
                return session1
            return session2

        mock_factory = MagicMock(side_effect=factory_side_effect)
        mock_settings = MagicMock()
        mock_settings.skip_auth = False

        with (
            patch("kt_mcp.oauth_provider.get_session_factory_cached", return_value=mock_factory),
            patch("kt_mcp.oauth_provider.get_settings", return_value=mock_settings),
            patch("kt_mcp.oauth_provider.verify_bearer_token", new_callable=AsyncMock, return_value=True),
        ):
            provider = KnowledgeTreeOAuthProvider.__new__(KnowledgeTreeOAuthProvider)
            result = await provider.verify_token("tokn_legacy123")

        assert result is not None
        assert result.client_id == "api_token"

    @pytest.mark.asyncio
    async def test_verify_skip_auth_mode(self):
        from kt_mcp.oauth_provider import KnowledgeTreeOAuthProvider

        # OAuth token not found
        session = _make_mock_session()
        session.get = AsyncMock(return_value=None)

        mock_settings = MagicMock()
        mock_settings.skip_auth = True

        with (
            patch("kt_mcp.oauth_provider.get_session_factory_cached", return_value=_make_mock_factory(session)),
            patch("kt_mcp.oauth_provider.get_settings", return_value=mock_settings),
        ):
            provider = KnowledgeTreeOAuthProvider.__new__(KnowledgeTreeOAuthProvider)
            result = await provider.verify_token("any_token")

        assert result is not None
        assert result.client_id == "skip_auth"


class TestOAuthProviderTokenExchange:
    @pytest.mark.asyncio
    async def test_exchange_authorization_code(self):
        from mcp.server.auth.provider import AuthorizationCode
        from mcp.shared.auth import OAuthClientInformationFull
        from pydantic import AnyHttpUrl, AnyUrl

        from kt_mcp.oauth_provider import KnowledgeTreeOAuthProvider

        row = MagicMock()
        row.user_id = "user-uuid"

        session = _make_mock_session()
        session.get = AsyncMock(return_value=row)
        session.delete = AsyncMock()
        session.add = MagicMock()
        session.commit = AsyncMock()

        client = OAuthClientInformationFull(
            client_id="test_client",
            redirect_uris=[AnyHttpUrl("http://localhost/callback")],
            grant_types=["authorization_code"],
            response_types=["code"],
            token_endpoint_auth_method="client_secret_post",
        )

        auth_code = AuthorizationCode(
            code="test_code",
            client_id="test_client",
            redirect_uri=AnyUrl("http://localhost/callback"),
            redirect_uri_provided_explicitly=True,
            scopes=["read"],
            expires_at=time.time() + 300,
            code_challenge="challenge",
        )

        with patch("kt_mcp.oauth_provider.get_session_factory_cached", return_value=_make_mock_factory(session)):
            provider = KnowledgeTreeOAuthProvider.__new__(KnowledgeTreeOAuthProvider)
            token = await provider.exchange_authorization_code(client, auth_code)

        assert token.token_type == "Bearer"
        assert token.access_token is not None
        assert token.refresh_token is not None
        assert token.expires_in == 3600
        # Verify old code was deleted and new tokens were added
        session.delete.assert_called_once_with(row)
        assert session.add.call_count == 2  # access + refresh

    @pytest.mark.asyncio
    async def test_exchange_missing_code_raises(self):
        from mcp.server.auth.provider import AuthorizationCode, TokenError
        from mcp.shared.auth import OAuthClientInformationFull
        from pydantic import AnyHttpUrl, AnyUrl

        from kt_mcp.oauth_provider import KnowledgeTreeOAuthProvider

        session = _make_mock_session()
        session.get = AsyncMock(return_value=None)

        client = OAuthClientInformationFull(
            client_id="test_client",
            redirect_uris=[AnyHttpUrl("http://localhost/callback")],
            grant_types=["authorization_code"],
            response_types=["code"],
            token_endpoint_auth_method="client_secret_post",
        )

        auth_code = AuthorizationCode(
            code="missing_code",
            client_id="test_client",
            redirect_uri=AnyUrl("http://localhost/callback"),
            redirect_uri_provided_explicitly=True,
            scopes=[],
            expires_at=time.time() + 300,
            code_challenge="challenge",
        )

        with patch("kt_mcp.oauth_provider.get_session_factory_cached", return_value=_make_mock_factory(session)):
            provider = KnowledgeTreeOAuthProvider.__new__(KnowledgeTreeOAuthProvider)
            with pytest.raises(TokenError):
                await provider.exchange_authorization_code(client, auth_code)


class TestOAuthProviderRevocation:
    @pytest.mark.asyncio
    async def test_revoke_access_token(self):
        from fastmcp.server.auth.auth import AccessToken

        from kt_mcp.oauth_provider import KnowledgeTreeOAuthProvider

        access_row = MagicMock()

        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []

        session = _make_mock_session()
        session.get = AsyncMock(return_value=access_row)
        session.execute = AsyncMock(return_value=mock_result)
        session.delete = AsyncMock()
        session.commit = AsyncMock()

        token = AccessToken(token="access_tok", client_id="client1", scopes=[], expires_at=None)

        with patch("kt_mcp.oauth_provider.get_session_factory_cached", return_value=_make_mock_factory(session)):
            provider = KnowledgeTreeOAuthProvider.__new__(KnowledgeTreeOAuthProvider)
            await provider.revoke_token(token)

        session.delete.assert_called_once_with(access_row)
        session.commit.assert_called_once()


# ── Login flow tests ─────────────────────────────────────────────────


class TestLoginCSRF:
    @pytest.mark.asyncio
    async def test_login_page_sets_csrf_token(self):
        from kt_mcp.oauth_login import login_page

        row = MagicMock()
        row.expires_at = time.time() + 300
        row.csrf_token = None

        session = _make_mock_session()
        session.get = AsyncMock(return_value=row)
        session.commit = AsyncMock()

        with patch("kt_mcp.oauth_login.get_session_factory_cached", return_value=_make_mock_factory(session)):
            response = await login_page(code_id="pending_test")

        assert response.status_code == 200
        body = response.body.decode()
        assert 'name="csrf_token"' in body
        # Verify the csrf_token was persisted on the row
        assert row.csrf_token is not None

    @pytest.mark.asyncio
    async def test_login_submit_rejects_wrong_csrf(self):
        from kt_mcp.oauth_login import login_submit

        row = MagicMock()
        row.expires_at = time.time() + 300
        row.csrf_token = "correct_token"

        session = _make_mock_session()
        session.get = AsyncMock(return_value=row)

        with patch("kt_mcp.oauth_login.get_session_factory_cached", return_value=_make_mock_factory(session)):
            response = await login_submit(
                code_id="pending_test",
                csrf_token="wrong_token",
                email="user@test.com",
                password="pass",
            )

        assert response.status_code == 403

    @pytest.mark.asyncio
    async def test_login_submit_rejects_missing_csrf(self):
        from kt_mcp.oauth_login import login_submit

        row = MagicMock()
        row.expires_at = time.time() + 300
        row.csrf_token = None  # no CSRF token was set

        session = _make_mock_session()
        session.get = AsyncMock(return_value=row)

        with patch("kt_mcp.oauth_login.get_session_factory_cached", return_value=_make_mock_factory(session)):
            response = await login_submit(
                code_id="pending_test",
                csrf_token="any",
                email="user@test.com",
                password="pass",
            )

        assert response.status_code == 403


class TestLoginFlow:
    @pytest.mark.asyncio
    async def test_expired_code_returns_400(self):
        from kt_mcp.oauth_login import login_page

        row = MagicMock()
        row.expires_at = time.time() - 100  # expired

        session = _make_mock_session()
        session.get = AsyncMock(return_value=row)

        with patch("kt_mcp.oauth_login.get_session_factory_cached", return_value=_make_mock_factory(session)):
            response = await login_page(code_id="expired_code")

        assert response.status_code == 400

    @pytest.mark.asyncio
    async def test_missing_code_returns_400(self):
        from kt_mcp.oauth_login import login_page

        session = _make_mock_session()
        session.get = AsyncMock(return_value=None)

        with patch("kt_mcp.oauth_login.get_session_factory_cached", return_value=_make_mock_factory(session)):
            response = await login_page(code_id="nonexistent")

        assert response.status_code == 400

    @pytest.mark.asyncio
    async def test_successful_login_creates_new_code_row(self):
        from kt_mcp.oauth_login import login_submit

        csrf = secrets.token_urlsafe(32)
        hashed_pw = _hash_password("correct_password")

        # Mock the pending auth code row
        row = MagicMock()
        row.expires_at = time.time() + 300
        row.csrf_token = csrf
        row.client_id = "client1"
        row.redirect_uri = "http://localhost/callback"
        row.redirect_uri_provided_explicitly = True
        row.scopes = ["read"]
        row.code_challenge = "challenge"
        row.resource = None
        row.state = "test_state"

        # Mock user
        user = MagicMock()
        user.id = "user-uuid-123"
        user.email = "user@test.com"
        user.hashed_password = hashed_pw
        user.is_active = True

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = user

        session = _make_mock_session()
        session.get = AsyncMock(return_value=row)
        session.execute = AsyncMock(return_value=mock_result)
        session.delete = AsyncMock()
        session.flush = AsyncMock()
        session.add = MagicMock()
        session.commit = AsyncMock()

        with patch("kt_mcp.oauth_login.get_session_factory_cached", return_value=_make_mock_factory(session)):
            response = await login_submit(
                code_id="pending_test",
                csrf_token=csrf,
                email="user@test.com",
                password="correct_password",
            )

        # Should redirect with 302
        assert response.status_code == 302
        # Old row should be deleted, new one added
        session.delete.assert_called_once_with(row)
        session.add.assert_called_once()
        # Verify the new row has user_id set
        new_row = session.add.call_args[0][0]
        assert new_row.user_id == "user-uuid-123"
        assert new_row.csrf_token is None  # CSRF cleared on real code

    @pytest.mark.asyncio
    async def test_wrong_password_returns_401(self):
        from kt_mcp.oauth_login import login_submit

        csrf = secrets.token_urlsafe(32)
        hashed_pw = _hash_password("correct_password")

        row = MagicMock()
        row.expires_at = time.time() + 300
        row.csrf_token = csrf

        user = MagicMock()
        user.email = "user@test.com"
        user.hashed_password = hashed_pw
        user.is_active = True

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = user

        session = _make_mock_session()
        session.get = AsyncMock(return_value=row)
        session.execute = AsyncMock(return_value=mock_result)

        with patch("kt_mcp.oauth_login.get_session_factory_cached", return_value=_make_mock_factory(session)):
            response = await login_submit(
                code_id="pending_test",
                csrf_token=csrf,
                email="user@test.com",
                password="wrong_password",
            )

        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_inactive_user_returns_403(self):
        from kt_mcp.oauth_login import login_submit

        csrf = secrets.token_urlsafe(32)
        hashed_pw = _hash_password("password")

        row = MagicMock()
        row.expires_at = time.time() + 300
        row.csrf_token = csrf

        user = MagicMock()
        user.email = "user@test.com"
        user.hashed_password = hashed_pw
        user.is_active = False

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = user

        session = _make_mock_session()
        session.get = AsyncMock(return_value=row)
        session.execute = AsyncMock(return_value=mock_result)

        with patch("kt_mcp.oauth_login.get_session_factory_cached", return_value=_make_mock_factory(session)):
            response = await login_submit(
                code_id="pending_test",
                csrf_token=csrf,
                email="user@test.com",
                password="password",
            )

        assert response.status_code == 403
