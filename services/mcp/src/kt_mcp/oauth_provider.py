"""PostgreSQL-backed OAuth 2.1 provider for the MCP server.

Subclasses FastMCP's OAuthProvider with database-backed storage for
clients, authorization codes, access tokens, and refresh tokens.
Falls back to legacy API token verification for backward compatibility.

Tokens are stored as SHA-256 hashes — the plaintext token is only ever
returned to the client and never persisted.
"""

from __future__ import annotations

import hashlib
import logging
import secrets
import time

from fastmcp.server.auth.auth import AccessToken, OAuthProvider
from mcp.server.auth.provider import (
    AuthorizationCode,
    AuthorizationParams,
    AuthorizeError,
    RefreshToken,
    TokenError,
)
from mcp.server.auth.settings import ClientRegistrationOptions, RevocationOptions
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken
from pydantic import AnyHttpUrl, AnyUrl
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from kt_config.settings import get_settings
from kt_db.models import (
    OAuthAccessToken,
    OAuthAuthorizationCode,
    OAuthClient,
    OAuthRefreshToken,
)
from kt_mcp.auth import verify_bearer_token
from kt_mcp.dependencies import get_session_factory_cached

logger = logging.getLogger(__name__)

# Expiration defaults
AUTH_CODE_EXPIRY_SECONDS = 5 * 60  # 5 minutes
ACCESS_TOKEN_EXPIRY_SECONDS = 60 * 60  # 1 hour
REFRESH_TOKEN_EXPIRY_SECONDS = 30 * 24 * 60 * 60  # 30 days


def _hash_token(token: str) -> str:
    """Return the hex SHA-256 digest of a token for storage."""
    return hashlib.sha256(token.encode()).hexdigest()


class KnowledgeTreeOAuthProvider(OAuthProvider):
    """OAuth 2.1 provider backed by PostgreSQL."""

    def _session(self) -> AsyncSession:
        factory = get_session_factory_cached()
        return factory()

    # ── Client management ──────────────────────────────────────────────

    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        async with self._session() as session:
            row = await session.get(OAuthClient, client_id)
            if row is None:
                return None
            metadata = dict(row.metadata_)
            metadata["client_id"] = row.client_id
            if row.client_secret:
                metadata["client_secret"] = row.client_secret
            if row.client_id_issued_at is not None:
                metadata["client_id_issued_at"] = row.client_id_issued_at
            if row.client_secret_expires_at is not None:
                metadata["client_secret_expires_at"] = row.client_secret_expires_at
            return OAuthClientInformationFull(**metadata)

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        if client_info.client_id is None:
            raise ValueError("client_id is required for client registration")

        # Validate scopes
        if (
            client_info.scope is not None
            and self.client_registration_options is not None
            and self.client_registration_options.valid_scopes is not None
        ):
            requested = set(client_info.scope.split())
            valid = set(self.client_registration_options.valid_scopes)
            invalid = requested - valid
            if invalid:
                raise ValueError(f"Invalid scopes: {', '.join(invalid)}")

        metadata = client_info.model_dump(
            mode="json",
            exclude={"client_id", "client_secret", "client_id_issued_at", "client_secret_expires_at"},
            exclude_none=True,
        )

        async with self._session() as session:
            row = OAuthClient(
                client_id=client_info.client_id,
                client_secret=client_info.client_secret,
                client_id_issued_at=client_info.client_id_issued_at,
                client_secret_expires_at=client_info.client_secret_expires_at,
                metadata_=metadata,
            )
            await session.merge(row)
            await session.commit()

    # ── Authorization ──────────────────────────────────────────────────

    async def authorize(self, client: OAuthClientInformationFull, params: AuthorizationParams) -> str:
        if client.client_id is None:
            raise AuthorizeError(error="unauthorized_client", error_description="Client ID required")

        code_value = f"pending_{secrets.token_urlsafe(32)}"
        expires_at = time.time() + AUTH_CODE_EXPIRY_SECONDS

        scopes_list = params.scopes if params.scopes is not None else []

        async with self._session() as session:
            row = OAuthAuthorizationCode(
                code=code_value,
                client_id=client.client_id,
                redirect_uri=str(params.redirect_uri),
                redirect_uri_provided_explicitly=params.redirect_uri_provided_explicitly,
                scopes=scopes_list,
                code_challenge=params.code_challenge,
                resource=getattr(params, "resource", None),
                state=params.state,
                expires_at=expires_at,
                csrf_token=None,  # generated when login page is rendered
                user_id=None,  # set after login
            )
            session.add(row)
            await session.commit()

        # Redirect to login page — user must authenticate before code is issued
        settings = get_settings()
        base = settings.mcp_oauth_base_url.rstrip("/")
        return f"{base}/oauth/login?code_id={code_value}"

    # ── Authorization code ─────────────────────────────────────────────

    async def load_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: str
    ) -> AuthorizationCode | None:
        async with self._session() as session:
            row = await session.get(OAuthAuthorizationCode, authorization_code)
            if row is None:
                return None
            if row.client_id != client.client_id:
                return None
            if row.expires_at < time.time():
                await session.delete(row)
                await session.commit()
                return None
            return AuthorizationCode(
                code=row.code,
                client_id=row.client_id,
                redirect_uri=AnyUrl(row.redirect_uri),
                redirect_uri_provided_explicitly=row.redirect_uri_provided_explicitly,
                scopes=row.scopes,
                expires_at=row.expires_at,
                code_challenge=row.code_challenge,
            )

    async def exchange_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: AuthorizationCode
    ) -> OAuthToken:
        async with self._session() as session:
            row = await session.get(OAuthAuthorizationCode, authorization_code.code)
            if row is None:
                raise TokenError("invalid_grant", "Authorization code not found or already used.")

            # Consume the code
            user_id = row.user_id
            await session.delete(row)

            # Create access + refresh tokens (store hashed, return plaintext)
            access_token_value = secrets.token_urlsafe(32)
            refresh_token_value = secrets.token_urlsafe(32)
            access_hash = _hash_token(access_token_value)
            refresh_hash = _hash_token(refresh_token_value)
            access_expires = int(time.time() + ACCESS_TOKEN_EXPIRY_SECONDS)
            refresh_expires = int(time.time() + REFRESH_TOKEN_EXPIRY_SECONDS)

            if client.client_id is None:
                raise TokenError("invalid_client", "Client ID is required")

            session.add(
                OAuthAccessToken(
                    token=access_hash,
                    client_id=client.client_id,
                    user_id=user_id,
                    scopes=authorization_code.scopes,
                    expires_at=access_expires,
                )
            )
            session.add(
                OAuthRefreshToken(
                    token=refresh_hash,
                    client_id=client.client_id,
                    user_id=user_id,
                    scopes=authorization_code.scopes,
                    expires_at=refresh_expires,
                    access_token=access_hash,
                )
            )
            await session.commit()

        return OAuthToken(
            access_token=access_token_value,
            token_type="Bearer",
            expires_in=ACCESS_TOKEN_EXPIRY_SECONDS,
            refresh_token=refresh_token_value,
            scope=" ".join(authorization_code.scopes),
        )

    # ── Access tokens ──────────────────────────────────────────────────

    async def load_access_token(self, token: str) -> AccessToken | None:
        token_hash = _hash_token(token)
        async with self._session() as session:
            row = await session.get(OAuthAccessToken, token_hash)
            if row is None:
                return None
            if row.expires_at is not None and row.expires_at < time.time():
                await session.delete(row)
                await session.commit()
                return None
            return AccessToken(
                token=token,
                client_id=row.client_id,
                scopes=row.scopes,
                expires_at=row.expires_at,
            )

    async def verify_token(self, token: str) -> AccessToken | None:
        """Verify bearer token: try OAuth tokens first, then legacy API tokens."""
        # Try OAuth access token (hashed lookup)
        result = await self.load_access_token(token)
        if result is not None:
            return result

        # Fall back to legacy API token (tokn_... format)
        settings = get_settings()
        if settings.skip_auth:
            return AccessToken(token=token, client_id="skip_auth", scopes=[], expires_at=None)

        async with self._session() as session:
            api_token = await verify_bearer_token(token, session)
            if api_token is not None:
                # Carry API token's graph_slugs as graph:{slug} scopes
                graph_scopes: list[str] = []
                slugs = getattr(api_token, "graph_slugs", None)
                if slugs:
                    graph_scopes = [f"graph:{s}" for s in slugs]
                return AccessToken(token=token, client_id="api_token", scopes=graph_scopes, expires_at=None)

        return None

    # ── Refresh tokens ─────────────────────────────────────────────────

    async def load_refresh_token(self, client: OAuthClientInformationFull, refresh_token: str) -> RefreshToken | None:
        token_hash = _hash_token(refresh_token)
        async with self._session() as session:
            row = await session.get(OAuthRefreshToken, token_hash)
            if row is None:
                return None
            if row.client_id != client.client_id:
                return None
            if row.expires_at is not None and row.expires_at < time.time():
                await session.delete(row)
                await session.commit()
                return None
            return RefreshToken(
                token=refresh_token,
                client_id=row.client_id,
                scopes=row.scopes,
                expires_at=row.expires_at,
            )

    async def exchange_refresh_token(
        self,
        client: OAuthClientInformationFull,
        refresh_token: RefreshToken,
        scopes: list[str],
    ) -> OAuthToken:
        original_scopes = set(refresh_token.scopes)
        requested_scopes = set(scopes)
        if not requested_scopes.issubset(original_scopes):
            raise TokenError(
                "invalid_scope",
                "Requested scopes exceed those authorized by the refresh token.",
            )

        async with self._session() as session:
            # Look up old refresh token by hash
            old_refresh_hash = _hash_token(refresh_token.token)
            old_refresh = await session.get(OAuthRefreshToken, old_refresh_hash)
            user_id = None
            if old_refresh is not None:
                user_id = old_refresh.user_id
                if old_refresh.access_token:
                    old_access = await session.get(OAuthAccessToken, old_refresh.access_token)
                    if old_access:
                        await session.delete(old_access)
                await session.delete(old_refresh)

            # Issue new tokens (store hashed, return plaintext)
            new_access_value = secrets.token_urlsafe(32)
            new_refresh_value = secrets.token_urlsafe(32)
            new_access_hash = _hash_token(new_access_value)
            new_refresh_hash = _hash_token(new_refresh_value)
            access_expires = int(time.time() + ACCESS_TOKEN_EXPIRY_SECONDS)
            refresh_expires = int(time.time() + REFRESH_TOKEN_EXPIRY_SECONDS)

            if client.client_id is None:
                raise TokenError("invalid_client", "Client ID is required")

            session.add(
                OAuthAccessToken(
                    token=new_access_hash,
                    client_id=client.client_id,
                    user_id=user_id,
                    scopes=scopes,
                    expires_at=access_expires,
                )
            )
            session.add(
                OAuthRefreshToken(
                    token=new_refresh_hash,
                    client_id=client.client_id,
                    user_id=user_id,
                    scopes=scopes,
                    expires_at=refresh_expires,
                    access_token=new_access_hash,
                )
            )
            await session.commit()

        return OAuthToken(
            access_token=new_access_value,
            token_type="Bearer",
            expires_in=ACCESS_TOKEN_EXPIRY_SECONDS,
            refresh_token=new_refresh_value,
            scope=" ".join(scopes),
        )

    # ── Revocation ─────────────────────────────────────────────────────

    async def revoke_token(self, token: AccessToken | RefreshToken) -> None:
        token_hash = _hash_token(token.token)
        async with self._session() as session:
            if isinstance(token, AccessToken):
                access_row = await session.get(OAuthAccessToken, token_hash)
                if access_row:
                    # Also revoke associated refresh token
                    result = await session.execute(
                        select(OAuthRefreshToken).where(OAuthRefreshToken.access_token == token_hash)
                    )
                    for rt in result.scalars().all():
                        await session.delete(rt)
                    await session.delete(access_row)
            elif isinstance(token, RefreshToken):
                refresh_row = await session.get(OAuthRefreshToken, token_hash)
                if refresh_row:
                    if refresh_row.access_token:
                        access_row = await session.get(OAuthAccessToken, refresh_row.access_token)
                        if access_row:
                            await session.delete(access_row)
                    await session.delete(refresh_row)
            await session.commit()

    # ── Cleanup ────────────────────────────────────────────────────────

    async def cleanup_expired(self) -> dict[str, int]:
        """Delete expired authorization codes, access tokens, and refresh tokens.

        Returns a dict with counts of deleted rows per table.
        """
        now = time.time()
        now_int = int(now)
        counts: dict[str, int] = {}

        async with self._session() as session:
            # Expired authorization codes
            result = await session.execute(
                select(OAuthAuthorizationCode).where(OAuthAuthorizationCode.expires_at < now)
            )
            rows = result.scalars().all()
            for row in rows:
                await session.delete(row)
            counts["authorization_codes"] = len(rows)

            # Expired access tokens
            result = await session.execute(
                select(OAuthAccessToken).where(
                    OAuthAccessToken.expires_at.isnot(None),
                    OAuthAccessToken.expires_at < now_int,
                )
            )
            rows = result.scalars().all()
            for row in rows:
                await session.delete(row)
            counts["access_tokens"] = len(rows)

            # Expired refresh tokens
            result = await session.execute(
                select(OAuthRefreshToken).where(
                    OAuthRefreshToken.expires_at.isnot(None),
                    OAuthRefreshToken.expires_at < now_int,
                )
            )
            rows = result.scalars().all()
            for row in rows:
                await session.delete(row)
            counts["refresh_tokens"] = len(rows)

            await session.commit()

        total = sum(counts.values())
        if total > 0:
            logger.info("OAuth cleanup: removed %d expired rows: %s", total, counts)
        return counts


def create_oauth_provider() -> KnowledgeTreeOAuthProvider:
    """Create the OAuth provider with settings from config."""
    settings = get_settings()
    return KnowledgeTreeOAuthProvider(
        base_url=AnyHttpUrl(settings.mcp_oauth_base_url),
        client_registration_options=ClientRegistrationOptions(
            enabled=True,
            valid_scopes=None,  # accept any scopes
        ),
        revocation_options=RevocationOptions(
            enabled=True,
        ),
    )
