"""Bearer token verification for the MCP server.

Replicates the API token verification logic from kt_api.auth.tokens
without importing from the API service (boundary rule: services never
import each other).
"""

from __future__ import annotations

import hashlib
import logging
from datetime import UTC, datetime

import bcrypt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from kt_config.settings import get_settings
from kt_db.models import ApiToken

logger = logging.getLogger(__name__)


def _verify_token(raw: str, hashed: str) -> bool:
    """Constant-time bcrypt verification (sha-256 pre-hash for length)."""
    try:
        digest = hashlib.sha256(raw.encode()).hexdigest()
        return bcrypt.checkpw(digest.encode(), hashed.encode())
    except (ValueError, TypeError):
        return False


async def verify_bearer_token(token: str, session: AsyncSession) -> bool:
    """Verify a bearer token against the api_tokens table.

    Returns True if the token matches a non-revoked, non-expired API token.
    When SKIP_AUTH is enabled, returns True unconditionally.
    """
    settings = get_settings()
    if settings.skip_auth:
        return True

    result = await session.execute(
        select(ApiToken).where(ApiToken.revoked.is_(False))
    )
    for api_token in result.scalars().all():
        if api_token.expires_at and api_token.expires_at < datetime.now(UTC).replace(tzinfo=None):
            continue
        if _verify_token(token, api_token.token_hash):
            return True
    return False
