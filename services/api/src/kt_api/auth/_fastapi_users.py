"""Constructs the FastAPIUsers instance (extracted to avoid circular imports)."""

import uuid

from fastapi_users import FastAPIUsers

from kt_api.auth.backend import auth_backend
from kt_api.auth.manager import get_user_manager
from kt_db.models import User

fastapi_users: FastAPIUsers[User, uuid.UUID] = FastAPIUsers[User, uuid.UUID](
    get_user_manager,
    [auth_backend],
)
