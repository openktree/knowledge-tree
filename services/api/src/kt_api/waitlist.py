"""Waitlist endpoints — public submission + admin management."""

from __future__ import annotations

import secrets
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from kt_api.auth.permissions import require_system_permission
from kt_api.dependencies import get_db_session
from kt_config.settings import get_settings
from kt_db.models import User
from kt_db.repositories.invites import InviteRepository
from kt_db.repositories.system_settings import SystemSettingsRepository
from kt_db.repositories.waitlist import WaitlistRepository
from kt_rbac import Permission

router = APIRouter(prefix="/api/v1/waitlist", tags=["waitlist"])


# ---------- Schemas ----------


class WaitlistSubmitRequest(BaseModel):
    email: str
    display_name: str | None = None
    message: str | None = None


class WaitlistSubmitResponse(BaseModel):
    status: str


class WaitlistEntryResponse(BaseModel):
    id: str
    email: str
    display_name: str | None
    message: str | None
    status: str
    reviewed_at: datetime | None
    created_at: datetime


class InviteInfo(BaseModel):
    id: str
    email: str
    code: str
    expires_at: datetime


class WaitlistReviewRequest(BaseModel):
    status: str  # "approved" | "rejected"
    expires_in_days: int = 7


class WaitlistReviewResponse(BaseModel):
    entry: WaitlistEntryResponse
    invite: InviteInfo | None = None


# ---------- Public ----------


def _entry_response(e: object) -> WaitlistEntryResponse:
    from kt_db.models import WaitlistEntry

    entry: WaitlistEntry = e  # type: ignore[assignment]
    return WaitlistEntryResponse(
        id=str(entry.id),
        email=entry.email,
        display_name=entry.display_name,
        message=entry.message,
        status=entry.status,
        reviewed_at=entry.reviewed_at,
        created_at=entry.created_at,
    )


@router.post("", response_model=WaitlistSubmitResponse)
async def submit_waitlist(
    body: WaitlistSubmitRequest,
    session: AsyncSession = Depends(get_db_session),
) -> WaitlistSubmitResponse:
    """Public: submit a waitlist request (only when registration is disabled)."""
    # Verify registration is actually disabled
    settings = get_settings()
    registration_disabled = settings.disable_self_registration
    if not registration_disabled:
        repo = SystemSettingsRepository(session)
        registration_disabled = await repo.get_bool("disable_self_registration")
    if not registration_disabled:
        raise HTTPException(status_code=400, detail="Registration is open — please register directly.")

    waitlist_repo = WaitlistRepository(session)
    if await waitlist_repo.exists_pending(body.email):
        raise HTTPException(status_code=409, detail="A pending request already exists for this email.")

    await waitlist_repo.create(
        email=body.email,
        display_name=body.display_name,
        message=body.message,
    )
    await session.commit()
    return WaitlistSubmitResponse(status="submitted")


# ---------- Admin ----------


@router.get("", response_model=list[WaitlistEntryResponse])
async def list_waitlist(
    status: str | None = Query(None),
    _admin: User = Depends(require_system_permission(Permission.SYSTEM_MANAGE_INVITES)),
    session: AsyncSession = Depends(get_db_session),
) -> list[WaitlistEntryResponse]:
    """Admin: list waitlist entries with optional status filter."""
    repo = WaitlistRepository(session)
    entries, _ = await repo.list_all(status_filter=status)
    return [_entry_response(e) for e in entries]


@router.patch("/{entry_id}", response_model=WaitlistReviewResponse)
async def review_waitlist_entry(
    entry_id: str,
    body: WaitlistReviewRequest,
    admin: User = Depends(require_system_permission(Permission.SYSTEM_MANAGE_INVITES)),
    session: AsyncSession = Depends(get_db_session),
) -> WaitlistReviewResponse:
    """Admin: approve or reject a waitlist entry. Approve auto-creates an invite."""
    if body.status not in ("approved", "rejected"):
        raise HTTPException(status_code=400, detail="Status must be 'approved' or 'rejected'.")

    try:
        eid = uuid.UUID(entry_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid entry ID.")

    repo = WaitlistRepository(session)
    entry = await repo.get_by_id(eid)
    if entry is None:
        raise HTTPException(status_code=404, detail="Waitlist entry not found.")
    if entry.status != "pending":
        raise HTTPException(status_code=400, detail="Entry has already been reviewed.")

    updated = await repo.update_status(eid, body.status, admin.id)
    assert updated is not None

    invite_info: InviteInfo | None = None
    if body.status == "approved":
        invite_repo = InviteRepository(session)
        code = secrets.token_urlsafe(32)
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        invite = await invite_repo.create(
            email=updated.email,
            code=code,
            created_by=admin.id,
            expires_at=now + timedelta(days=body.expires_in_days),
        )
        invite_info = InviteInfo(
            id=str(invite.id),
            email=invite.email,
            code=invite.code,
            expires_at=invite.expires_at,
        )

    await session.commit()
    return WaitlistReviewResponse(entry=_entry_response(updated), invite=invite_info)
