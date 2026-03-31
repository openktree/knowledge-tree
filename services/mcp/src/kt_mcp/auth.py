"""Bearer token verification for the MCP server.

Delegates to the shared kt-auth library for Redis-cached API token
verification, eliminating the ~500ms bcrypt overhead on every request.
"""

from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from kt_auth import ApiTokenVerifier
from kt_config.settings import get_settings

logger = logging.getLogger(__name__)


async def verify_bearer_token(token: str, session: AsyncSession) -> bool:
    """Verify a bearer token against the api_tokens table (Redis-cached).

    Returns True if the token matches a non-revoked, non-expired API token.
    When SKIP_AUTH is enabled, returns True unconditionally.
    """
    settings = get_settings()
    if settings.skip_auth:
        return True

    verifier = ApiTokenVerifier(session)
    api_token = await verifier.find_by_raw(token)
    return api_token is not None
