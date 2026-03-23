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
