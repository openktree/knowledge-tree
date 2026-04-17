"""Plugin discovery — importlib.metadata entry points + legacy fallback.

Entry point group: ``kt.plugins``. An entry point may resolve to either
a :class:`PluginManifest` instance (new style) or a
:class:`BackendEnginePlugin` subclass / instance (legacy — auto-wrapped).

Plugins not declaring entry points yet are picked up via the legacy
hardcoded target list — kept so tests can run without
``uv sync --all-packages`` first.
"""

from __future__ import annotations

import importlib
import importlib.metadata
import logging
from collections.abc import Iterable

from kt_plugins.errors import PluginLoadError
from kt_plugins.legacy import BackendEnginePlugin, legacy_to_manifest
from kt_plugins.manifest import PluginManifest

logger = logging.getLogger(__name__)

ENTRY_POINT_GROUP = "kt.plugins"

# Hardcoded fallback for plugins that don't yet declare entry points.
# Each target is (module_path, attribute_name). The attribute can be a
# PluginManifest instance OR a BackendEnginePlugin subclass.
_LEGACY_PLUGIN_TARGETS: list[tuple[str, str]] = [
    ("kt_plugin_be_concept_extractor.plugin", "ConceptExtractorBackendEnginePlugin"),
    ("kt_plugin_be_search_providers.plugin", "SearchProvidersBackendEnginePlugin"),
]


def _coerce_to_manifest(obj: object, source: str) -> PluginManifest | None:
    """Normalize a discovered object into a ``PluginManifest``."""
    if isinstance(obj, PluginManifest):
        return obj
    if isinstance(obj, BackendEnginePlugin):
        return legacy_to_manifest(obj)
    if isinstance(obj, type) and issubclass(obj, BackendEnginePlugin):
        try:
            return legacy_to_manifest(obj())
        except Exception:
            logger.exception("plugin %s: failed to instantiate legacy class", source)
            return None
    logger.warning(
        "plugin %s: entry point is neither PluginManifest nor BackendEnginePlugin (got %s) — skipping",
        source,
        type(obj).__name__,
    )
    return None


def _discover_entry_points() -> list[PluginManifest]:
    manifests: list[PluginManifest] = []
    try:
        eps = importlib.metadata.entry_points(group=ENTRY_POINT_GROUP)
    except Exception:
        logger.exception("entry_points(group=%s) lookup failed", ENTRY_POINT_GROUP)
        return manifests

    for ep in eps:
        try:
            obj = ep.load()
        except Exception:
            logger.exception("plugin entry point %s failed to load", ep.name)
            continue
        manifest = _coerce_to_manifest(obj, f"entry-point:{ep.name}")
        if manifest is not None:
            manifests.append(manifest)
    return manifests


def _discover_legacy_targets() -> list[PluginManifest]:
    manifests: list[PluginManifest] = []
    for module_path, attr_name in _LEGACY_PLUGIN_TARGETS:
        try:
            module = importlib.import_module(module_path)
        except ImportError:
            logger.debug("legacy plugin %s not installed — skipping", module_path)
            continue
        obj = getattr(module, attr_name, None)
        if obj is None:
            logger.warning("legacy plugin %s: attribute %s not found", module_path, attr_name)
            continue
        manifest = _coerce_to_manifest(obj, f"legacy:{module_path}:{attr_name}")
        if manifest is not None:
            manifests.append(manifest)
    return manifests


def discover_plugins(enabled: list[str] | None = None) -> list[PluginManifest]:
    """Discover plugins from entry points + legacy targets.

    ``enabled`` is an allowlist of plugin IDs. Empty/None means all
    discovered plugins are loaded. Duplicate IDs are dropped — entry
    points win over legacy targets.
    """
    seen: set[str] = set()
    manifests: list[PluginManifest] = []

    for manifest in _discover_entry_points():
        if manifest.id in seen:
            continue
        seen.add(manifest.id)
        manifests.append(manifest)

    for manifest in _discover_legacy_targets():
        if manifest.id in seen:
            continue
        seen.add(manifest.id)
        manifests.append(manifest)

    if enabled:
        enabled_set = set(enabled)
        manifests = [m for m in manifests if m.id in enabled_set]
    return manifests


def validate_dependencies(manifests: Iterable[PluginManifest]) -> None:
    """Order-check declared dependencies and raise on missing refs.

    The manifest list is expected to already be in load order; we just
    verify each ``dependencies`` entry points at a manifest appearing
    earlier in the list.
    """
    seen: set[str] = set()
    for manifest in manifests:
        for dep in manifest.dependencies:
            if dep not in seen:
                raise PluginLoadError(
                    manifest.id,
                    f"depends on {dep!r} which is not loaded (or loads after this plugin)",
                )
        seen.add(manifest.id)
