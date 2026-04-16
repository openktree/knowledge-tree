"""Plugin-local settings for search providers.

Env vars use the ``SEARCH_PROVIDERS_`` prefix, e.g.
``SEARCH_PROVIDERS_SERPER_KEY``, ``SEARCH_PROVIDERS_BRAVE_KEY``.
Falls back to the legacy ``SERPER_KEY`` / ``BRAVE_KEY`` / ``OPENALEX_MAILTO``
env vars for backwards compatibility.
"""

from __future__ import annotations

import os
from functools import lru_cache

from pydantic_settings import BaseSettings


class SearchProvidersSettings(BaseSettings):
    model_config = {"env_prefix": "SEARCH_PROVIDERS_"}

    serper_key: str = ""
    brave_key: str = ""
    openalex_mailto: str = ""

    def model_post_init(self, __context: object) -> None:
        if not self.serper_key:
            self.serper_key = os.environ.get("SERPER_KEY", "")
        if not self.brave_key:
            self.brave_key = os.environ.get("BRAVE_KEY", "")
        if not self.openalex_mailto:
            self.openalex_mailto = os.environ.get("OPENALEX_MAILTO", "")


@lru_cache(maxsize=1)
def get_search_providers_settings() -> SearchProvidersSettings:
    return SearchProvidersSettings()
