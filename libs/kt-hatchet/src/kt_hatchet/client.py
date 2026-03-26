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
            f"Failed to dispatch workflow '{workflow_name}'. "
            f"Ensure the worker is running (just worker). Error: {exc}"
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
            f"Failed to run workflow '{workflow_name}'. "
            f"Ensure the worker is running (just worker). Error: {exc}"
        ) from exc
