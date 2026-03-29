"""Plugin manager — orchestrates discovery, lifecycle, and wiring."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from kt_plugins.context import PluginContext
from kt_plugins.discovery import discover_plugins, validate_dependencies
from kt_plugins.errors import PluginLicenseError, PluginLoadError
from kt_plugins.extension_points import ExtensionRegistry, RouteRegistration
from kt_plugins.hooks import HookRegistry
from kt_plugins.license import validate_license_key
from kt_plugins.manifest import PluginManifest

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

logger = logging.getLogger(__name__)


class PluginManager:
    """Orchestrates the full plugin lifecycle.

    Usage::

        manager = PluginManager()
        await manager.initialize(enabled_plugins=["billing"], license_keys={"billing": "key"})
        await manager.bootstrap(session_factory=sf, write_session_factory=wsf)
        # ... application runs ...
        await manager.shutdown()
    """

    def __init__(self) -> None:
        self._manifests: list[PluginManifest] = []
        self._extension_registry = ExtensionRegistry()
        self._hook_registry = HookRegistry()
        self._plugin_contexts: dict[str, PluginContext] = {}
        self._initialized = False
        self._bootstrapped = False

    @property
    def hook_registry(self) -> HookRegistry:
        return self._hook_registry

    @property
    def extension_registry(self) -> ExtensionRegistry:
        return self._extension_registry

    @property
    def manifests(self) -> list[PluginManifest]:
        return list(self._manifests)

    async def initialize(
        self,
        *,
        enabled_plugins: list[str] | None = None,
        license_keys: dict[str, str] | None = None,
    ) -> None:
        """Discover plugins, validate licenses, and run registration phase.

        Args:
            enabled_plugins: Optional allowlist (empty/None = all discovered).
            license_keys: Map of plugin_id -> license key for commercial plugins.
        """
        if self._initialized:
            logger.warning("PluginManager.initialize() called twice — skipping")
            return

        license_keys = license_keys or {}

        # Phase 1: Discover
        self._manifests = discover_plugins(enabled=enabled_plugins or None)
        if not self._manifests:
            logger.info("No plugins discovered")
            self._initialized = True
            return

        # Validate inter-plugin dependencies
        validate_dependencies(self._manifests)

        # Phase 2: Validate licenses
        for manifest in self._manifests:
            if manifest.requires_license_key:
                key = license_keys.get(manifest.id, "")
                try:
                    validate_license_key(manifest.id, key)
                except PluginLicenseError:
                    logger.error("License validation failed for plugin %r — skipping", manifest.id)
                    self._manifests = [m for m in self._manifests if m.id != manifest.id]
                    continue

        # Phase 3: Register
        for manifest in self._manifests:
            if manifest.lifecycle is not None:
                try:
                    await manifest.lifecycle.register(self._extension_registry)
                    logger.info("Plugin %r registered", manifest.id)
                except Exception:
                    logger.exception("Plugin %r failed during register()", manifest.id)
                    raise PluginLoadError(manifest.id, "register() failed") from None

        self._initialized = True
        logger.info(
            "Plugin initialization complete: %d plugin(s) loaded",
            len(self._manifests),
        )

    async def bootstrap(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession] | None = None,
        write_session_factory: async_sessionmaker[AsyncSession] | None = None,
    ) -> None:
        """Run the bootstrap phase — provide runtime services to plugins.

        Args:
            session_factory: Graph-db async session factory.
            write_session_factory: Write-db async session factory.
        """
        if not self._initialized:
            raise RuntimeError("Must call initialize() before bootstrap()")
        if self._bootstrapped:
            logger.warning("PluginManager.bootstrap() called twice — skipping")
            return

        for manifest in self._manifests:
            settings: Any = None
            if manifest.settings_class is not None:
                try:
                    settings = manifest.settings_class()
                except Exception:
                    logger.warning("Failed to load settings for plugin %r", manifest.id, exc_info=True)

            ctx = PluginContext(
                plugin_id=manifest.id,
                settings=settings,
                hook_registry=self._hook_registry,
                session_factory=session_factory,
                write_session_factory=write_session_factory,
            )
            self._plugin_contexts[manifest.id] = ctx

            if manifest.lifecycle is not None:
                try:
                    await manifest.lifecycle.bootstrap(ctx)
                    logger.info("Plugin %r bootstrapped", manifest.id)
                except Exception:
                    logger.exception("Plugin %r failed during bootstrap()", manifest.id)
                    raise PluginLoadError(manifest.id, "bootstrap() failed") from None

        self._bootstrapped = True

    async def shutdown(self) -> None:
        """Shutdown all plugins in reverse registration order."""
        for manifest in reversed(self._manifests):
            if manifest.lifecycle is not None:
                try:
                    await manifest.lifecycle.shutdown()
                    logger.info("Plugin %r shut down", manifest.id)
                except Exception:
                    logger.exception("Plugin %r failed during shutdown()", manifest.id)

    # -- Accessors for wiring into the application -----------------------------

    def get_plugin_routes(self) -> list[RouteRegistration]:
        """Return all route registrations from plugins."""
        return self._extension_registry.get_routes()

    def get_plugin_workflows(self) -> list[Any]:
        """Return all workflow objects from plugins."""
        return [w.workflow for w in self._extension_registry.get_workflows()]
