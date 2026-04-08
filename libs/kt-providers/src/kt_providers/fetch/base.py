"""Abstract base class for content-fetcher providers.

Each provider knows how to retrieve a single URL.  The `FetchProviderRegistry`
composes one or more providers into a fallback chain so we can try a cheap
strategy first (`httpx`) and progressively fall back to heavier ones
(`curl_cffi`, `flaresolverr`) for sites that block plain HTTP clients.

Mirrors the existing `KnowledgeProvider` ABC in `kt_providers.base` so the
two registry concepts feel symmetric.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from kt_providers.fetch.types import FetchResult


class ContentFetcherProvider(ABC):
    """Abstract base class for content-fetcher providers."""

    @property
    @abstractmethod
    def provider_id(self) -> str:
        """Unique identifier for this provider (e.g. ``"httpx"``)."""

    @abstractmethod
    async def is_available(self) -> bool:
        """Whether this provider is currently usable.

        Should return False when the provider's required configuration or
        dependency is missing — e.g. the FlareSolverr URL is unset, or the
        `curl_cffi` package is not installed.  Providers that are always
        usable (like the plain `httpx` provider) just return True.
        """

    @abstractmethod
    async def fetch(self, uri: str) -> FetchResult:
        """Fetch a single URI and return a `FetchResult`.

        Implementations MUST NOT raise on transport-level failures (timeouts,
        4xx/5xx, malformed bodies, etc.) — they should catch those and put a
        descriptive string in `FetchResult.error`.  Letting exceptions escape
        is reserved for genuinely unexpected programming errors.
        """

    async def close(self) -> None:
        """Release any resources held by the provider (HTTP clients, etc.).

        Default no-op so cheap providers don't need to implement it.
        """
        return None
