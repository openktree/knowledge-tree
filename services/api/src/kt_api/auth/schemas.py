"""Pydantic schemas for user registration, read, and update."""

import uuid
from datetime import datetime

from fastapi_users import schemas


class UserRead(schemas.BaseUser[uuid.UUID]):
    display_name: str | None = None
    created_at: datetime
    has_api_key: bool = False


class UserCreate(schemas.BaseUserCreate):
    display_name: str | None = None


class UserUpdate(schemas.BaseUserUpdate):
    display_name: str | None = None
