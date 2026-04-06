"""UserManager — FastAPI Users user management."""

from __future__ import annotations

import logging
import uuid
from collections.abc import AsyncGenerator
from urllib.parse import quote

from fastapi import Depends, HTTPException
from fastapi_users import BaseUserManager, UUIDIDMixin
from fastapi_users import schemas as fu_schemas
from fastapi_users.db import SQLAlchemyUserDatabase
from sqlalchemy import func, select

from kt_api.auth.db import get_user_db
from kt_api.auth.email_templates import password_reset_email_html, verification_email_html
from kt_config.settings import get_settings
from kt_db.models import User
from kt_db.repositories.system_settings import SystemSettingsRepository
from kt_providers.email_base import EmailMessage, EmailProvider

logger = logging.getLogger(__name__)

# Module-level email provider singleton (created once on first use)
_email_provider_singleton: EmailProvider | None = None
_email_provider_initialized = False


def _get_email_provider_cached() -> EmailProvider | None:
    global _email_provider_singleton, _email_provider_initialized
    if not _email_provider_initialized:
        from kt_providers.email_factory import create_email_provider

        _email_provider_singleton = create_email_provider(get_settings())
        _email_provider_initialized = True
    return _email_provider_singleton


class UserManager(UUIDIDMixin, BaseUserManager[User, uuid.UUID]):
    def __init__(
        self,
        user_db: SQLAlchemyUserDatabase[User, uuid.UUID],
        email_provider: EmailProvider | None = None,
    ) -> None:
        super().__init__(user_db)
        self._email_provider = email_provider

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
            logger.info("First user %s auto-promoted to admin", user.email)

        # fastapi-users commits user creation in its own transaction, so any
        # changes made here (invite redemption, admin promotion) are in a new
        # implicit transaction that must be committed explicitly.
        await session.commit()

        # Request email verification when enabled (best-effort — don't fail registration)
        settings = get_settings()
        if settings.email_enabled and settings.email_verification and self._email_provider:
            try:
                await self.request_verify(user, request)
            except Exception:
                logger.exception("Failed to send verification email to %s", user.email)

    async def on_after_request_verify(self, user: User, token: str, request=None) -> None:  # type: ignore[override]
        if self._email_provider is None:
            return
        settings = get_settings()
        if not (settings.email_enabled and settings.email_verification):
            return

        base_url = settings.frontend_url.rstrip("/")
        verify_url = f"{base_url}/verify?token={quote(token, safe='')}"
        await self._email_provider.send_email(
            EmailMessage(
                to=user.email,
                subject="Verify Your Email — Knowledge Tree",
                html=verification_email_html(verify_url, user.email),
            )
        )
        logger.info("Verification email sent to %s", user.email)

    async def on_after_forgot_password(self, user: User, token: str, request=None) -> None:  # type: ignore[override]
        if self._email_provider is None:
            return
        settings = get_settings()
        if not settings.email_enabled:
            return

        base_url = settings.frontend_url.rstrip("/")
        reset_url = f"{base_url}/reset-password?token={quote(token, safe='')}"
        await self._email_provider.send_email(
            EmailMessage(
                to=user.email,
                subject="Reset Your Password — Knowledge Tree",
                html=password_reset_email_html(reset_url, user.email),
            )
        )
        logger.info("Password reset email sent to %s", user.email)


async def get_user_manager(user_db=Depends(get_user_db)) -> AsyncGenerator[UserManager, None]:
    email_provider = _get_email_provider_cached()
    yield UserManager(user_db, email_provider=email_provider)
