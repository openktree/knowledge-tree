"""Plugin declaration for backend-engine-search-providers.

Single plugin exposing Serper, Brave, and OpenAlex as three
SearchProviderContributions.
"""

from __future__ import annotations

from collections.abc import Iterable

from kt_config.plugin import (
    BackendEnginePlugin,
    SearchProviderContribution,
)


class SearchProvidersBackendEnginePlugin(BackendEnginePlugin):
    """Contributes Serper / Brave / OpenAlex search providers."""

    plugin_id = "backend-engine-search-providers"

    def get_search_providers(self) -> Iterable[SearchProviderContribution]:
        from kt_plugin_be_search_providers.providers.brave import BraveSearchProvider
        from kt_plugin_be_search_providers.providers.openalex import OpenAlexSearchProvider
        from kt_plugin_be_search_providers.providers.serper import SerperSearchProvider
        from kt_plugin_be_search_providers.settings import get_search_providers_settings

        settings = get_search_providers_settings()

        yield SearchProviderContribution(
            provider_id="serper",
            factory=lambda: SerperSearchProvider(settings.serper_key),
            is_available=lambda: bool(settings.serper_key),
        )
        yield SearchProviderContribution(
            provider_id="brave_search",
            factory=lambda: BraveSearchProvider(settings.brave_key),
            is_available=lambda: bool(settings.brave_key),
        )
        yield SearchProviderContribution(
            provider_id="openalex",
            factory=lambda: OpenAlexSearchProvider(mailto=settings.openalex_mailto or None),
        )
