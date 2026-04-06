"""Tests for worker-side API key resolution."""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from kt_hatchet.keys import decrypt_api_key, resolve_user_api_key

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_user(
    *,
    user_id: uuid.UUID | None = None,
    encrypted_key: str | None = None,
    is_superuser: bool = False,
) -> SimpleNamespace:
    return SimpleNamespace(
        id=user_id or uuid.uuid4(),
        encrypted_openrouter_key=encrypted_key,
        is_superuser=is_superuser,
    )


def _mock_session_factory(user: SimpleNamespace | None = None):
    """Return a callable that yields an async-context session mock."""
    session = AsyncMock()
    result_mock = MagicMock()
    result_mock.scalar_one_or_none.return_value = user
    session.execute = AsyncMock(return_value=result_mock)

    @asynccontextmanager
    async def factory():
        yield session

    return factory


# ---------------------------------------------------------------------------
# decrypt_api_key
# ---------------------------------------------------------------------------


class TestDecryptApiKey:
    def test_round_trip(self) -> None:
        from cryptography.fernet import Fernet

        key = Fernet.generate_key()
        fernet = Fernet(key)
        plaintext = "sk-test-12345"
        ciphertext = fernet.encrypt(plaintext.encode()).decode()

        with patch("kt_hatchet.keys.get_settings") as mock_settings:
            mock_settings.return_value.byok_encryption_key = key.decode()
            assert decrypt_api_key(ciphertext) == plaintext

    def test_missing_encryption_key_raises(self) -> None:
        with patch("kt_hatchet.keys.get_settings") as mock_settings:
            mock_settings.return_value.byok_encryption_key = None
            with pytest.raises(ValueError, match="BYOK_ENCRYPTION_KEY"):
                decrypt_api_key("doesntmatter")


# ---------------------------------------------------------------------------
# resolve_user_api_key
# ---------------------------------------------------------------------------


class TestResolveUserApiKey:
    @pytest.mark.asyncio
    async def test_no_user_id_returns_system_key(self) -> None:
        with patch("kt_hatchet.keys.get_settings") as mock_settings:
            mock_settings.return_value.openrouter_api_key = "system-key"
            result = await resolve_user_api_key(MagicMock(), None)
        assert result == "system-key"

    @pytest.mark.asyncio
    async def test_no_user_id_no_system_key_returns_none(self) -> None:
        with patch("kt_hatchet.keys.get_settings") as mock_settings:
            mock_settings.return_value.openrouter_api_key = None
            result = await resolve_user_api_key(MagicMock(), None)
        assert result is None

    @pytest.mark.asyncio
    async def test_invalid_uuid_returns_system_key(self) -> None:
        with patch("kt_hatchet.keys.get_settings") as mock_settings:
            mock_settings.return_value.openrouter_api_key = "system-key"
            result = await resolve_user_api_key(MagicMock(), "not-a-uuid")
        assert result == "system-key"

    @pytest.mark.asyncio
    async def test_user_not_found_returns_system_key(self) -> None:
        factory = _mock_session_factory(user=None)
        with patch("kt_hatchet.keys.get_settings") as mock_settings:
            mock_settings.return_value.openrouter_api_key = "system-key"
            result = await resolve_user_api_key(factory, str(uuid.uuid4()))
        assert result == "system-key"

    @pytest.mark.asyncio
    async def test_byok_user_gets_decrypted_key(self) -> None:
        from cryptography.fernet import Fernet

        fernet_key = Fernet.generate_key()
        fernet = Fernet(fernet_key)
        plaintext = "sk-byok-user"
        ciphertext = fernet.encrypt(plaintext.encode()).decode()

        user = _make_user(encrypted_key=ciphertext, is_superuser=False)
        factory = _mock_session_factory(user=user)

        with patch("kt_hatchet.keys.get_settings") as mock_settings:
            mock_settings.return_value.openrouter_api_key = "system-key"
            mock_settings.return_value.byok_encryption_key = fernet_key.decode()
            result = await resolve_user_api_key(factory, str(user.id))
        assert result == plaintext

    @pytest.mark.asyncio
    async def test_superuser_without_byok_gets_system_key(self) -> None:
        user = _make_user(encrypted_key=None, is_superuser=True)
        factory = _mock_session_factory(user=user)

        with patch("kt_hatchet.keys.get_settings") as mock_settings:
            mock_settings.return_value.openrouter_api_key = "system-key"
            result = await resolve_user_api_key(factory, str(user.id))
        assert result == "system-key"

    @pytest.mark.asyncio
    async def test_regular_user_without_byok_gets_none(self) -> None:
        user = _make_user(encrypted_key=None, is_superuser=False)
        factory = _mock_session_factory(user=user)

        with patch("kt_hatchet.keys.get_settings") as mock_settings:
            mock_settings.return_value.openrouter_api_key = "system-key"
            result = await resolve_user_api_key(factory, str(user.id))
        assert result is None

    @pytest.mark.asyncio
    async def test_corrupted_byok_superuser_falls_back_to_system(self) -> None:
        """Superuser with a corrupted BYOK key should fall back to system key."""
        user = _make_user(encrypted_key="corrupted-ciphertext", is_superuser=True)
        factory = _mock_session_factory(user=user)

        with patch("kt_hatchet.keys.get_settings") as mock_settings:
            mock_settings.return_value.openrouter_api_key = "system-key"
            mock_settings.return_value.byok_encryption_key = "dGVzdGtleXRlc3RrZXl0ZXN0a2V5dGVzdGtleTA="
            result = await resolve_user_api_key(factory, str(user.id))
        assert result == "system-key"

    @pytest.mark.asyncio
    async def test_corrupted_byok_regular_user_gets_none(self) -> None:
        """Regular user with a corrupted BYOK key gets None (no fallback)."""
        user = _make_user(encrypted_key="corrupted-ciphertext", is_superuser=False)
        factory = _mock_session_factory(user=user)

        with patch("kt_hatchet.keys.get_settings") as mock_settings:
            mock_settings.return_value.openrouter_api_key = "system-key"
            mock_settings.return_value.byok_encryption_key = "dGVzdGtleXRlc3RrZXl0ZXN0a2V5dGVzdGtleTA="
            result = await resolve_user_api_key(factory, str(user.id))
        assert result is None
