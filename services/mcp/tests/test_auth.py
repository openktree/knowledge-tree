"""Tests for MCP auth module."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from kt_auth import verify_token as _verify_token
from kt_mcp.auth import verify_bearer_token


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
    async def test_valid_token_returns_token_object(self):
        mock_token = MagicMock()
        mock_token.user_id = "test-user-id"
        mock_token.graph_slugs = ["default"]

        mock_settings = MagicMock()
        mock_settings.skip_auth = False

        with (
            patch("kt_mcp.auth.get_settings", return_value=mock_settings),
            patch("kt_mcp.auth.ApiTokenVerifier") as MockVerifier,
        ):
            instance = MockVerifier.return_value
            instance.find_by_raw = AsyncMock(return_value=mock_token)
            result = await verify_bearer_token("tokn_valid", AsyncMock())
        assert result is mock_token

    @pytest.mark.asyncio
    async def test_invalid_token_returns_none(self):
        mock_settings = MagicMock()
        mock_settings.skip_auth = False

        with (
            patch("kt_mcp.auth.get_settings", return_value=mock_settings),
            patch("kt_mcp.auth.ApiTokenVerifier") as MockVerifier,
        ):
            instance = MockVerifier.return_value
            instance.find_by_raw = AsyncMock(return_value=None)
            result = await verify_bearer_token("tokn_wrong", AsyncMock())
        assert result is None
