"""Hatchet client singleton.

The Hatchet SDK reads ``HATCHET_CLIENT_TOKEN`` and
``HATCHET_CLIENT_TLS_STRATEGY`` from the environment automatically.
We load ``.env`` here so the vars are available at module-import time.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

# Load .env before any Hatchet client reads env vars.
# This matches the path used by config.py's pydantic-settings.
_ENV_PATH = Path(os.environ.get("KT_ENV_FILE", str(Path(__file__).resolve().parents[4] / ".env")))
load_dotenv(_ENV_PATH)

from hatchet_sdk import Hatchet  # noqa: E402


@lru_cache(maxsize=1)
def get_hatchet() -> Hatchet:
    """Return a cached Hatchet client instance."""
    return Hatchet()


async def get_workflow_run_details(workflow_run_id: str) -> object:
    """Fetch full workflow run details (status + task tree) from Hatchet.

    Returns a ``V1WorkflowRunDetails`` object with ``.run`` (status, timestamps),
    ``.tasks`` (list of ``V1TaskSummary``), and ``.task_events``.

    Raises ``RuntimeError`` if the Hatchet API is unreachable.
    """
    import logging

    logger = logging.getLogger(__name__)
    h = get_hatchet()
    try:
        return await h.runs.aio_get(workflow_run_id)
    except Exception as exc:
        logger.warning("Failed to fetch workflow run %s: %s", workflow_run_id, exc)
        raise RuntimeError(f"Failed to fetch workflow run '{workflow_run_id}': {exc}") from exc


def _ensure_dict(input: dict | object) -> dict:
    """Coerce *input* to a plain dict.

    Accepts ``dict`` (pass-through) or a Pydantic ``BaseModel`` (calls
    ``.model_dump()``).  Raises ``TypeError`` for anything else.
    """
    if isinstance(input, dict):
        return input
    # Pydantic v2 BaseModel — duck-type check avoids hard import
    if hasattr(input, "model_dump"):
        return input.model_dump()  # type: ignore[union-attr]
    raise TypeError(f"Expected dict or Pydantic model, got {type(input).__name__}")


def inject_graph_id(input: dict | object, graph_id: str | None) -> dict:
    """Return a plain dict with ``graph_id`` merged in when non-None.

    Safe to call from both API and worker code — lives in kt_hatchet so
    neither side crosses a service boundary.
    """
    d = _ensure_dict(input)
    if graph_id is not None:
        return {**d, "graph_id": graph_id}
    return d


async def dispatch_workflow(
    workflow_name: str,
    input: dict,
    additional_metadata: dict | None = None,
) -> str:
    """Dispatch a Hatchet workflow by name and return the workflow run ID.

    This is the preferred way for the API to trigger workflows — it avoids
    importing worker packages directly (which would create cross-service
    dependencies).

    Uses the admin gRPC client (``admin.aio_run_workflow``) which is what
    ``Workflow.aio_run_no_wait()`` uses internally.

    Raises ``RuntimeError`` if Hatchet rejects the request.
    """
    import json
    import logging

    from hatchet_sdk import TriggerWorkflowOptions

    logger = logging.getLogger(__name__)
    h = get_hatchet()

    options = TriggerWorkflowOptions()
    if additional_metadata:
        options.additional_metadata = additional_metadata

    try:
        ref = await h._client.admin.aio_run_workflow(
            workflow_name=workflow_name,
            input=json.dumps(input),
            options=options,
        )
        return ref.workflow_run_id
    except Exception as exc:
        logger.error("Failed to dispatch workflow %s: %s", workflow_name, exc)
        raise RuntimeError(
            f"Failed to dispatch workflow '{workflow_name}'. Ensure the worker is running (just worker). Error: {exc}"
        ) from exc


async def run_workflow(
    workflow_name: str,
    input: dict,
    additional_metadata: dict | None = None,
) -> dict:
    """Dispatch a workflow and wait for its result.

    Returns the workflow output dict.
    """
    import json
    import logging

    from hatchet_sdk import TriggerWorkflowOptions

    logger = logging.getLogger(__name__)
    h = get_hatchet()

    options = TriggerWorkflowOptions()
    if additional_metadata:
        options.additional_metadata = additional_metadata

    try:
        ref = await h._client.admin.aio_run_workflow(
            workflow_name=workflow_name,
            input=json.dumps(input),
            options=options,
        )
        return ref.result()
    except Exception as exc:
        logger.error("Failed to run workflow %s: %s", workflow_name, exc)
        raise RuntimeError(
            f"Failed to run workflow '{workflow_name}'. Ensure the worker is running (just worker). Error: {exc}"
        ) from exc
