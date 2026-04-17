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

    **Must not be called from inside a running event loop** — asyncio.run
    will crash. Worker ``__main__`` is sync at this point, so it is safe.
    Prefer :func:`bootstrap_worker_plugins` which bundles the standard
    worker startup sequence.
    """
    import asyncio

    _ = targets  # accepted for back-compat; actual list lives in discovery
    asyncio.run(
        plugin_manager.initialize(
            enabled_plugins=enabled_plugins,
            license_keys=license_keys,
        )
    )


def bootstrap_worker_plugins() -> None:
    """Full plugin startup sequence for Hatchet workers.

    Equivalent to:
        register_core_plugins(plugin_manager)
        load_default_plugins(enabled_plugins=..., license_keys=...)
        bridge_plugin_search_providers()

    Every worker's ``__main__`` must call this *before* building its
    workflow list so plugin-contributed Hatchet workflows are enrolled.
    Keeping this in one helper avoids the "forgot to update one of seven
    workers" class of bug.
    """
    # Lazy imports so kt-plugins keeps its foundation-level footprint —
    # kt_db + kt_providers are heavier and only needed inside workers.
    from kt_config.settings import get_settings
    from kt_db.core_plugin import register_core_plugins
    from kt_providers.registry import bridge_plugin_search_providers

    settings = get_settings()
    register_core_plugins(plugin_manager)
    load_default_plugins(
        enabled_plugins=settings.enabled_plugins or None,
        license_keys=settings.plugin_license_keys or None,
    )
    bridge_plugin_search_providers()


__all__ = [
    "ENTRY_POINT_GROUP",
    "BackendEnginePlugin",
    "BackendPlugin",
    "DbTarget",
    "bootstrap_worker_plugins",
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
