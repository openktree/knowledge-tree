"""Auth module — exports the main auth objects."""

from kt_api.auth._fastapi_users import fastapi_users
from kt_api.auth.backend import auth_backend
from kt_api.auth.tokens import require_admin, require_auth

__all__ = ["fastapi_users", "auth_backend", "require_admin", "require_auth"]
