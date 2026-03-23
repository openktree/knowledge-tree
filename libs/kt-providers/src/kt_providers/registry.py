import asyncio
from typing import overload

from kt_config.types import RawSearchResult
from kt_providers.base import KnowledgeProvider


class ProviderRegistry:
    """Registry for knowledge providers."""

    def __init__(self) -> None:
        self._providers: dict[str, KnowledgeProvider] = {}

    def register(self, provider: KnowledgeProvider) -> None:
        self._providers[provider.provider_id] = provider

    def get(self, provider_id: str) -> KnowledgeProvider | None:
        return self._providers.get(provider_id)

    def get_all(self) -> list[KnowledgeProvider]:
        return list(self._providers.values())

    @overload
    async def search_all(self, query: str, max_results: int = 10) -> list[RawSearchResult]: ...

    @overload
    async def search_all(self, query: list[str], max_results: int = 10) -> dict[str, list[RawSearchResult]]: ...

    async def search_all(
        self, query: str | list[str], max_results: int = 10
    ) -> list[RawSearchResult] | dict[str, list[RawSearchResult]]:
        """Search all providers and return deduplicated results.

        When given a single string, returns list[RawSearchResult] (backwards compatible).
        When given a list of strings, runs all queries in parallel via asyncio.gather
        and returns dict[str, list[RawSearchResult]] keyed by query.
        """
        if isinstance(query, str):
            return await self._search_single(query, max_results)

        # Multiple queries — run in parallel
        tasks = [self._search_single(q, max_results) for q in query]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        out: dict[str, list[RawSearchResult]] = {}
        for q, result in zip(query, results):
            if isinstance(result, BaseException):
                out[q] = []
            else:
                out[q] = result
        return out

    async def _search_single(self, query: str, max_results: int) -> list[RawSearchResult]:
        """Search all providers for a single query and return deduplicated results."""
        all_results: list[RawSearchResult] = []
        seen_uris: set[str] = set()

        for provider in self._providers.values():
            if await provider.is_available():
                results = await provider.search(query, max_results)
                for result in results:
                    if result.uri not in seen_uris:
                        seen_uris.add(result.uri)
                        all_results.append(result)

        return all_results
