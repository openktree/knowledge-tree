"""Plugin discovery via Python entry points."""

from __future__ import annotations

import importlib.metadata
import logging

from kt_plugins.errors import PluginLoadError
from kt_plugins.manifest import PluginManifest

logger = logging.getLogger(__name__)

ENTRY_POINT_GROUP = "kt.plugins"


def discover_plugins(
    enabled: list[str] | None = None,
) -> list[PluginManifest]:
    """Discover installed plugins via ``importlib.metadata`` entry points.

    Scans the ``kt.plugins`` entry point group for ``PluginManifest``
    instances. If ``enabled`` is a non-empty list, only plugins whose
    ``id`` appears in the list are loaded. An empty list means load all.

    Args:
        enabled: Optional allowlist of plugin IDs. Empty or None = all.

    Returns:
        List of discovered ``PluginManifest`` instances.
    """
    manifests: list[PluginManifest] = []
    eps = importlib.metadata.entry_points(group=ENTRY_POINT_GROUP)

    for ep in eps:
        try:
            obj = ep.load()
        except Exception:
            logger.warning("Failed to load plugin entry point: %s", ep.name, exc_info=True)
            continue

        if not isinstance(obj, PluginManifest):
            logger.warning(
                "Entry point %s did not resolve to a PluginManifest (got %s)",
                ep.name,
                type(obj).__name__,
            )
            continue

        # Filter by enabled list
        if enabled and obj.id not in enabled:
            logger.info("Plugin %r skipped (not in enabled_plugins list)", obj.id)
            continue

        manifests.append(obj)
        logger.info("Discovered plugin: %s v%s", obj.id, obj.version)

    return manifests


def validate_dependencies(manifests: list[PluginManifest]) -> None:
    """Check that all plugin dependencies are satisfied.

    Raises ``PluginLoadError`` if a plugin declares a dependency
    on another plugin that is not in the manifest list.
    """
    ids = {m.id for m in manifests}
    for m in manifests:
        for dep in m.dependencies:
            if dep not in ids:
                raise PluginLoadError(
                    m.id,
                    f"Missing dependency: plugin {m.id!r} requires {dep!r}",
                )
