"""Auth API router — login, register, OAuth, API tokens."""

from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

# Assembled in __init__.py
from kt_api.auth._fastapi_users import fastapi_users
from kt_api.auth.backend import auth_backend
from kt_api.auth.oauth import get_google_oauth_client
from kt_api.auth.schemas import UserCreate, UserRead, UserUpdate
from kt_api.auth.tokens import ApiTokenRepository, generate_token, require_auth
from kt_api.dependencies import get_db_session
from kt_config.settings import get_settings
from kt_db.models import User
from kt_db.repositories.system_settings import SystemSettingsRepository

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])

# ---- Standard FastAPI Users routes ----
router.include_router(fastapi_users.get_register_router(UserRead, UserCreate))
router.include_router(fastapi_users.get_reset_password_router())
router.include_router(fastapi_users.get_verify_router(UserRead))
router.include_router(fastapi_users.get_auth_router(auth_backend))
router.include_router(fastapi_users.get_users_router(UserRead, UserUpdate), prefix="/me-mgmt")

# ---- Google OAuth routes ----
google_oauth_client = get_google_oauth_client()
if google_oauth_client.client_id:
    router.include_router(
        fastapi_users.get_oauth_router(
            google_oauth_client,
            auth_backend,
            redirect_url=None,  # caller provides redirect_url param
        ),
        prefix="/google",
    )


# ---- /me endpoint ----
@router.get("/me", response_model=UserRead)
async def get_me(user: User = Depends(require_auth)) -> UserRead:
    return UserRead(
        id=user.id,
        email=user.email,
        is_active=user.is_active,
        is_superuser=user.is_superuser,
        is_verified=user.is_verified,
        display_name=getattr(user, "display_name", None),
        created_at=getattr(user, "created_at", None) or datetime.min,
        has_api_key=getattr(user, "encrypted_openrouter_key", None) is not None,
    )


# ---- API token endpoints ----


class ApiTokenCreateRequest(BaseModel):
    name: str
    expires_at: datetime | None = None
    graph_slugs: list[str] | None = None  # NULL = all graphs user can access


class ApiTokenResponse(BaseModel):
    id: uuid.UUID
    name: str
    created_at: str
    expires_at: str | None = None
    last_used_at: str | None = None
    graph_slugs: list[str] | None = None


class ApiTokenCreated(ApiTokenResponse):
    token: str  # raw token returned only once


@router.get("/tokens", response_model=list[ApiTokenResponse])
async def list_tokens(
    user: User = Depends(require_auth),
    session: AsyncSession = Depends(get_db_session),
) -> list[ApiTokenResponse]:
    repo = ApiTokenRepository(session)
    tokens = await repo.list_for_user(user.id)
    return [
        ApiTokenResponse(
            id=t.id,
            name=t.name,
            created_at=t.created_at.isoformat(),
            expires_at=t.expires_at.isoformat() if t.expires_at else None,
            last_used_at=t.last_used_at.isoformat() if t.last_used_at else None,
            graph_slugs=t.graph_slugs,
        )
        for t in tokens
    ]


@router.post("/tokens", response_model=ApiTokenCreated, status_code=status.HTTP_201_CREATED)
async def create_token(
    body: ApiTokenCreateRequest,
    user: User = Depends(require_auth),
    session: AsyncSession = Depends(get_db_session),
) -> ApiTokenCreated:
    # Validate graph_slugs if provided
    if body.graph_slugs is not None:
        from kt_db.repositories.graphs import GraphRepository

        graph_repo = GraphRepository(session)
        for slug in body.graph_slugs:
            graph = await graph_repo.get_by_slug(slug)
            if graph is None:
                raise HTTPException(status_code=400, detail=f"Graph '{slug}' not found")
            if not graph.is_default and not user.is_superuser:
                role = await graph_repo.get_member_role(graph.id, user.id)
                if role is None:
                    raise HTTPException(status_code=403, detail=f"No access to graph '{slug}'")

    raw = generate_token()
    repo = ApiTokenRepository(session)
    token = await repo.create(user.id, body.name, raw, expires_at=body.expires_at, graph_slugs=body.graph_slugs)
    await session.commit()
    return ApiTokenCreated(
        id=token.id,
        name=token.name,
        created_at=token.created_at.isoformat(),
        expires_at=token.expires_at.isoformat() if token.expires_at else None,
        graph_slugs=token.graph_slugs,
        token=raw,
    )


@router.delete("/tokens/{token_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_token(
    token_id: uuid.UUID,
    user: User = Depends(require_auth),
    session: AsyncSession = Depends(get_db_session),
) -> None:
    repo = ApiTokenRepository(session)
    revoked = await repo.revoke(token_id, user.id)
    if not revoked:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Token not found")
    await session.commit()


# ---- BYOK (Bring Your Own Key) endpoints ----


class SetApiKeyRequest(BaseModel):
    api_key: str


class ApiKeyStatusResponse(BaseModel):
    has_key: bool


@router.put("/me/api-key", response_model=ApiKeyStatusResponse)
async def set_api_key(
    body: SetApiKeyRequest,
    user: User = Depends(require_auth),
    session: AsyncSession = Depends(get_db_session),
) -> ApiKeyStatusResponse:
    """Encrypt and store the user's OpenRouter API key."""
    from kt_api.auth.crypto import encrypt_api_key

    if not body.api_key.strip():
        raise HTTPException(status_code=400, detail="API key cannot be empty")

    encrypted = encrypt_api_key(body.api_key.strip())
    from sqlalchemy import update

    await session.execute(update(User).where(User.id == user.id).values(encrypted_openrouter_key=encrypted))
    await session.commit()
    return ApiKeyStatusResponse(has_key=True)


@router.delete("/me/api-key", response_model=ApiKeyStatusResponse)
async def remove_api_key(
    user: User = Depends(require_auth),
    session: AsyncSession = Depends(get_db_session),
) -> ApiKeyStatusResponse:
    """Remove the user's stored OpenRouter API key."""
    from sqlalchemy import update

    await session.execute(update(User).where(User.id == user.id).values(encrypted_openrouter_key=None))
    await session.commit()
    return ApiKeyStatusResponse(has_key=False)


@router.get("/me/api-key/status", response_model=ApiKeyStatusResponse)
async def api_key_status(
    user: User = Depends(require_auth),
) -> ApiKeyStatusResponse:
    """Check whether the user has a stored API key."""
    return ApiKeyStatusResponse(
        has_key=getattr(user, "encrypted_openrouter_key", None) is not None,
    )


# ---- Auth features (public) ----


class AuthFeaturesResponse(BaseModel):
    google_oauth_enabled: bool
    email_verification_enabled: bool


@router.get("/features", response_model=AuthFeaturesResponse)
async def auth_features() -> AuthFeaturesResponse:
    """Public endpoint: check which auth features are available."""
    settings = get_settings()
    return AuthFeaturesResponse(
        google_oauth_enabled=bool(settings.google_oauth_client_id),
        email_verification_enabled=settings.email_enabled and settings.email_verification,
    )


# ---- Registration status (public) ----


class RegistrationStatusResponse(BaseModel):
    registration_open: bool
    waitlist_enabled: bool = False
    reason: str | None = None


@router.get("/registration-status", response_model=RegistrationStatusResponse)
async def registration_status(
    session: AsyncSession = Depends(get_db_session),
) -> RegistrationStatusResponse:
    """Public endpoint: check if self-registration is open."""
    settings = get_settings()
    if settings.disable_self_registration:
        return RegistrationStatusResponse(
            registration_open=False,
            waitlist_enabled=True,
            reason="Registration is disabled by the administrator.",
        )

    repo = SystemSettingsRepository(session)
    if await repo.get_bool("disable_self_registration"):
        return RegistrationStatusResponse(
            registration_open=False,
            waitlist_enabled=True,
            reason="Registration is disabled by the administrator.",
        )

    return RegistrationStatusResponse(registration_open=True)
