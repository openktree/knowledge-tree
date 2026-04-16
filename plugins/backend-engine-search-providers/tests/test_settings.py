"""Verify plugin-local settings load defaults correctly."""

from kt_plugin_be_search_providers.settings import (
    SearchProvidersSettings,
    get_search_providers_settings,
)


def test_defaults_empty():
    s = SearchProvidersSettings()
    assert s.serper_key == ""
    assert s.brave_key == ""
    assert s.openalex_mailto == ""


def test_env_prefix(monkeypatch):
    monkeypatch.setenv("SEARCH_PROVIDERS_SERPER_KEY", "sk-test")
    s = SearchProvidersSettings()
    assert s.serper_key == "sk-test"


def test_legacy_env_fallback(monkeypatch):
    monkeypatch.setenv("SERPER_KEY", "legacy-key")
    s = SearchProvidersSettings()
    assert s.serper_key == "legacy-key"


def test_get_settings_cached():
    a = get_search_providers_settings()
    b = get_search_providers_settings()
    assert a is b
