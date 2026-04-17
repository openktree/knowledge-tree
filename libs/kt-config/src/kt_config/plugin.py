"""Deprecated — shim re-exporting the plugin framework from :mod:`kt_plugins`.

The full plugin framework (manifest, lifecycle, hooks, entry-point
discovery, license validation) now lives in ``kt-plugins``. This module
remains so existing imports from ``kt_config.plugin`` keep working:

  - ``BackendEnginePlugin``, ``BackendPlugin``, ``FrontendPlugin`` ABCs
  - ``PluginDatabase``, ``EntityExtractorContribution``,
    ``SearchProviderContribution``, ``PostExtractionHook`` contributions
  - ``PluginRegistry`` type + ``plugin_registry`` singleton
  - ``load_default_plugins`` helper

New code should import from ``kt_plugins`` directly.
"""

from __future__ import annotations

from kt_plugins import (
    BackendEnginePlugin,
    BackendPlugin,
    DbTarget,
    EntityExtractorContribution,
    FrontendPlugin,
    PluginDatabase,
    PluginType,
    PostExtractionHandler,
    PostExtractionHook,
    SearchProviderContribution,
    load_default_plugins,
    plugin_manager,
    plugin_registry,
)
from kt_plugins.manager import PluginManager as PluginRegistry

__all__ = [
    "BackendEnginePlugin",
    "BackendPlugin",
    "DbTarget",
    "EntityExtractorContribution",
    "FrontendPlugin",
    "PluginDatabase",
    "PluginRegistry",
    "PluginType",
    "PostExtractionHandler",
    "PostExtractionHook",
    "SearchProviderContribution",
    "load_default_plugins",
    "plugin_manager",
    "plugin_registry",
]
