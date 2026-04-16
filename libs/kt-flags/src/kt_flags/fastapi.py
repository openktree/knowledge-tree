"""FastAPI adapters for kt-flags.

- ``get_flags`` — dependency that yields the process-wide ``FlagClient``.
- ``attach_eval_context`` — middleware factory that derives an
  ``EvalContext`` from the request (user / tenant / graph / environment)
  and stashes it on ``request.state.flag_context``. Handlers that need
  targeting pass ``request.state.flag_context`` into flag calls.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

from kt_flags.client import FlagClient, get_flag_client
from kt_flags.context import EvalContext, build_eval_context

if TYPE_CHECKING:
    from fastapi import Request
    from starlette.responses import Response

logger = logging.getLogger(__name__)


def get_flags() -> FlagClient:
    """FastAPI dependency — returns the process-wide ``FlagClient``."""
    return get_flag_client()


def _extract_user_id(request: "Request") -> str | None:
    user = getattr(request.state, "user", None)
    if user is None:
        return None
    user_id = getattr(user, "id", None)
    return str(user_id) if user_id is not None else None


def _extract_graph_id(request: "Request") -> str | None:
    # Graph id appears either in path params (``/graphs/{graph_id}/...``)
    # or as a header on multigraph-aware routes. Best-effort — absence is
    # normal and harmless in Phase 0 (no targeting rules read it).
    path_graph = request.path_params.get("graph_id")
    if path_graph:
        return str(path_graph)
    header_graph = request.headers.get("X-Graph-Id")
    return header_graph or None


async def attach_eval_context(
    request: "Request",
    call_next: Callable[["Request"], Awaitable["Response"]],
) -> "Response":
    """Starlette middleware — attach an ``EvalContext`` to every request.

    Failures in context construction must never break the request; log and
    fall back to an empty context so flag evaluation degrades to defaults.
    """
    try:
        ctx: EvalContext = build_eval_context(
            user_id=_extract_user_id(request),
            graph_id=_extract_graph_id(request),
        )
    except Exception:  # noqa: BLE001 — defensive at request boundary
        logger.exception("kt-flags: failed to build EvaluationContext")
        ctx = build_eval_context()
    request.state.flag_context = ctx
    return await call_next(request)


def install_middleware(app: Any) -> None:
    """Register ``attach_eval_context`` on a FastAPI / Starlette app."""
    from starlette.middleware.base import BaseHTTPMiddleware

    app.add_middleware(BaseHTTPMiddleware, dispatch=attach_eval_context)
