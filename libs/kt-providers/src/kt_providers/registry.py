import asyncio
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Callable, overload

from kt_config.types import RawSearchResult
from kt_core_engine_api.search import KnowledgeProvider


@dataclass(frozen=True)
class ExtraProviderFactory:
    """External provider factory registered by services at startup.

    Keeps ``kt-providers`` free of plugin-framework awareness: services
    (which already know about plugins) translate plugin contributions
    into these generic tuples and push them here before the worker
    lifespan runs.
    """

    name: str                                      # unique id used for selection + logging
    provider_id: str                               # matched against settings.default_search_provider
    factory: Callable[[], KnowledgeProvider]
    is_available: Callable[[], bool] = lambda: True


_EXTRA_PROVIDER_FACTORIES: list[ExtraProviderFactory] = []


def register_extra_provider_factory(factory: ExtraProviderFactory) -> None:
    """Register an extra provider factory for the worker lifespan to pick up.

    Idempotent by ``name`` — re-registering the same name is a no-op.
    """
    for existing in _EXTRA_PROVIDER_FACTORIES:
        if existing.name == factory.name:
            return
    _EXTRA_PROVIDER_FACTORIES.append(factory)


def iter_extra_provider_factories() -> Iterable[ExtraProviderFactory]:
    """Iterate every registered extra provider factory."""
    return tuple(_EXTRA_PROVIDER_FACTORIES)


def clear_extra_provider_factories() -> None:
    """Remove every registered extra factory. Intended for test isolation."""
    _EXTRA_PROVIDER_FACTORIES.clear()


def bridge_plugin_search_providers() -> None:
    """Bridge every plugin ``SearchProviderContribution`` into the extras list.

    Call once at startup, after ``load_default_plugins()``. Keeps
    ``kt-hatchet`` and the API dependency layer unaware of the plugin
    framework — they only see generic ``ExtraProviderFactory`` entries.
    """
    from kt_config.plugin import plugin_registry

    for contrib in plugin_registry.iter_search_providers():
        register_extra_provider_factory(
            ExtraProviderFactory(
                name=contrib.provider_id,
                provider_id=contrib.provider_id,
                factory=contrib.factory,
                is_available=contrib.is_available,
            )
        )


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
