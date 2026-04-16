"""Hatchet adapters for kt-flags.

Workers call :func:`init_worker` once during lifespan setup to guarantee
the flag client is ready before any task executes. Tasks that need
targeting call :func:`context_from_task` with their ``ctx.input`` payload
to build an ``EvalContext`` from the workflow input.
"""

from __future__ import annotations

import logging
from typing import Any

from kt_flags.client import FlagClient, get_flag_client
from kt_flags.context import EvalContext, build_eval_context

logger = logging.getLogger(__name__)


def init_worker() -> FlagClient:
    """Initialize the flag client at worker boot.

    Returns the singleton so the caller can log the provider metadata or
    stash it on ``WorkerState`` if desired.
    """
    client = get_flag_client()
    logger.info("kt-flags: worker flag client ready")
    return client


def _safe_str(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)


def context_from_task(payload: Any) -> EvalContext:
    """Build an ``EvalContext`` from a Hatchet workflow input payload.

    Accepts either a Pydantic model or a plain dict. Missing fields are
    tolerated — absent context is valid in Phase 0.
    """
    if hasattr(payload, "model_dump"):
        data: dict[str, Any] = payload.model_dump()
    elif isinstance(payload, dict):
        data = payload
    else:
        data = {}

    return build_eval_context(
        user_id=_safe_str(data.get("user_id")),
        tenant_id=_safe_str(data.get("tenant_id")),
        graph_id=_safe_str(data.get("graph_id")),
        environment=_safe_str(data.get("environment")),
    )
