"""OAuth login page for the MCP server.

Handles user authentication during the OAuth 2.1 authorization flow.
When the OAuthProvider.authorize() method is called, it stores a pending
authorization code and redirects here. The user logs in, and on success
the pending code is finalized and the user is redirected back to the
OAuth client (e.g., Claude Web) with the authorization code.
"""

from __future__ import annotations

import hmac
import html
import logging
import secrets
import time
from collections import defaultdict

import bcrypt
from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from mcp.server.auth.provider import construct_redirect_uri
from sqlalchemy import select

from kt_db.models import OAuthAuthorizationCode, User
from kt_mcp.dependencies import get_session_factory_cached
from kt_mcp.oauth_provider import AUTH_CODE_EXPIRY_SECONDS

logger = logging.getLogger(__name__)

oauth_login_router = APIRouter(prefix="/oauth", tags=["oauth"])

# ── Simple in-memory rate limiter for login attempts ──────────────────
_MAX_ATTEMPTS = 5  # max failed attempts per IP
_WINDOW_SECONDS = 300  # 5-minute window
_MAX_TRACKED_IPS = 10_000  # cap to prevent unbounded memory growth
_failed_attempts: dict[str, list[float]] = defaultdict(list)


def _is_rate_limited(ip: str) -> bool:
    """Check if an IP has exceeded the login attempt limit."""
    now = time.monotonic()
    attempts = _failed_attempts[ip]
    # Prune old entries
    _failed_attempts[ip] = [t for t in attempts if now - t < _WINDOW_SECONDS]
    if not _failed_attempts[ip]:
        del _failed_attempts[ip]
        return False
    return len(_failed_attempts[ip]) >= _MAX_ATTEMPTS


def _record_failed_attempt(ip: str) -> None:
    """Record a failed login attempt for rate limiting."""
    # Evict oldest IPs if we've hit the cap
    if len(_failed_attempts) >= _MAX_TRACKED_IPS and ip not in _failed_attempts:
        now = time.monotonic()
        # Prune all expired entries first
        expired = [k for k, v in _failed_attempts.items() if all(now - t >= _WINDOW_SECONDS for t in v)]
        for k in expired:
            del _failed_attempts[k]
        # If still over cap, drop the oldest entries
        if len(_failed_attempts) >= _MAX_TRACKED_IPS:
            to_drop = len(_failed_attempts) - _MAX_TRACKED_IPS + 1
            for k in list(_failed_attempts)[:to_drop]:
                del _failed_attempts[k]
    _failed_attempts[ip].append(time.monotonic())


_LOGIN_PAGE_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Knowledge Tree — Authorize</title>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ font-family: system-ui, -apple-system, sans-serif; background: #0f172a; color: #e2e8f0; display: flex; align-items: center; justify-content: center; min-height: 100vh; }}
  .card {{ background: #1e293b; border-radius: 12px; padding: 2rem; width: 100%; max-width: 400px; box-shadow: 0 4px 24px rgba(0,0,0,0.3); }}
  h1 {{ font-size: 1.25rem; margin-bottom: 0.5rem; color: #f8fafc; }}
  p {{ font-size: 0.875rem; color: #94a3b8; margin-bottom: 1.5rem; }}
  label {{ display: block; font-size: 0.875rem; margin-bottom: 0.25rem; color: #cbd5e1; }}
  input[type="email"], input[type="password"] {{ width: 100%; padding: 0.625rem; border: 1px solid #334155; border-radius: 6px; background: #0f172a; color: #f8fafc; font-size: 0.875rem; margin-bottom: 1rem; }}
  input:focus {{ outline: none; border-color: #3b82f6; box-shadow: 0 0 0 2px rgba(59,130,246,0.3); }}
  button {{ width: 100%; padding: 0.625rem; background: #3b82f6; color: white; border: none; border-radius: 6px; font-size: 0.875rem; font-weight: 500; cursor: pointer; }}
  button:hover {{ background: #2563eb; }}
  .error {{ background: #7f1d1d; color: #fca5a5; padding: 0.75rem; border-radius: 6px; margin-bottom: 1rem; font-size: 0.875rem; }}
  .footer {{ text-align: center; margin-top: 1rem; font-size: 0.75rem; color: #64748b; }}
</style>
</head>
<body>
<div class="card">
  <h1>Authorize Access</h1>
  <p>Sign in to grant access to your Knowledge Tree graph.</p>
  {error}
  <form method="POST" action="/oauth/login">
    <input type="hidden" name="code_id" value="{code_id}">
    <input type="hidden" name="csrf_token" value="{csrf_token}">
    <label for="email">Email</label>
    <input type="email" id="email" name="email" required autofocus>
    <label for="password">Password</label>
    <input type="password" id="password" name="password" required>
    <button type="submit">Authorize</button>
  </form>
  <div class="footer">Knowledge Tree MCP Server</div>
</div>
</body>
</html>"""


@oauth_login_router.get("/login")
async def login_page(code_id: str) -> HTMLResponse:
    """Show the login form for OAuth authorization."""
    factory = get_session_factory_cached()
    async with factory() as session:
        row = await session.get(OAuthAuthorizationCode, code_id)
        if row is None or row.expires_at < time.time():
            return HTMLResponse(
                content="<h1>Authorization request expired or invalid.</h1><p>Please try connecting again.</p>",
                status_code=400,
            )

        # Generate and persist a CSRF token for this login form
        csrf_token = secrets.token_urlsafe(32)
        row.csrf_token = csrf_token
        await session.commit()

    page = _LOGIN_PAGE_HTML.format(code_id=html.escape(code_id), csrf_token=html.escape(csrf_token), error="")
    return HTMLResponse(content=page)


@oauth_login_router.post("/login", response_model=None)
async def login_submit(
    request: Request,
    code_id: str = Form(...),
    csrf_token: str = Form(...),
    email: str = Form(...),
    password: str = Form(...),
) -> RedirectResponse | HTMLResponse:
    """Validate credentials and complete the authorization flow."""
    client_ip = request.client.host if request.client else "unknown"

    # Rate limiting: block after too many failed attempts from this IP
    if _is_rate_limited(client_ip):
        return HTMLResponse(
            content="<h1>Too many login attempts.</h1><p>Please try again later.</p>",
            status_code=429,
        )

    factory = get_session_factory_cached()

    async with factory() as session:
        # Load the pending authorization code
        row = await session.get(OAuthAuthorizationCode, code_id)
        if row is None or row.expires_at < time.time():
            return HTMLResponse(
                content="<h1>Authorization request expired.</h1><p>Please try connecting again.</p>",
                status_code=400,
            )

        # Verify CSRF token (constant-time comparison)
        if row.csrf_token is None or not hmac.compare_digest(row.csrf_token, csrf_token):
            return HTMLResponse(
                content="<h1>Invalid request.</h1><p>Please try connecting again.</p>",
                status_code=403,
            )

        # Verify user credentials
        result = await session.execute(select(User).where(User.email == email))
        user = result.unique().scalar_one_or_none()

        if user is None or not _verify_password(password, user.hashed_password):
            _record_failed_attempt(client_ip)
            page = _LOGIN_PAGE_HTML.format(
                code_id=html.escape(code_id),
                csrf_token=html.escape(csrf_token),
                error='<div class="error">Invalid email or password.</div>',
            )
            return HTMLResponse(content=page, status_code=401)

        if not user.is_active:
            _record_failed_attempt(client_ip)
            page = _LOGIN_PAGE_HTML.format(
                code_id=html.escape(code_id),
                csrf_token=html.escape(csrf_token),
                error='<div class="error">Account is disabled.</div>',
            )
            return HTMLResponse(content=page, status_code=403)

        # Generate the real authorization code: delete pending row, insert new one
        real_code = secrets.token_urlsafe(32)
        redirect_uri = row.redirect_uri
        state = row.state

        new_row = OAuthAuthorizationCode(
            code=real_code,
            client_id=row.client_id,
            redirect_uri=row.redirect_uri,
            redirect_uri_provided_explicitly=row.redirect_uri_provided_explicitly,
            scopes=row.scopes,
            code_challenge=row.code_challenge,
            resource=row.resource,
            state=row.state,
            expires_at=time.time() + AUTH_CODE_EXPIRY_SECONDS,
            csrf_token=None,
            user_id=user.id,
        )
        await session.delete(row)
        await session.flush()
        session.add(new_row)
        await session.commit()

    # Redirect back to the OAuth client with the authorization code
    redirect = construct_redirect_uri(redirect_uri, code=real_code, state=state)
    return RedirectResponse(url=redirect, status_code=302)


def _verify_password(plain: str, hashed: str) -> bool:
    """Verify a password against a bcrypt hash (same algorithm as fastapi-users)."""
    try:
        return bcrypt.checkpw(plain.encode(), hashed.encode())
    except (ValueError, TypeError):
        return False
