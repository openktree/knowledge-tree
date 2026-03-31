"""API token generation, hashing, verification, and auth dependency."""

from __future__ import annotations

import hashlib
import logging
import secrets
import uuid
from datetime import UTC, datetime

import bcrypt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from kt_api.dependencies import get_db_session
from kt_config.settings import get_settings
from kt_db.models import ApiToken, User

logger = logging.getLogger(__name__)

_bearer_scheme = HTTPBearer(auto_error=False)


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
    digest = hashlib.sha256(raw.encode()).hexdigest()
    return bcrypt.checkpw(digest.encode(), hashed.encode())


def _token_cache_key(raw: str) -> str:
    """Redis cache key for a verified API token (keyed by SHA-256 of raw token)."""
    digest = hashlib.sha256(raw.encode()).hexdigest()
    return f"kt:auth:token:{digest}"


# ---------------------------------------------------------------------------
# Repository
# ---------------------------------------------------------------------------


class ApiTokenRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self, user_id: uuid.UUID, name: str, raw_token: str, expires_at: datetime | None = None
    ) -> ApiToken:
        token = ApiToken(
            id=uuid.uuid4(),
            user_id=user_id,
            name=name,
            token_hash=hash_token(raw_token),
            expires_at=expires_at.replace(tzinfo=None) if expires_at and expires_at.tzinfo else expires_at,
        )
        self._session.add(token)
        await self._session.flush()
        return token

    async def list_for_user(self, user_id: uuid.UUID) -> list[ApiToken]:
        result = await self._session.execute(
            select(ApiToken).where(ApiToken.user_id == user_id, ApiToken.revoked.is_(False))
        )
        return list(result.scalars().all())

    async def get_by_id(self, token_id: uuid.UUID, user_id: uuid.UUID) -> ApiToken | None:
        result = await self._session.execute(
            select(ApiToken).where(ApiToken.id == token_id, ApiToken.user_id == user_id)
        )
        return result.scalar_one_or_none()

    async def find_by_raw(self, raw_token: str) -> ApiToken | None:
        """Find a non-revoked, non-expired token matching raw_token."""
        result = await self._session.execute(select(ApiToken).where(ApiToken.revoked.is_(False)))
        for token in result.scalars().all():
            if token.expires_at and token.expires_at < datetime.now(UTC).replace(tzinfo=None):
                continue
            if verify_token(raw_token, token.token_hash):
                return token
        return None

    async def revoke(self, token_id: uuid.UUID, user_id: uuid.UUID) -> bool:
        result = await self._session.execute(
            update(ApiToken).where(ApiToken.id == token_id, ApiToken.user_id == user_id).values(revoked=True)
        )
        return result.rowcount > 0

    async def touch_last_used(self, token_id: uuid.UUID) -> None:
        await self._session.execute(
            update(ApiToken).where(ApiToken.id == token_id).values(last_used_at=datetime.now(UTC).replace(tzinfo=None))
        )


# ---------------------------------------------------------------------------
# Auth dependency
# ---------------------------------------------------------------------------


async def require_auth(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
    session: AsyncSession = Depends(get_db_session),
) -> User:
    """Dependency that validates a JWT or API token.

    When SKIP_AUTH=true (test mode), returns a stub user without any DB calls.

    Accepts the token from either:
    - ``Authorization: Bearer <token>`` header (normal API calls)
    - ``?token=<token>`` query parameter (EventSource/SSE, which can't set headers)
    """
    settings = get_settings()

    if settings.skip_auth:
        # Return a minimal stub — tests don't need a real User object
        stub = User()
        stub.id = uuid.UUID("00000000-0000-0000-0000-000000000001")
        stub.email = "test@example.com"
        stub.is_active = True
        stub.is_superuser = True  # admin in test mode so all endpoints are accessible
        stub.is_verified = True
        return stub

    # Prefer Authorization header; fall back to query param for SSE endpoints
    # (browser EventSource cannot send custom headers).
    if credentials is not None:
        raw_token: str | None = credentials.credentials
    else:
        raw_token = request.query_params.get("token")

    if not raw_token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")

    # 1) Try JWT — construct user_db and manager directly (bypass FastAPI DI)
    from fastapi_users.db import SQLAlchemyUserDatabase

    from kt_api.auth.backend import auth_backend
    from kt_api.auth.manager import UserManager
    from kt_db.models import OAuthAccount

    try:
        user_db = SQLAlchemyUserDatabase(session, User, OAuthAccount)
        user_manager = UserManager(user_db)
        strategy = auth_backend.get_strategy()
        user = await strategy.read_token(raw_token, user_manager)
        if user is not None and user.is_active:
            return user
    except Exception:
        pass

    # 2) Try API token — check Redis cache first to skip bcrypt
    from kt_config.cache import cache_get, cache_set

    cache_key = _token_cache_key(raw_token)
    cached_user_id = await cache_get(cache_key)
    if cached_user_id is not None:
        result = await session.execute(select(User).where(User.id == uuid.UUID(cached_user_id)))
        user = result.unique().scalar_one_or_none()
        if user is not None and user.is_active:
            return user

    repo = ApiTokenRepository(session)
    api_token = await repo.find_by_raw(raw_token)
    if api_token is not None:
        # Cache the verified token→user_id mapping for 5 minutes
        await cache_set(cache_key, str(api_token.user_id), ttl=300)
        await repo.touch_last_used(api_token.id)
        result = await session.execute(select(User).where(User.id == api_token.user_id))
        user = result.unique().scalar_one_or_none()
        if user is not None and user.is_active:
            return user

    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token")


async def require_admin(
    user: User = Depends(require_auth),
) -> User:
    """Dependency that requires the user to be an admin (is_superuser)."""
    if not user.is_superuser:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required")
    return user
