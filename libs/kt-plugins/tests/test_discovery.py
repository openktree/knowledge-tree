"""Tests for plugin discovery via entry points."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from kt_plugins.discovery import discover_plugins, validate_dependencies
from kt_plugins.errors import PluginLoadError
from kt_plugins.manifest import PluginManifest


def _make_manifest(plugin_id: str, deps: list[str] | None = None) -> PluginManifest:
    return PluginManifest(
        id=plugin_id,
        name=plugin_id.title(),
        version="1.0.0",
        dependencies=deps or [],
    )


def _make_entry_point(name: str, manifest: PluginManifest) -> MagicMock:
    ep = MagicMock()
    ep.name = name
    ep.load.return_value = manifest
    return ep


def test_discover_plugins_finds_manifests() -> None:
    m1 = _make_manifest("billing")
    m2 = _make_manifest("sso")
    eps = [_make_entry_point("billing", m1), _make_entry_point("sso", m2)]

    with patch("kt_plugins.discovery.importlib.metadata.entry_points", return_value=eps):
        result = discover_plugins()

    assert len(result) == 2
    assert result[0].id == "billing"
    assert result[1].id == "sso"


def test_discover_plugins_filters_by_enabled() -> None:
    m1 = _make_manifest("billing")
    m2 = _make_manifest("sso")
    eps = [_make_entry_point("billing", m1), _make_entry_point("sso", m2)]

    with patch("kt_plugins.discovery.importlib.metadata.entry_points", return_value=eps):
        result = discover_plugins(enabled=["billing"])

    assert len(result) == 1
    assert result[0].id == "billing"


def test_discover_plugins_skips_bad_entry_points() -> None:
    bad_ep = MagicMock()
    bad_ep.name = "broken"
    bad_ep.load.side_effect = ImportError("no module")

    good = _make_manifest("good")
    good_ep = _make_entry_point("good", good)

    with patch("kt_plugins.discovery.importlib.metadata.entry_points", return_value=[bad_ep, good_ep]):
        result = discover_plugins()

    assert len(result) == 1
    assert result[0].id == "good"


def test_discover_plugins_skips_non_manifest() -> None:
    ep = MagicMock()
    ep.name = "notplugin"
    ep.load.return_value = "not a manifest"

    with patch("kt_plugins.discovery.importlib.metadata.entry_points", return_value=[ep]):
        result = discover_plugins()

    assert result == []


def test_discover_empty() -> None:
    with patch("kt_plugins.discovery.importlib.metadata.entry_points", return_value=[]):
        result = discover_plugins()
    assert result == []


def test_validate_dependencies_ok() -> None:
    manifests = [
        _make_manifest("a", deps=["b"]),
        _make_manifest("b"),
    ]
    validate_dependencies(manifests)  # should not raise


def test_validate_dependencies_missing() -> None:
    manifests = [_make_manifest("a", deps=["missing"])]
    with pytest.raises(PluginLoadError, match="missing"):
        validate_dependencies(manifests)
