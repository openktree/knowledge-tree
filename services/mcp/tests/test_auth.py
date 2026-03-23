"""Tests for MCP auth module."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from kt_mcp.auth import _verify_token, verify_bearer_token


class TestVerifyToken:
    def test_correct_token(self):
        import hashlib

        import bcrypt

        raw = "tokn_test123"
        digest = hashlib.sha256(raw.encode()).hexdigest()
        hashed = bcrypt.hashpw(digest.encode(), bcrypt.gensalt()).decode()
        assert _verify_token(raw, hashed) is True

    def test_wrong_token(self):
        import hashlib

        import bcrypt

        raw = "tokn_test123"
        digest = hashlib.sha256(raw.encode()).hexdigest()
        hashed = bcrypt.hashpw(digest.encode(), bcrypt.gensalt()).decode()
        assert _verify_token("tokn_wrong", hashed) is False


class TestVerifyBearerToken:
    @pytest.mark.asyncio
    async def test_skip_auth_returns_true(self):
        mock_settings = MagicMock()
        mock_settings.skip_auth = True
        with patch("kt_mcp.auth.get_settings", return_value=mock_settings):
            result = await verify_bearer_token("any_token", AsyncMock())
        assert result is True

    @pytest.mark.asyncio
    async def test_valid_token_returns_true(self):
        import hashlib

        import bcrypt

        raw = "tokn_valid"
        digest = hashlib.sha256(raw.encode()).hexdigest()
        hashed = bcrypt.hashpw(digest.encode(), bcrypt.gensalt()).decode()

        mock_token = MagicMock()
        mock_token.token_hash = hashed
        mock_token.expires_at = None

        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [mock_token]

        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(return_value=mock_result)

        mock_settings = MagicMock()
        mock_settings.skip_auth = False
        with patch("kt_mcp.auth.get_settings", return_value=mock_settings):
            result = await verify_bearer_token(raw, mock_session)
        assert result is True

    @pytest.mark.asyncio
    async def test_invalid_token_returns_false(self):
        mock_token = MagicMock()
        mock_token.token_hash = "$2b$12$invalidhashvalue"
        mock_token.expires_at = None

        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [mock_token]

        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(return_value=mock_result)

        mock_settings = MagicMock()
        mock_settings.skip_auth = False
        with patch("kt_mcp.auth.get_settings", return_value=mock_settings):
            result = await verify_bearer_token("tokn_wrong", mock_session)
        assert result is False
