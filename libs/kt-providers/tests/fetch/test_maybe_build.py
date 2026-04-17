"""``maybe_build_fetch_registry`` gates on ``feature.full_text_fetch``."""

from __future__ import annotations

from unittest.mock import patch

from kt_config.settings import Settings
from kt_flags.testing import override_flags
from kt_providers.fetch import maybe_build_fetch_registry


def test_flag_off_returns_none() -> None:
    with override_flags({"feature.full_text_fetch": False}):
        result = maybe_build_fetch_registry(Settings())
    assert result is None


def test_flag_on_builds_registry() -> None:
    with patch("kt_providers.fetch.builder.build_fetch_registry") as builder:
        builder.return_value = "fake"
        with override_flags({"feature.full_text_fetch": True}):
            result = maybe_build_fetch_registry(Settings())
    assert result == "fake"
    assert builder.call_count == 1


def test_default_pulls_from_registry() -> None:
    # No override — should resolve through SettingsProvider using Settings().enable_full_text_fetch.
    from kt_flags.registry import FLAG_REGISTRY

    spec = FLAG_REGISTRY["feature.full_text_fetch"]
    assert spec.default is True  # sanity — expected spec default
    with patch("kt_providers.fetch.builder.build_fetch_registry") as builder:
        builder.return_value = "reg"
        result = maybe_build_fetch_registry(Settings(enable_full_text_fetch=True))
    assert result == "reg"
