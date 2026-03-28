"""UserManager — FastAPI Users user management."""

import logging
import uuid
from collections.abc import AsyncGenerator

from fastapi import Depends, HTTPException
from fastapi_users import BaseUserManager, UUIDIDMixin
from fastapi_users import schemas as fu_schemas
from sqlalchemy import func, select

from kt_api.auth.db import get_user_db
from kt_config.settings import get_settings
from kt_db.models import User
from kt_db.repositories.system_settings import SystemSettingsRepository

logger = logging.getLogger(__name__)


class UserManager(UUIDIDMixin, BaseUserManager[User, uuid.UUID]):
    @property
    def reset_password_token_secret(self) -> str:
        return get_settings().jwt_secret_key

    @property
    def verification_token_secret(self) -> str:
        return get_settings().jwt_secret_key

    async def create(  # type: ignore[override]
        self,
        user_create: fu_schemas.UC,
        safe: bool = False,
        request: object | None = None,
    ) -> User:
        """Block registration when self-registration is disabled (unless bootstrapping)."""
        session = self.user_db.session
        result = await session.execute(select(func.count()).select_from(User))
        user_count = result.scalar_one()

        if user_count > 0:
            settings = get_settings()
            registration_disabled = settings.disable_self_registration
            if not registration_disabled:
                repo = SystemSettingsRepository(session)
                registration_disabled = await repo.get_bool("disable_self_registration")

            if registration_disabled:
                # Allow registration if the user has a valid invite
                from kt_db.repositories.invites import InviteRepository

                invite_repo = InviteRepository(session)
                invite = await invite_repo.get_any_valid_for_email(user_create.email)
                if invite is None:
                    raise HTTPException(
                        status_code=403,
                        detail="Registration is disabled by the administrator.",
                    )

        return await super().create(user_create, safe=safe, request=request)

    async def on_after_register(self, user: User, request=None) -> None:  # type: ignore[override]
        logger.info("New user registered: %s (%s)", user.email, user.id)

        session = self.user_db.session

        # Mark any matching invite as redeemed
        from kt_db.repositories.invites import InviteRepository

        invite_repo = InviteRepository(session)
        invite = await invite_repo.get_any_valid_for_email(user.email)
        if invite is not None:
            await invite_repo.redeem(invite.id, user.id)
            logger.info("Invite %s redeemed by user %s", invite.id, user.email)

        # Auto-promote the first registered user to admin
        result = await session.execute(select(func.count()).select_from(User))
        user_count = result.scalar_one()
        if user_count == 1:
            user.is_superuser = True
            session.add(user)
            await session.flush()
            logger.info("First user %s auto-promoted to admin", user.email)


async def get_user_manager(user_db=Depends(get_user_db)) -> AsyncGenerator[UserManager, None]:
    yield UserManager(user_db)
