"""FastAPI adapters for kt-flags.

- ``get_flags`` — dependency that yields the process-wide ``FlagClient``.
- ``FlagEvalContextMiddleware`` — raw ASGI middleware that derives an
  ``EvalContext`` from the request (user / tenant / graph / environment)
  and stashes it on ``request.state.flag_context``. Raw ASGI (not
  ``BaseHTTPMiddleware``) so streaming responses like SSE pass through
  unbuffered.

Auth note: this middleware reads ``request.state.user`` opportunistically.
The project's authentication runs as a FastAPI dependency (fastapi-users
``Depends``), which fires *after* middleware, so ``request.state.user``
will usually be ``None`` at middleware-execute time. That is intentional
for Phase 0 — no targeting rules consume ``targeting_key`` yet. When a
DB-backed provider lands with per-user rules, either promote auth into
middleware or re-derive ``user_id`` inside each dependency that needs it
and pass a fresh ``EvalContext`` explicitly to the flag client.
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING, Any

from kt_flags.client import FlagClient, get_flag_client
from kt_flags.context import EvalContext, build_eval_context

if TYPE_CHECKING:
    from starlette.types import ASGIApp, Receive, Scope, Send

logger = logging.getLogger(__name__)


def get_flags() -> FlagClient:
    """FastAPI dependency — returns the process-wide ``FlagClient``."""
    return get_flag_client()


def _extract_user_id(scope: "Scope") -> str | None:
    state = scope.get("state") or {}
    user = state.get("user") if isinstance(state, dict) else None
    if user is None:
        return None
    user_id = getattr(user, "id", None)
    return str(user_id) if user_id is not None else None


_GRAPH_PATH_RE = re.compile(r"^/(?:api/v\d+/)?graphs/(?P<graph_id>[^/]+)")


def _extract_graph_id(scope: "Scope") -> str | None:
    # Path params aren't populated at middleware time (Starlette resolves
    # them during routing, which fires after us), so read the raw path.
    path = scope.get("path", "")
    if isinstance(path, str):
        match = _GRAPH_PATH_RE.match(path)
        if match:
            return match.group("graph_id")
    for name, value in scope.get("headers", ()):
        if name == b"x-graph-id" and value:
            return value.decode("latin-1") or None
    return None


class FlagEvalContextMiddleware:
    """Raw ASGI middleware that attaches an ``EvalContext`` per request.

    No ``BaseHTTPMiddleware`` — it buffers response bodies and trips SSE
    streaming in the synthesis / super-synthesis workflows this project
    depends on. Raw ASGI lets scope flow unmodified.
    """

    def __init__(self, app: "ASGIApp") -> None:
        self.app = app

    async def __call__(self, scope: "Scope", receive: "Receive", send: "Send") -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return
        try:
            ctx: EvalContext = build_eval_context(
                user_id=_extract_user_id(scope),
                graph_id=_extract_graph_id(scope),
            )
        except Exception:  # noqa: BLE001 — defensive at request boundary
            logger.exception("kt-flags: failed to build EvaluationContext")
            ctx = build_eval_context()
        state = scope.get("state")
        if isinstance(state, dict):
            state["flag_context"] = ctx
        else:
            scope["state"] = {"flag_context": ctx}
        await self.app(scope, receive, send)


def install_middleware(app: Any) -> None:
    """Register ``FlagEvalContextMiddleware`` on a FastAPI / Starlette app."""
    app.add_middleware(FlagEvalContextMiddleware)
