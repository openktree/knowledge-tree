"""``maybe_build_fetch_registry`` gates on ``feature.full_text_fetch``."""

from __future__ import annotations

from unittest.mock import patch

import pytest

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


def test_default_pulls_from_flag_registry(monkeypatch: pytest.MonkeyPatch) -> None:
    """``maybe_build_fetch_registry`` must source its default from FLAG_REGISTRY,
    not a hardcoded literal, so future spec changes propagate.

    Simulates an unknown flag key (SettingsProvider returns the caller's
    default + FLAG_NOT_FOUND) by monkey-patching the spec's default to
    False. With the spec default False and no Settings backing, the gate
    must close.
    """
    from dataclasses import replace

    from kt_flags.registry import FLAG_REGISTRY

    spec = FLAG_REGISTRY["feature.full_text_fetch"]
    # Replace the backed settings_field with None so the provider falls
    # through to spec.default, then flip the default to False.
    monkeypatch.setitem(
        FLAG_REGISTRY,
        "feature.full_text_fetch",
        replace(spec, settings_field=None, default=False),
    )
    with patch("kt_providers.fetch.builder.build_fetch_registry") as builder:
        result = maybe_build_fetch_registry(Settings())
    assert result is None
    assert builder.call_count == 0
