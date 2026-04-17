"""Plugin discovery — entry-point + legacy-target fallback + dep validation."""

from __future__ import annotations

import pytest

from kt_plugins import PluginLoadError, PluginManifest
from kt_plugins.discovery import discover_plugins, validate_dependencies


def test_discover_includes_in_tree_legacy_plugins() -> None:
    """In-tree concept-extractor + search-providers must be found.

    These declare ``[project.entry-points."kt.plugins"]`` so they load
    via the entry-point path, not the legacy-targets fallback.
    """
    manifests = discover_plugins()
    ids = {m.id for m in manifests}
    assert "backend-engine-concept-extractor" in ids
    assert "backend-engine-search-providers" in ids


def test_enabled_allowlist_filters() -> None:
    manifests = discover_plugins(enabled=["backend-engine-search-providers"])
    ids = {m.id for m in manifests}
    assert ids == {"backend-engine-search-providers"}


def test_validate_dependencies_rejects_missing_dep() -> None:
    a = PluginManifest(id="a", dependencies=["missing"])
    with pytest.raises(PluginLoadError, match="missing"):
        validate_dependencies([a])


def test_validate_dependencies_rejects_out_of_order() -> None:
    a = PluginManifest(id="a", dependencies=["b"])
    b = PluginManifest(id="b")
    with pytest.raises(PluginLoadError):
        validate_dependencies([a, b])  # a needs b but b loads later


def test_validate_dependencies_accepts_correct_order() -> None:
    b = PluginManifest(id="b")
    a = PluginManifest(id="a", dependencies=["b"])
    validate_dependencies([b, a])  # must not raise
