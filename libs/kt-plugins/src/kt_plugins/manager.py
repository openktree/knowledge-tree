"""Plugin manager — orchestrates discovery, lifecycle, and wiring.

Replaces the ``PluginRegistry`` singleton from ``kt_config.plugin`` while
preserving its API (via the module-level ``plugin_manager`` instance
re-exported as ``plugin_registry`` from :mod:`kt_plugins`).
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Iterable
from typing import Any

from kt_plugins.context import PluginContext
from kt_plugins.discovery import discover_plugins, validate_dependencies
from kt_plugins.errors import PluginLicenseError, PluginLoadError
from kt_plugins.extension_points import (
    EntityExtractorContribution,
    ExtensionRegistry,
    PluginDatabase,
    PostExtractionHook,
    RouteContribution,
    SearchProviderContribution,
    WorkflowContribution,
)
from kt_plugins.hooks import HookRegistry
from kt_plugins.legacy import BackendEnginePlugin, legacy_to_manifest
from kt_plugins.license import validate_license_key
from kt_plugins.manifest import PluginManifest

logger = logging.getLogger(__name__)


CtxFactory = Callable[[PluginManifest], Awaitable[PluginContext] | PluginContext]


class PluginManager:
    """Orchestrates the full plugin lifecycle.

    Supports three usage modes:

    1. **Legacy back-compat** — ``register_backend_engine(plugin)``
       directly enrolls a ``BackendEnginePlugin`` instance (as the old
       ``PluginRegistry`` did). Used by :mod:`kt_db.core_plugin`, by
       tests, and by module-scope ``load_default_plugins()`` calls that
       haven't migrated to ``initialize()`` yet.
    2. **Discovery + register phase** — ``await initialize(...)`` does
       entry-point discovery, license checks, and calls
       ``lifecycle.register(ExtensionRegistry)`` on every manifest.
    3. **Bootstrap phase** — ``await bootstrap(ctx_factory)`` builds a
       per-plugin ``PluginContext`` and invokes
       ``lifecycle.bootstrap(ctx)``.

    Extension contributions (routes, workflows, providers, extractors,
    post-extraction hooks, databases) live on
    ``self.extension_registry`` and are populated during register().
    """

    def __init__(self) -> None:
        self._manifests: list[PluginManifest] = []
        self._extension_registry = ExtensionRegistry()
        self._hook_registry = HookRegistry()
        self._plugin_contexts: dict[str, PluginContext] = {}
        self._initialized = False
        self._bootstrapped = False

    # -- Introspection ---------------------------------------------------------

    @property
    def hook_registry(self) -> HookRegistry:
        return self._hook_registry

    @property
    def extension_registry(self) -> ExtensionRegistry:
        return self._extension_registry

    @property
    def manifests(self) -> list[PluginManifest]:
        return list(self._manifests)

    # -- Legacy-compatible API (mirrors old PluginRegistry) --------------------

    def register_backend_engine(self, plugin: BackendEnginePlugin) -> None:
        """Enrol a legacy ``BackendEnginePlugin`` instance.

        Idempotent by ``plugin_id``. Immediately runs the legacy
        register phase (pumps contributions into the extension registry)
        — no awaiting needed because the legacy adapter's register is
        synchronous in practice.
        """
        if any(m.id == plugin.plugin_id for m in self._manifests):
            logger.debug("plugin already registered, skipping: %s", plugin.plugin_id)
            return
        manifest = legacy_to_manifest(plugin)
        self._manifests.append(manifest)
        # Run register synchronously by scheduling it on a new loop if
        # needed. In practice callers invoke this from sync startup code
        # (kt_db.core_plugin, tests) so just populate the registry
        # directly via the plugin's methods.
        self._populate_legacy(plugin)
        logger.debug("registered legacy backend-engine plugin: %s", plugin.plugin_id)

    def _populate_legacy(self, plugin: BackendEnginePlugin) -> None:
        db = plugin.get_database()
        if db is not None:
            self._extension_registry.add_database(db)
        for contrib in plugin.get_entity_extractors():
            self._extension_registry.add_entity_extractor(contrib)
        for contrib in plugin.get_search_providers():
            self._extension_registry.add_provider(contrib)
        for hook in plugin.get_post_extraction_hooks():
            self._extension_registry.add_post_extraction_hook(hook)
        for route in plugin.get_routes():
            self._extension_registry.add_route(route)
        for wf in plugin.get_workflows():
            self._extension_registry.workflows.append(WorkflowContribution(workflow=wf))

    def clear(self) -> None:
        """Reset state. Intended for test isolation."""
        self._manifests.clear()
        self._extension_registry = ExtensionRegistry()
        self._hook_registry.clear()
        self._plugin_contexts.clear()
        self._initialized = False
        self._bootstrapped = False

    # -- Legacy iteration helpers (used by kt_facts, kt_providers, kt_db) ------

    def get_entity_extractor(self, name: str, gateway: Any) -> Any | None:
        """Look up an entity extractor by name and instantiate it."""
        factory = self._extension_registry.get_entity_extractor_factory(name)
        if factory is None:
            return None
        return factory(gateway)

    def iter_search_providers(self) -> Iterable[SearchProviderContribution]:
        yield from self._extension_registry.providers

    def iter_post_extraction_hooks(self, extractor_name: str) -> Iterable[PostExtractionHook]:
        yield from self._extension_registry.iter_post_extraction_hooks(extractor_name)

    def iter_plugin_databases(self) -> Iterable[PluginDatabase]:
        yield from self._extension_registry.databases

    async def run_database_migrations(
        self,
        write_db_urls: Iterable[str] | None = None,
        *,
        graph_db_urls: Iterable[str] | None = None,
        strict: bool = False,
        schema: str | None = None,
    ) -> None:
        """Run ``ensure_migrated`` for every registered plugin database.

        Matches the old ``PluginRegistry.run_database_migrations`` API so
        ``kt_db.startup.run_startup_migrations`` keeps working unchanged.
        """
        write_urls = list(dict.fromkeys(write_db_urls or ()))
        graph_urls = list(dict.fromkeys(graph_db_urls or ()))
        for db in self._extension_registry.databases:
            urls = graph_urls if db.target == "graph" else write_urls
            for url in urls:
                try:
                    await db.ensure_migrated(url, schema=schema)
                except Exception:
                    if strict:
                        raise
                    logger.exception(
                        "plugin DB migration failed for %s (schema=%s, target=%s, url=%s) — skipping",
                        db.plugin_id,
                        db.schema_name,
                        db.target,
                        url,
                    )

    # -- New lifecycle API -----------------------------------------------------

    async def initialize(
        self,
        *,
        enabled_plugins: list[str] | None = None,
        license_keys: dict[str, str] | None = None,
    ) -> None:
        """Discover entry-point plugins, validate licenses, run register.

        Safe to call alongside ``register_backend_engine`` — the latter
        can be called either before or after ``initialize`` to enrol
        plugins that don't use entry points (e.g. core DB migrations).
        """
        if self._initialized:
            logger.debug("PluginManager.initialize() called twice — skipping re-discovery")
            return

        license_keys = license_keys or {}
        discovered = discover_plugins(enabled=enabled_plugins or None)
        validate_dependencies(discovered)

        # License gating — drop plugins whose commercial key is missing/invalid.
        gated: list[PluginManifest] = []
        for manifest in discovered:
            if manifest.requires_license_key:
                key = license_keys.get(manifest.id, "")
                try:
                    validate_license_key(manifest.id, key)
                except PluginLicenseError:
                    logger.error(
                        "license validation failed for plugin %r — skipping",
                        manifest.id,
                    )
                    continue
            gated.append(manifest)

        # Drop duplicates already registered via register_backend_engine.
        existing_ids = {m.id for m in self._manifests}
        to_register = [m for m in gated if m.id not in existing_ids]

        for manifest in to_register:
            if manifest.lifecycle is not None:
                try:
                    await manifest.lifecycle.register(self._extension_registry)
                except Exception as exc:
                    logger.exception("plugin %r failed during register()", manifest.id)
                    raise PluginLoadError(manifest.id, "register() failed") from exc
            self._manifests.append(manifest)
            logger.info("plugin %r registered", manifest.id)

        self._initialized = True
        logger.info(
            "plugin initialization complete: %d plugin(s) loaded",
            len(self._manifests),
        )

    async def bootstrap(self, ctx_factory: CtxFactory | None = None) -> None:
        """Run the bootstrap phase — hand runtime services to plugins.

        ``ctx_factory`` is a callable taking a ``PluginManifest`` and
        returning (or awaiting) a ``PluginContext``. If omitted, a
        minimal context with just the hook registry is built — plugins
        that need session factories / model gateways won't work without
        a proper factory.
        """
        if self._bootstrapped:
            logger.debug("PluginManager.bootstrap() called twice — skipping")
            return

        for manifest in self._manifests:
            if ctx_factory is not None:
                ctx = ctx_factory(manifest)
                if hasattr(ctx, "__await__"):
                    ctx = await ctx  # type: ignore[assignment,misc]
            else:
                ctx = PluginContext(
                    plugin_id=manifest.id,
                    settings=None,
                    hook_registry=self._hook_registry,
                )
            self._plugin_contexts[manifest.id] = ctx  # type: ignore[assignment]

            if manifest.lifecycle is not None:
                try:
                    await manifest.lifecycle.bootstrap(ctx)  # type: ignore[arg-type]
                    logger.info("plugin %r bootstrapped", manifest.id)
                except Exception as exc:
                    logger.exception("plugin %r failed during bootstrap()", manifest.id)
                    raise PluginLoadError(manifest.id, "bootstrap() failed") from exc

        self._bootstrapped = True

    async def shutdown(self) -> None:
        """Invoke each plugin's ``shutdown()`` in reverse registration order."""
        for manifest in reversed(self._manifests):
            if manifest.lifecycle is not None:
                try:
                    await manifest.lifecycle.shutdown()
                    logger.info("plugin %r shut down", manifest.id)
                except Exception:
                    logger.exception("plugin %r failed during shutdown()", manifest.id)

    # -- Accessors for wiring -------------------------------------------------

    def get_plugin_routes(self) -> list[RouteContribution]:
        return list(self._extension_registry.routes)

    def get_plugin_workflows(self) -> list[Any]:
        return [w.workflow for w in self._extension_registry.workflows]

    def get_plugin_providers(self) -> list[SearchProviderContribution]:
        return list(self._extension_registry.providers)

    def get_plugin_entity_extractors(self) -> list[EntityExtractorContribution]:
        return list(self._extension_registry.entity_extractors)

    def get_plugin_post_extraction_hooks(self) -> list[PostExtractionHook]:
        return list(self._extension_registry.post_extraction_hooks)


# Module-level singleton — drop-in replacement for the old
# ``kt_config.plugin.plugin_registry``.
plugin_manager = PluginManager()
