"""System settings endpoints — admin only."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from kt_api.dependencies import get_db_session
from kt_api.auth.tokens import require_admin
from kt_config.settings import get_settings
from kt_db.models import User
from kt_db.repositories.system_settings import SystemSettingsRepository

router = APIRouter(prefix="/api/v1/system-settings", tags=["system-settings"])


class SystemSettingsResponse(BaseModel):
    disable_self_registration: bool
    disable_self_registration_source: str  # "env" | "db" | "default"


class UpdateSystemSettingsRequest(BaseModel):
    disable_self_registration: bool | None = None


@router.get("", response_model=SystemSettingsResponse)
async def get_system_settings(
    _admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_db_session),
) -> SystemSettingsResponse:
    """Return all system settings with their source."""
    settings = get_settings()
    repo = SystemSettingsRepository(session)

    if settings.disable_self_registration:
        return SystemSettingsResponse(
            disable_self_registration=True,
            disable_self_registration_source="env",
        )

    db_val = await repo.get_bool("disable_self_registration")
    return SystemSettingsResponse(
        disable_self_registration=db_val,
        disable_self_registration_source="db" if await repo.get("disable_self_registration") is not None else "default",
    )


@router.patch("", response_model=SystemSettingsResponse)
async def update_system_settings(
    body: UpdateSystemSettingsRequest,
    _admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_db_session),
) -> SystemSettingsResponse:
    """Update system settings (DB values only)."""
    settings = get_settings()
    repo = SystemSettingsRepository(session)

    if body.disable_self_registration is not None:
        await repo.set("disable_self_registration", str(body.disable_self_registration).lower())
        await session.commit()

    if settings.disable_self_registration:
        return SystemSettingsResponse(
            disable_self_registration=True,
            disable_self_registration_source="env",
        )

    db_val = await repo.get_bool("disable_self_registration")
    return SystemSettingsResponse(
        disable_self_registration=db_val,
        disable_self_registration_source="db" if await repo.get("disable_self_registration") is not None else "default",
    )
