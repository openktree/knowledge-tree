"""SettingsProvider resolves flags from kt_config.Settings."""

from __future__ import annotations

import pytest

from kt_config.settings import Settings
from kt_flags.providers.settings_provider import SettingsProvider
from kt_flags.registry import FlagType


def _provider_with(**overrides: object) -> SettingsProvider:
    return SettingsProvider(settings=Settings(**overrides))


def test_resolves_from_settings_field() -> None:
    p = _provider_with(enable_full_text_fetch=False)
    got = p.resolve_boolean_details("feature.full_text_fetch", True)
    assert got.value is False


def test_resolves_default_when_no_settings_field() -> None:
    # plugin.concept_extractor.enabled has settings_field=None → spec.default
    p = _provider_with()
    got = p.resolve_boolean_details("plugin.concept_extractor.enabled", False)
    assert got.value is True


def test_unknown_key_returns_default_with_error_code() -> None:
    from openfeature.exception import ErrorCode

    p = _provider_with()
    got = p.resolve_boolean_details("feature.does_not_exist", False)
    assert got.value is False
    assert got.error_code == ErrorCode.FLAG_NOT_FOUND


def test_type_mismatch_returns_default_with_error_code() -> None:
    from openfeature.exception import ErrorCode

    p = _provider_with()
    got = p.resolve_string_details("feature.full_text_fetch", "fallback")
    assert got.value == "fallback"
    assert got.error_code == ErrorCode.TYPE_MISMATCH


def test_env_override_via_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENABLE_FULL_TEXT_FETCH", "false")
    # Fresh Settings reads env during construction
    p = SettingsProvider(settings=Settings())
    got = p.resolve_boolean_details("feature.full_text_fetch", True)
    assert got.value is False


def test_registry_types_match_resolver(pytestconfig: pytest.Config) -> None:  # noqa: ARG001
    # Smoke check: every BOOLEAN spec resolves via boolean API without error.
    from kt_flags.registry import FLAG_REGISTRY

    p = _provider_with()
    for spec in FLAG_REGISTRY.values():
        if spec.type == FlagType.BOOLEAN:
            got = p.resolve_boolean_details(spec.key, bool(spec.default))
            assert isinstance(got.value, bool)
