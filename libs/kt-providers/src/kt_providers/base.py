from abc import ABC, abstractmethod

from kt_config.types import RawSearchResult


class KnowledgeProvider(ABC):
    """Abstract base class for knowledge providers."""

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
