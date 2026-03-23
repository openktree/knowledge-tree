"""FastAPI Users SQLAlchemy adapter dependency."""

from collections.abc import AsyncGenerator

from fastapi import Depends
from fastapi_users.db import SQLAlchemyUserDatabase
from sqlalchemy.ext.asyncio import AsyncSession

from kt_api.dependencies import get_db_session
from kt_db.models import OAuthAccount, User


async def get_user_db(session: AsyncSession = Depends(get_db_session)) -> AsyncGenerator[SQLAlchemyUserDatabase, None]:  # type: ignore[type-arg]
    yield SQLAlchemyUserDatabase(session, User, OAuthAccount)
