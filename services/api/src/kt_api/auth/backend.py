"""JWT authentication backend setup."""

from fastapi_users.authentication import AuthenticationBackend, BearerTransport, JWTStrategy

from kt_config.settings import get_settings


def get_jwt_strategy() -> JWTStrategy:  # type: ignore[type-arg]
    settings = get_settings()
    return JWTStrategy(
        secret=settings.jwt_secret_key,
        lifetime_seconds=settings.access_token_expire_minutes * 60,
    )


bearer_transport = BearerTransport(tokenUrl="/api/v1/auth/login")

auth_backend = AuthenticationBackend(
    name="jwt",
    transport=bearer_transport,
    get_strategy=get_jwt_strategy,
)
