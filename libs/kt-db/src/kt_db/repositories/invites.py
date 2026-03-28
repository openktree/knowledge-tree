"""Repository for admin-generated invites."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from kt_db.models import Invite


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class InviteRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self,
        email: str,
        code: str,
        created_by: uuid.UUID,
        expires_at: datetime,
    ) -> Invite:
        invite = Invite(
            id=uuid.uuid4(),
            email=email.strip().lower(),
            code=code,
            created_by=created_by,
            expires_at=expires_at,
        )
        self._session.add(invite)
        await self._session.flush()
        return invite

    async def list_all(
        self,
        offset: int = 0,
        limit: int = 50,
    ) -> tuple[list[Invite], int]:
        from sqlalchemy import func

        total = (await self._session.execute(select(func.count()).select_from(Invite))).scalar_one()
        rows = (
            (await self._session.execute(select(Invite).order_by(Invite.created_at.desc()).offset(offset).limit(limit)))
            .scalars()
            .all()
        )
        return list(rows), total

    async def get_by_code(self, code: str) -> Invite | None:
        result = await self._session.execute(select(Invite).where(Invite.code == code))
        return result.scalar_one_or_none()

    async def get_valid_for_email(self, email: str, code: str) -> Invite | None:
        """Find a valid (non-expired, non-redeemed) invite matching email + code."""
        result = await self._session.execute(
            select(Invite).where(
                Invite.email == email.strip().lower(),
                Invite.code == code,
                Invite.redeemed_at.is_(None),
                Invite.expires_at > _utcnow(),
            )
        )
        return result.scalar_one_or_none()

    async def get_any_valid_for_email(self, email: str) -> Invite | None:
        """Find any valid (non-expired, non-redeemed) invite for an email."""
        result = await self._session.execute(
            select(Invite)
            .where(
                Invite.email == email.strip().lower(),
                Invite.redeemed_at.is_(None),
                Invite.expires_at > _utcnow(),
            )
            .order_by(Invite.created_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def redeem(self, invite_id: uuid.UUID, user_id: uuid.UUID) -> None:
        await self._session.execute(
            update(Invite).where(Invite.id == invite_id).values(redeemed_at=_utcnow(), redeemed_by=user_id)
        )
        await self._session.flush()

    async def delete(self, invite_id: uuid.UUID) -> bool:
        result = await self._session.execute(delete(Invite).where(Invite.id == invite_id, Invite.redeemed_at.is_(None)))
        await self._session.flush()
        return result.rowcount > 0
