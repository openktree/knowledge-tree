"""Tests for MCP OAuth 2.1 provider and login flow."""

from __future__ import annotations

import hashlib
import secrets
import time
from unittest.mock import AsyncMock, MagicMock, patch

import bcrypt
import pytest


def _hash_password(plain: str) -> str:
    """Hash a password with bcrypt (matching fastapi-users format)."""
    return bcrypt.hashpw(plain.encode(), bcrypt.gensalt()).decode()


def _hash_token(token: str) -> str:
    """Mirror the provider's hashing for test assertions."""
    return hashlib.sha256(token.encode()).hexdigest()


# ── oauth_provider._hash_token ────────────────────────────────────────


class TestHashToken:
    def test_deterministic(self):
        from kt_mcp.oauth_provider import _hash_token

        assert _hash_token("abc") == _hash_token("abc")

    def test_different_inputs_differ(self):
        from kt_mcp.oauth_provider import _hash_token

        assert _hash_token("abc") != _hash_token("def")

    def test_returns_hex_sha256(self):
        from kt_mcp.oauth_provider import _hash_token

        result = _hash_token("test")
        assert result == hashlib.sha256(b"test").hexdigest()
        assert len(result) == 64  # SHA-256 hex is 64 chars


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


def _make_mock_request(ip: str = "127.0.0.1") -> MagicMock:
    """Create a mock FastAPI Request with a client IP."""
    request = MagicMock()
    request.client = MagicMock()
    request.client.host = ip
    return request


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

        plaintext_token = "valid_token_abc123"

        row = MagicMock()
        row.token = _hash_token(plaintext_token)
        row.client_id = "client1"
        row.scopes = []
        row.expires_at = int(time.time()) + 3600

        session = _make_mock_session()
        session.get = AsyncMock(return_value=row)

        with patch("kt_mcp.oauth_provider.get_session_factory_cached", return_value=_make_mock_factory(session)):
            provider = KnowledgeTreeOAuthProvider.__new__(KnowledgeTreeOAuthProvider)
            result = await provider.verify_token(plaintext_token)

        assert result is not None
        # verify_token returns the plaintext token, not the hash
        assert result.token == plaintext_token
        assert result.client_id == "client1"
        # Verify lookup was by hash
        session.get.assert_called_once()
        lookup_key = session.get.call_args[0][1]
        assert lookup_key == _hash_token(plaintext_token)

    @pytest.mark.asyncio
    async def test_verify_expired_token_returns_none(self):
        from kt_mcp.oauth_provider import KnowledgeTreeOAuthProvider

        plaintext_token = "expired_token_xyz"

        row = MagicMock()
        row.token = _hash_token(plaintext_token)
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
            result = await provider.verify_token(plaintext_token)

        assert result is None

    @pytest.mark.asyncio
    async def test_verify_falls_back_to_legacy_token(self):
        from kt_mcp.oauth_provider import KnowledgeTreeOAuthProvider

        # First call: OAuth token not found (hash lookup misses)
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
    async def test_exchange_authorization_code_stores_hashed(self):
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

        # Verify tokens are stored as hashes, not plaintext
        access_row = session.add.call_args_list[0][0][0]
        refresh_row = session.add.call_args_list[1][0][0]
        assert access_row.token == _hash_token(token.access_token)
        assert refresh_row.token == _hash_token(token.refresh_token)
        # Refresh token's access_token FK should also be the hash
        assert refresh_row.access_token == _hash_token(token.access_token)
        # user_id should be propagated from the auth code row
        assert access_row.user_id == "user-uuid"
        assert refresh_row.user_id == "user-uuid"

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


class TestOAuthProviderRefreshToken:
    @pytest.mark.asyncio
    async def test_exchange_refresh_token_propagates_user_id(self):
        from mcp.server.auth.provider import RefreshToken
        from mcp.shared.auth import OAuthClientInformationFull
        from pydantic import AnyHttpUrl

        from kt_mcp.oauth_provider import KnowledgeTreeOAuthProvider

        plaintext_refresh = "refresh_tok_abc"
        refresh_hash = _hash_token(plaintext_refresh)
        access_hash = _hash_token("old_access_tok")

        old_refresh_row = MagicMock()
        old_refresh_row.user_id = "user-uuid-456"
        old_refresh_row.access_token = access_hash

        old_access_row = MagicMock()

        async def mock_get(model, key):
            if key == refresh_hash:
                return old_refresh_row
            if key == access_hash:
                return old_access_row
            return None

        session = _make_mock_session()
        session.get = AsyncMock(side_effect=mock_get)
        session.delete = AsyncMock()
        session.add = MagicMock()
        session.commit = AsyncMock()

        client = OAuthClientInformationFull(
            client_id="test_client",
            redirect_uris=[AnyHttpUrl("http://localhost/callback")],
            grant_types=["authorization_code", "refresh_token"],
            response_types=["code"],
            token_endpoint_auth_method="client_secret_post",
        )

        refresh_token = RefreshToken(
            token=plaintext_refresh,
            client_id="test_client",
            scopes=["read"],
            expires_at=int(time.time()) + 3600,
        )

        with patch("kt_mcp.oauth_provider.get_session_factory_cached", return_value=_make_mock_factory(session)):
            provider = KnowledgeTreeOAuthProvider.__new__(KnowledgeTreeOAuthProvider)
            token = await provider.exchange_refresh_token(client, refresh_token, ["read"])

        assert token.access_token is not None
        assert token.refresh_token is not None

        # Verify new tokens are stored as hashes
        new_access_row = session.add.call_args_list[0][0][0]
        new_refresh_row = session.add.call_args_list[1][0][0]
        assert new_access_row.token == _hash_token(token.access_token)
        assert new_refresh_row.token == _hash_token(token.refresh_token)

        # user_id must be propagated from old refresh token
        assert new_access_row.user_id == "user-uuid-456"
        assert new_refresh_row.user_id == "user-uuid-456"

        # Old tokens should be deleted
        assert session.delete.call_count == 2  # old access + old refresh


class TestOAuthProviderRevocation:
    @pytest.mark.asyncio
    async def test_revoke_access_token(self):
        from fastmcp.server.auth.auth import AccessToken

        from kt_mcp.oauth_provider import KnowledgeTreeOAuthProvider

        plaintext_token = "access_tok_to_revoke"
        token_hash = _hash_token(plaintext_token)

        access_row = MagicMock()

        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []

        session = _make_mock_session()
        session.get = AsyncMock(return_value=access_row)
        session.execute = AsyncMock(return_value=mock_result)
        session.delete = AsyncMock()
        session.commit = AsyncMock()

        token = AccessToken(token=plaintext_token, client_id="client1", scopes=[], expires_at=None)

        with patch("kt_mcp.oauth_provider.get_session_factory_cached", return_value=_make_mock_factory(session)):
            provider = KnowledgeTreeOAuthProvider.__new__(KnowledgeTreeOAuthProvider)
            await provider.revoke_token(token)

        # Should look up by hash
        session.get.assert_called_once()
        lookup_key = session.get.call_args[0][1]
        assert lookup_key == token_hash

        session.delete.assert_called_once_with(access_row)
        session.commit.assert_called_once()


# ── Login flow tests ─────────────────────────────────────────────────


class TestLoginXSSEscaping:
    @pytest.mark.asyncio
    async def test_code_id_is_html_escaped(self):
        from kt_mcp.oauth_login import login_page

        malicious_code_id = 'pending_<script>alert("xss")</script>'

        row = MagicMock()
        row.expires_at = time.time() + 300
        row.csrf_token = None

        session = _make_mock_session()
        session.get = AsyncMock(return_value=row)
        session.commit = AsyncMock()

        with patch("kt_mcp.oauth_login.get_session_factory_cached", return_value=_make_mock_factory(session)):
            response = await login_page(code_id=malicious_code_id)

        body = response.body.decode()
        # The raw script tag must NOT appear in the output
        assert "<script>" not in body
        # The escaped version should be present
        assert "&lt;script&gt;" in body


@pytest.fixture(autouse=True)
def _clear_rate_limiter():
    """Reset the in-memory rate limiter between tests."""
    from kt_mcp.oauth_login import _failed_attempts

    _failed_attempts.clear()
    yield
    _failed_attempts.clear()


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
                request=_make_mock_request(),
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
                request=_make_mock_request(),
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
                request=_make_mock_request(),
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
        # Verify the new row has user_id set and expires_at is refreshed
        new_row = session.add.call_args[0][0]
        assert new_row.user_id == "user-uuid-123"
        assert new_row.csrf_token is None  # CSRF cleared on real code
        # expires_at should be reset to a fresh window, not copied from the old row
        assert new_row.expires_at > row.expires_at

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
                request=_make_mock_request(),
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
                request=_make_mock_request(),
                code_id="pending_test",
                csrf_token=csrf,
                email="user@test.com",
                password="password",
            )

        assert response.status_code == 403


class TestLoginRateLimiting:
    @pytest.mark.asyncio
    async def test_blocks_after_max_failed_attempts(self):
        from kt_mcp.oauth_login import _MAX_ATTEMPTS, _record_failed_attempt, login_submit

        ip = "10.0.0.99"

        # Exhaust the rate limit
        for _ in range(_MAX_ATTEMPTS):
            _record_failed_attempt(ip)

        row = MagicMock()
        row.expires_at = time.time() + 300
        row.csrf_token = "token"

        session = _make_mock_session()
        session.get = AsyncMock(return_value=row)

        with patch("kt_mcp.oauth_login.get_session_factory_cached", return_value=_make_mock_factory(session)):
            response = await login_submit(
                request=_make_mock_request(ip=ip),
                code_id="pending_test",
                csrf_token="token",
                email="user@test.com",
                password="pass",
            )

        assert response.status_code == 429
        # Should not even hit the database
        session.get.assert_not_called()

    @pytest.mark.asyncio
    async def test_allows_requests_from_different_ip(self):
        from kt_mcp.oauth_login import _MAX_ATTEMPTS, _record_failed_attempt, login_submit

        blocked_ip = "10.0.0.100"
        for _ in range(_MAX_ATTEMPTS):
            _record_failed_attempt(blocked_ip)

        row = MagicMock()
        row.expires_at = time.time() + 300
        row.csrf_token = "token"

        session = _make_mock_session()
        session.get = AsyncMock(return_value=row)

        # Different IP should not be blocked
        with patch("kt_mcp.oauth_login.get_session_factory_cached", return_value=_make_mock_factory(session)):
            response = await login_submit(
                request=_make_mock_request(ip="10.0.0.101"),
                code_id="pending_test",
                csrf_token="wrong",
                email="user@test.com",
                password="pass",
            )

        # Should get past rate limiting (403 = CSRF check, not 429)
        assert response.status_code == 403

    def test_failed_login_records_attempt(self):
        from kt_mcp.oauth_login import _failed_attempts, _record_failed_attempt

        ip = "10.0.0.200"
        assert len(_failed_attempts[ip]) == 0
        _record_failed_attempt(ip)
        assert len(_failed_attempts[ip]) == 1

    def test_rate_limiter_evicts_when_over_cap(self):
        from kt_mcp.oauth_login import _MAX_TRACKED_IPS, _failed_attempts, _record_failed_attempt

        # Fill to the cap
        for i in range(_MAX_TRACKED_IPS):
            _failed_attempts[f"10.0.{i // 256}.{i % 256}"].append(time.monotonic())

        assert len(_failed_attempts) == _MAX_TRACKED_IPS

        # Recording a new IP should evict old entries to stay within cap
        _record_failed_attempt("192.168.1.1")
        assert len(_failed_attempts) <= _MAX_TRACKED_IPS
        assert "192.168.1.1" in _failed_attempts

    def test_expired_entries_cleaned_on_check(self):
        from kt_mcp.oauth_login import _failed_attempts, _is_rate_limited

        ip = "10.0.0.201"
        # Add an entry far in the past (will be expired)
        _failed_attempts[ip].append(time.monotonic() - 600)
        assert not _is_rate_limited(ip)
        # Expired entry should have been cleaned up, removing the key entirely
        assert ip not in _failed_attempts


class TestOAuthCleanup:
    @pytest.mark.asyncio
    async def test_cleanup_expired_tokens(self):
        from kt_mcp.oauth_provider import KnowledgeTreeOAuthProvider

        now = time.time()
        now_int = int(now)

        expired_code = MagicMock()
        expired_access = MagicMock()
        expired_refresh = MagicMock()

        # Build mock results for three queries
        code_result = MagicMock()
        code_result.scalars.return_value.all.return_value = [expired_code]

        access_result = MagicMock()
        access_result.scalars.return_value.all.return_value = [expired_access]

        refresh_result = MagicMock()
        refresh_result.scalars.return_value.all.return_value = [expired_refresh]

        call_count = 0

        async def mock_execute(stmt):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return code_result
            elif call_count == 2:
                return access_result
            else:
                return refresh_result

        session = _make_mock_session()
        session.execute = AsyncMock(side_effect=mock_execute)
        session.delete = AsyncMock()
        session.commit = AsyncMock()

        with patch("kt_mcp.oauth_provider.get_session_factory_cached", return_value=_make_mock_factory(session)):
            provider = KnowledgeTreeOAuthProvider.__new__(KnowledgeTreeOAuthProvider)
            counts = await provider.cleanup_expired()

        assert counts["authorization_codes"] == 1
        assert counts["access_tokens"] == 1
        assert counts["refresh_tokens"] == 1
        assert session.delete.call_count == 3
        session.commit.assert_called_once()

    @pytest.mark.asyncio
    async def test_cleanup_no_expired(self):
        from kt_mcp.oauth_provider import KnowledgeTreeOAuthProvider

        empty_result = MagicMock()
        empty_result.scalars.return_value.all.return_value = []

        session = _make_mock_session()
        session.execute = AsyncMock(return_value=empty_result)
        session.commit = AsyncMock()

        with patch("kt_mcp.oauth_provider.get_session_factory_cached", return_value=_make_mock_factory(session)):
            provider = KnowledgeTreeOAuthProvider.__new__(KnowledgeTreeOAuthProvider)
            counts = await provider.cleanup_expired()

        assert counts["authorization_codes"] == 0
        assert counts["access_tokens"] == 0
        assert counts["refresh_tokens"] == 0
        session.commit.assert_called_once()
