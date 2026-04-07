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

# Async session factory — ``async_sessionmaker[AsyncSession]`` in practice.
# The returned object is an async context manager yielding an AsyncSession.
SessionFactory = Callable[[], Any]


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
        # User.oauth_accounts is a lazy="joined" collection, so the result
        # contains duplicate parent rows and must be uniquified before
        # scalar_one_or_none() (SQLAlchemy 2.0 safety check).
        user = result.unique().scalar_one_or_none()

    if user is None:
        logger.warning("User %s not found for API key resolution", user_id)
        return settings.openrouter_api_key or None

    # BYOK key takes priority
    if user.encrypted_openrouter_key:
        try:
            return decrypt_api_key(user.encrypted_openrouter_key)
        except Exception:
            # Fall through: superusers get system key, others get None
            logger.warning("Failed to decrypt BYOK key for user %s", user_id, exc_info=True)

    # Superusers fall back to system key
    if user.is_superuser:
        return settings.openrouter_api_key or None

    return None


async def resolve_user_api_key_cached(
    state: Any,
    user_id: str | None,
) -> str | None:
    """Resolve API key for a user, caching on ``state`` for the workflow run.

    Avoids repeated DB lookups when multiple tasks/phases call this within
    the same Hatchet workflow.  The result is stored as ``_resolved_api_key``
    on the state object.
    """
    if not hasattr(state, "_resolved_api_key"):
        state._resolved_api_key = await resolve_user_api_key(state.session_factory, user_id) if user_id else None
    return state._resolved_api_key
