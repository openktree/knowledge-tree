"""Members management endpoints — admin only."""

from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from kt_api.dependencies import get_db_session
from kt_api.auth.tokens import require_admin
from kt_db.models import User

router = APIRouter(prefix="/api/v1/members", tags=["members"])


class MemberResponse(BaseModel):
    id: str
    email: str
    display_name: str | None = None
    is_superuser: bool
    is_active: bool
    created_at: datetime
    has_byok: bool


class UpdateRoleRequest(BaseModel):
    is_superuser: bool


@router.get("", response_model=list[MemberResponse])
async def list_members(
    _admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_db_session),
) -> list[MemberResponse]:
    """List all users (admin only)."""
    result = await session.execute(
        select(User).order_by(User.created_at)
    )
    users = result.unique().scalars().all()
    return [
        MemberResponse(
            id=str(u.id),
            email=u.email,
            display_name=getattr(u, "display_name", None),
            is_superuser=u.is_superuser,
            is_active=u.is_active,
            created_at=getattr(u, "created_at", datetime.min),
            has_byok=getattr(u, "encrypted_openrouter_key", None) is not None,
        )
        for u in users
    ]


@router.patch("/{user_id}/role", response_model=MemberResponse)
async def update_member_role(
    user_id: str,
    body: UpdateRoleRequest,
    admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_db_session),
) -> MemberResponse:
    """Update a user's admin role (admin only). Cannot demote yourself."""
    try:
        target_uuid = uuid.UUID(user_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid user ID")

    if target_uuid == admin.id and not body.is_superuser:
        raise HTTPException(status_code=400, detail="Cannot demote yourself")

    result = await session.execute(select(User).where(User.id == target_uuid))
    target = result.unique().scalar_one_or_none()
    if target is None:
        raise HTTPException(status_code=404, detail="User not found")

    await session.execute(
        update(User).where(User.id == target_uuid).values(is_superuser=body.is_superuser)
    )
    await session.commit()

    # Re-fetch to return updated state
    result = await session.execute(select(User).where(User.id == target_uuid))
    target = result.unique().scalar_one_or_none()
    assert target is not None

    return MemberResponse(
        id=str(target.id),
        email=target.email,
        display_name=getattr(target, "display_name", None),
        is_superuser=target.is_superuser,
        is_active=target.is_active,
        created_at=getattr(target, "created_at", datetime.min),
        has_byok=getattr(target, "encrypted_openrouter_key", None) is not None,
    )
