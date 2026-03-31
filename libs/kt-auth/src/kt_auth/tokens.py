"""API token generation, hashing, verification, and Redis-cached lookup.

Shared by both the API service and MCP server so that token verification
is fast (~1ms cached) and consistent across all entry points.
"""

from __future__ import annotations

import hashlib
import logging
import secrets
import uuid
from datetime import UTC, datetime

import bcrypt
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from kt_db.models import ApiToken

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Token crypto helpers
# ---------------------------------------------------------------------------


def generate_token() -> str:
    """Generate a cryptographically secure raw token with tokn_ prefix."""
    return "tokn_" + secrets.token_urlsafe(32)


def hash_token(raw: str) -> str:
    """Hash a raw token using bcrypt (via sha-256 to handle length limits)."""
    digest = hashlib.sha256(raw.encode()).hexdigest()
    return bcrypt.hashpw(digest.encode(), bcrypt.gensalt()).decode()


def verify_token(raw: str, hashed: str) -> bool:
    """Constant-time bcrypt verification."""
    try:
        digest = hashlib.sha256(raw.encode()).hexdigest()
        return bcrypt.checkpw(digest.encode(), hashed.encode())
    except (ValueError, TypeError):
        return False


def _token_cache_key(raw: str) -> str:
    """Redis cache key for a verified API token."""
    digest = hashlib.sha256(raw.encode()).hexdigest()
    return f"kt:auth:token:{digest}"


# ---------------------------------------------------------------------------
# Cached token verifier
# ---------------------------------------------------------------------------


class ApiTokenVerifier:
    """Verify API tokens with Redis caching to skip expensive bcrypt.

    Usage::

        verifier = ApiTokenVerifier(session)
        api_token = await verifier.find_by_raw("tokn_...")
        if api_token is not None:
            # token is valid, api_token.user_id is the owner
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def find_by_raw(self, raw_token: str) -> ApiToken | None:
        """Find a non-revoked, non-expired token matching *raw_token*.

        Checks Redis first (keyed by SHA-256 of the raw token). On cache miss,
        falls back to bcrypt verification against all non-revoked tokens and
        caches the result for 5 minutes on success.
        """
        from kt_config.cache import cache_get, cache_set

        cache_key = _token_cache_key(raw_token)

        # Fast path: cache hit → look up the token by ID directly
        cached = await cache_get(cache_key)
        if cached is not None:
            token_id = uuid.UUID(cached["token_id"])
            result = await self._session.execute(
                select(ApiToken).where(ApiToken.id == token_id, ApiToken.revoked.is_(False))
            )
            api_token = result.scalar_one_or_none()
            if api_token is not None:
                if api_token.expires_at and api_token.expires_at < datetime.now(UTC).replace(tzinfo=None):
                    return None
                return api_token

        # Slow path: full table scan + bcrypt
        result = await self._session.execute(select(ApiToken).where(ApiToken.revoked.is_(False)))
        for api_token in result.scalars().all():
            if api_token.expires_at and api_token.expires_at < datetime.now(UTC).replace(tzinfo=None):
                continue
            if verify_token(raw_token, api_token.token_hash):
                # Cache token_id + user_id for 5 minutes
                await cache_set(
                    cache_key,
                    {"token_id": str(api_token.id), "user_id": str(api_token.user_id)},
                    ttl=300,
                )
                return api_token

        return None

    async def find_by_raw_uncached(self, raw_token: str) -> ApiToken | None:
        """Verify without Redis (for environments without Redis)."""
        result = await self._session.execute(select(ApiToken).where(ApiToken.revoked.is_(False)))
        for api_token in result.scalars().all():
            if api_token.expires_at and api_token.expires_at < datetime.now(UTC).replace(tzinfo=None):
                continue
            if verify_token(raw_token, api_token.token_hash):
                return api_token
        return None

    async def touch_last_used(self, token_id: uuid.UUID) -> None:
        """Update the last_used_at timestamp."""
        await self._session.execute(
            update(ApiToken).where(ApiToken.id == token_id).values(last_used_at=datetime.now(UTC).replace(tzinfo=None))
        )
