"""Knowledge Tree plugin framework.

Entry-point discovered, lifecycle-managed, hook-enabled plugin system.
See ``PLUGINS.md`` in the repo root for the user-facing contract, and
``CLAUDE.md`` (libs table) for the architectural role.

Exports the legacy ``BackendEnginePlugin`` ABC and contribution types
so the existing in-tree plugins and ``kt_config.plugin`` shim continue
to import from here via re-export.
"""

from kt_plugins.context import PluginContext
from kt_plugins.discovery import ENTRY_POINT_GROUP, discover_plugins, validate_dependencies
from kt_plugins.errors import PluginError, PluginLicenseError, PluginLoadError
from kt_plugins.extension_points import (
    DbTarget,
    EntityExtractorContribution,
    ExtensionRegistry,
    HookSubscription,
    PluginDatabase,
    PluginType,
    PostExtractionHandler,
    PostExtractionHook,
    RouteContribution,
    SearchProviderContribution,
    WorkflowContribution,
)
from kt_plugins.hooks import HookHandler, HookRegistry
from kt_plugins.legacy import BackendEnginePlugin, BackendPlugin, FrontendPlugin, legacy_to_manifest
from kt_plugins.license import generate_license_key, validate_license_key
from kt_plugins.manager import PluginManager, plugin_manager
from kt_plugins.manifest import PluginLifecycle, PluginManifest

# Back-compat alias — the old kt_config.plugin module exposed this name.
plugin_registry = plugin_manager


def load_default_plugins(
    *,
    targets: list[tuple[str, str]] | None = None,
    enabled_plugins: list[str] | None = None,
    license_keys: dict[str, str] | None = None,
) -> None:
    """Synchronous bootstrap — entry-point discovery + legacy targets.

    Calls ``plugin_manager.initialize()`` via ``asyncio.run``. Workers
    invoke this from their sync ``__main__`` before building their
    workflow list so plugin-contributed workflows are enrolled with the
    Hatchet worker. FastAPI callers should prefer
    ``await plugin_manager.initialize(...)`` directly from lifespan.

    ``targets`` is accepted for back-compat but ignored — the legacy
    target list is hardwired inside discovery.
    """
    import asyncio

    _ = targets  # accepted for back-compat; actual list lives in discovery
    asyncio.run(
        plugin_manager.initialize(
            enabled_plugins=enabled_plugins,
            license_keys=license_keys,
        )
    )


__all__ = [
    "ENTRY_POINT_GROUP",
    "BackendEnginePlugin",
    "BackendPlugin",
    "DbTarget",
    "discover_plugins",
    "validate_dependencies",
    "EntityExtractorContribution",
    "ExtensionRegistry",
    "FrontendPlugin",
    "HookHandler",
    "HookRegistry",
    "HookSubscription",
    "PluginContext",
    "PluginDatabase",
    "PluginError",
    "PluginLicenseError",
    "PluginLifecycle",
    "PluginLoadError",
    "PluginManager",
    "PluginManifest",
    "PluginType",
    "PostExtractionHandler",
    "PostExtractionHook",
    "RouteContribution",
    "SearchProviderContribution",
    "WorkflowContribution",
    "generate_license_key",
    "legacy_to_manifest",
    "load_default_plugins",
    "plugin_manager",
    "plugin_registry",
    "validate_license_key",
]
