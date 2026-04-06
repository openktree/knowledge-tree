"""Worker-side API key resolution.

Workers receive ``user_id`` (not plaintext API keys) via Hatchet workflow
inputs.  This module resolves the actual key from the database so that
sensitive credentials never appear in Hatchet payloads or dashboard UI.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from typing import Any

from cryptography.fernet import Fernet

from kt_config.settings import get_settings

logger = logging.getLogger(__name__)

# Type alias for async session factories (sessionmaker-style callables).
SessionFactory = Callable[..., Any]


def _get_fernet() -> Fernet:
    key = get_settings().byok_encryption_key
    if not key:
        raise ValueError("BYOK_ENCRYPTION_KEY is not configured")
    return Fernet(key.encode() if isinstance(key, str) else key)


def decrypt_api_key(ciphertext: str) -> str:
    """Decrypt a Fernet-encrypted API key."""
    return _get_fernet().decrypt(ciphertext.encode()).decode()


async def resolve_user_api_key(
    session_factory: SessionFactory,
    user_id: str | None,
) -> str | None:
    """Resolve API key for a user on the worker side.

    Looks up the user in graph-db, decrypts their BYOK key if present,
    or falls back to the system key for superusers.  Returns ``None``
    when no key is available.

    The resolved key is used in-process only — it is never serialized
    back into Hatchet payloads.
    """
    settings = get_settings()

    if not user_id:
        return settings.openrouter_api_key or None

    from sqlalchemy import select

    from kt_db.models import User

    try:
        uid = uuid.UUID(user_id)
    except ValueError:
        logger.warning("Invalid user_id for API key resolution: %s", user_id)
        return settings.openrouter_api_key or None

    async with session_factory() as session:
        result = await session.execute(select(User).where(User.id == uid))
        user = result.scalar_one_or_none()

    if user is None:
        logger.warning("User %s not found for API key resolution", user_id)
        return settings.openrouter_api_key or None

    # BYOK key takes priority
    if user.encrypted_openrouter_key:
        try:
            return decrypt_api_key(user.encrypted_openrouter_key)
        except Exception:
            logger.warning("Failed to decrypt BYOK key for user %s", user_id)
            # Fall through: superusers get system key, others get None

    # Superusers fall back to system key
    if user.is_superuser:
        return settings.openrouter_api_key or None

    return None
