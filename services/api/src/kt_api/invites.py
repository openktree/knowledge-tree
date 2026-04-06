"""Invite endpoints — admin management + public validation."""

from __future__ import annotations

import secrets
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from kt_api.auth.permissions import require_system_permission
from kt_rbac import Permission
from kt_api.dependencies import get_db_session
from kt_db.models import User
from kt_db.repositories.invites import InviteRepository

router = APIRouter(prefix="/api/v1/invites", tags=["invites"])


# ---------- Schemas ----------


class InviteCreateRequest(BaseModel):
    email: str
    expires_in_days: int = 7


class InviteResponse(BaseModel):
    id: str
    email: str
    code: str
    expires_at: datetime
    redeemed_at: datetime | None
    created_at: datetime


class InviteValidateRequest(BaseModel):
    email: str
    code: str


class InviteValidateResponse(BaseModel):
    valid: bool
    email: str


# ---------- Helpers ----------


def _invite_response(inv: object) -> InviteResponse:
    from kt_db.models import Invite

    i: Invite = inv  # type: ignore[assignment]
    return InviteResponse(
        id=str(i.id),
        email=i.email,
        code=i.code,
        expires_at=i.expires_at,
        redeemed_at=i.redeemed_at,
        created_at=i.created_at,
    )


# ---------- Admin ----------


@router.post("", response_model=InviteResponse, status_code=201)
async def create_invite(
    body: InviteCreateRequest,
    admin: User = Depends(require_system_permission(Permission.SYSTEM_MANAGE_INVITES)),
    session: AsyncSession = Depends(get_db_session),
) -> InviteResponse:
    """Admin: generate a new invite for a specific email."""
    repo = InviteRepository(session)
    code = secrets.token_urlsafe(32)
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    invite = await repo.create(
        email=body.email,
        code=code,
        created_by=admin.id,
        expires_at=now + timedelta(days=body.expires_in_days),
    )
    await session.commit()
    return _invite_response(invite)


@router.get("", response_model=list[InviteResponse])
async def list_invites(
    _admin: User = Depends(require_system_permission(Permission.SYSTEM_MANAGE_INVITES)),
    session: AsyncSession = Depends(get_db_session),
) -> list[InviteResponse]:
    """Admin: list all invites."""
    repo = InviteRepository(session)
    invites, _ = await repo.list_all()
    return [_invite_response(i) for i in invites]


@router.delete("/{invite_id}", status_code=204)
async def revoke_invite(
    invite_id: str,
    _admin: User = Depends(require_system_permission(Permission.SYSTEM_MANAGE_INVITES)),
    session: AsyncSession = Depends(get_db_session),
) -> None:
    """Admin: revoke an unredeemed invite."""
    try:
        iid = uuid.UUID(invite_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid invite ID.")

    repo = InviteRepository(session)
    deleted = await repo.delete(iid)
    if not deleted:
        raise HTTPException(status_code=404, detail="Invite not found or already redeemed.")
    await session.commit()


# ---------- Public ----------


@router.post("/validate", response_model=InviteValidateResponse)
async def validate_invite(
    body: InviteValidateRequest,
    session: AsyncSession = Depends(get_db_session),
) -> InviteValidateResponse:
    """Public: validate an invite code for an email. Does NOT redeem it."""
    repo = InviteRepository(session)
    invite = await repo.get_valid_for_email(body.email, body.code)
    if invite is None:
        return InviteValidateResponse(valid=False, email=body.email.strip().lower())
    return InviteValidateResponse(valid=True, email=invite.email)
