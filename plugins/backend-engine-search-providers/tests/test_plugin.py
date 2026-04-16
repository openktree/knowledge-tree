"""Plugin declaration tests."""

from kt_plugin_be_search_providers.plugin import SearchProvidersBackendEnginePlugin


def test_plugin_identity() -> None:
    plugin = SearchProvidersBackendEnginePlugin()
    assert plugin.plugin_id == "backend-engine-search-providers"


def test_plugin_yields_three_providers() -> None:
    plugin = SearchProvidersBackendEnginePlugin()
    contribs = list(plugin.get_search_providers())
    ids = [c.provider_id for c in contribs]
    assert ids == ["serper", "brave_search", "openalex"]


def test_plugin_has_no_database_or_extractor() -> None:
    plugin = SearchProvidersBackendEnginePlugin()
    assert plugin.get_database() is None
    assert plugin.get_entity_extractor() is None
