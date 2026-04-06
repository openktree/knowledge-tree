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


async def verify_bearer_token(token: str, session: AsyncSession) -> object | None:
    """Verify a bearer token against the api_tokens table (Redis-cached).

    Returns the ApiToken object if valid, or a truthy stub when SKIP_AUTH
    is enabled. Returns None if the token is invalid.
    """
    settings = get_settings()
    if settings.skip_auth:
        return True  # truthy stub — no graph_slugs attribute

    verifier = ApiTokenVerifier(session)
    return await verifier.find_by_raw(token)
