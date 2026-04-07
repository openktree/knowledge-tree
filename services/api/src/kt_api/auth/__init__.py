"""Auth module — exports the main auth objects."""

from kt_api.auth._fastapi_users import fastapi_users
from kt_api.auth.backend import auth_backend
from kt_api.auth.permissions import (
    build_permission_context,
    has_permission,
    require_graph_permission,
    require_system_permission,
)
from kt_api.auth.tokens import require_auth

__all__ = [
    "fastapi_users",
    "auth_backend",
    "require_auth",
    "require_system_permission",
    "require_graph_permission",
    "build_permission_context",
    "has_permission",
]
