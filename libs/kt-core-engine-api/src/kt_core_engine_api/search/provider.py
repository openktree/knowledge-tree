"""Abstract base for knowledge-provider search implementations."""

from __future__ import annotations

from abc import ABC, abstractmethod

from kt_core_engine_api.search.types import RawSearchResult


class KnowledgeProvider(ABC):
    """Abstract base class for knowledge providers."""

    #: Whether results from this provider are considered public — i.e. safe to
    #: cache in / contribute to the shared default graph. Public providers
    #: (web search APIs like Serper, Brave) default to True. Future internal
    #: connectors (Jira, Confluence, SharePoint, etc.) MUST override this to
    #: False so the multigraph public-cache machinery never crosses the
    #: tenancy boundary. Operator overrides live in
    #: ``Settings.search_provider_public_overrides`` and are applied
    #: per-instance, so this is a regular class attribute (not a ``ClassVar``)
    #: to allow instance assignment.
    is_public: bool = True

    @property
    @abstractmethod
    def provider_id(self) -> str:
        """Unique identifier for this provider."""
        ...

    @abstractmethod
    async def search(self, query: str, max_results: int = 10) -> list[RawSearchResult]:
        """Search for content matching the query."""
        ...

    @abstractmethod
    async def is_available(self) -> bool:
        """Check if the provider is currently available."""
        ...

    async def close(self) -> None:
        """Release resources (HTTP clients, connections, etc.).

        Default is a no-op. Implementations holding an ``httpx.AsyncClient``
        or similar should override to call ``await client.aclose()``.
        """
