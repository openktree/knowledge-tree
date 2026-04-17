"""PluginManager lifecycle: initialize, bootstrap, shutdown, legacy adapter."""

from __future__ import annotations

from kt_plugins import (
    BackendEnginePlugin,
    ExtensionRegistry,
    PluginContext,
    PluginManager,
    PluginManifest,
)

# ── Legacy plugin used by several tests ───────────────────────────────────


class _ExamplePlugin(BackendEnginePlugin):
    plugin_id = "example-legacy"


async def test_register_backend_engine_is_idempotent() -> None:
    mgr = PluginManager()
    mgr.register_backend_engine(_ExamplePlugin())
    mgr.register_backend_engine(_ExamplePlugin())
    ids = [m.id for m in mgr.manifests]
    assert ids == ["example-legacy"]


async def test_initialize_runs_lifecycle_register() -> None:
    seen: list[str] = []

    class _Life:
        async def register(self, registry: ExtensionRegistry) -> None:
            seen.append("register")

        async def bootstrap(self, ctx: PluginContext) -> None:
            seen.append("bootstrap")

        async def shutdown(self) -> None:
            seen.append("shutdown")

    manifest = PluginManifest(id="inline", lifecycle=_Life())
    mgr = PluginManager()
    # Bypass entry-point discovery by stuffing the manifest directly
    # after discovery but before the register phase — use initialize()
    # with monkeypatched discovery instead.
    import kt_plugins.manager as manager_mod

    original = manager_mod.discover_plugins
    manager_mod.discover_plugins = lambda enabled=None: [manifest]  # type: ignore[assignment]
    try:
        await mgr.initialize()
    finally:
        manager_mod.discover_plugins = original

    assert seen == ["register"]
    await mgr.bootstrap()
    assert seen == ["register", "bootstrap"]
    await mgr.shutdown()
    assert seen == ["register", "bootstrap", "shutdown"]


async def test_initialize_skips_plugins_missing_license() -> None:
    seen: list[str] = []

    class _Life:
        async def register(self, registry: ExtensionRegistry) -> None:
            seen.append("registered")

        async def bootstrap(self, ctx: PluginContext) -> None: ...

        async def shutdown(self) -> None: ...

    manifest = PluginManifest(id="billing", requires_license_key=True, lifecycle=_Life())
    mgr = PluginManager()
    import kt_plugins.manager as manager_mod

    original = manager_mod.discover_plugins
    manager_mod.discover_plugins = lambda enabled=None: [manifest]  # type: ignore[assignment]
    try:
        # No allowlist — missing key drops the plugin silently (log-only).
        await mgr.initialize(license_keys={})
    finally:
        manager_mod.discover_plugins = original

    assert seen == []  # license gate prevented register()
    assert [m.id for m in mgr.manifests] == []


async def test_initialize_fails_hard_when_allowlisted_plugin_lacks_license() -> None:
    """Operator put the plugin on ``enabled_plugins`` but didn't configure
    its license key — that's a misconfiguration, not an "oh well"."""
    import pytest

    from kt_plugins import PluginLicenseError

    class _Life:
        async def register(self, registry: ExtensionRegistry) -> None: ...
        async def bootstrap(self, ctx: PluginContext) -> None: ...
        async def shutdown(self) -> None: ...

    manifest = PluginManifest(id="billing", requires_license_key=True, lifecycle=_Life())
    mgr = PluginManager()
    import kt_plugins.manager as manager_mod

    original = manager_mod.discover_plugins
    # Simulate discovery returning the plugin even when allowlist filter runs.
    manager_mod.discover_plugins = lambda enabled=None: [manifest]  # type: ignore[assignment]
    try:
        with pytest.raises(PluginLicenseError):
            await mgr.initialize(enabled_plugins=["billing"], license_keys={})
    finally:
        manager_mod.discover_plugins = original


async def test_initialize_respects_enabled_allowlist() -> None:
    class _Life:
        async def register(self, registry: ExtensionRegistry) -> None: ...
        async def bootstrap(self, ctx: PluginContext) -> None: ...
        async def shutdown(self) -> None: ...

    a = PluginManifest(id="a", lifecycle=_Life())
    b = PluginManifest(id="b", lifecycle=_Life())
    mgr = PluginManager()
    import kt_plugins.manager as manager_mod

    original = manager_mod.discover_plugins

    def _filtered(enabled=None):  # matches the real discover_plugins contract
        all_manifests = [a, b]
        if enabled:
            return [m for m in all_manifests if m.id in set(enabled)]
        return all_manifests

    manager_mod.discover_plugins = _filtered  # type: ignore[assignment]
    try:
        await mgr.initialize(enabled_plugins=["a"])
    finally:
        manager_mod.discover_plugins = original
    assert [m.id for m in mgr.manifests] == ["a"]


async def test_legacy_adapter_populates_extension_registry() -> None:
    from pathlib import Path

    from kt_plugins import (
        EntityExtractorContribution,
        PluginDatabase,
        PostExtractionHook,
        SearchProviderContribution,
    )

    class _Big(BackendEnginePlugin):
        plugin_id = "legacy-big"

        def get_database(self) -> PluginDatabase:
            return PluginDatabase(
                plugin_id=self.plugin_id,
                schema_name="plugin_legacy_big",
                alembic_config_path=Path("/nonexistent/alembic.ini"),
                target="write",
            )

        def get_entity_extractors(self):
            return [EntityExtractorContribution(extractor_name="spacy", factory=lambda g: g)]

        def get_search_providers(self):
            return [
                SearchProviderContribution(
                    provider_id="serper",
                    factory=lambda: object(),
                )
            ]

        def get_post_extraction_hooks(self):
            async def _h(session, items, scope): ...

            return [PostExtractionHook(extractor_name="spacy", output_key="shells", handler=_h)]

    mgr = PluginManager()
    mgr.register_backend_engine(_Big())

    assert len(list(mgr.iter_plugin_databases())) == 1
    assert len(mgr.get_plugin_providers()) == 1
    assert len(mgr.get_plugin_entity_extractors()) == 1
    assert len(mgr.get_plugin_post_extraction_hooks()) == 1


async def test_bootstrap_without_initialize_is_allowed() -> None:
    """Legacy-only code paths (kt-db core_plugin) skip initialize()."""
    mgr = PluginManager()
    mgr.register_backend_engine(_ExamplePlugin())
    # Should not raise — bootstrap iterates existing manifests with no-op
    # lifecycle if the legacy adapter didn't subscribe to anything.
    await mgr.bootstrap()


async def test_shutdown_reverse_order() -> None:
    order: list[str] = []

    class _Life:
        def __init__(self, name: str) -> None:
            self.name = name

        async def register(self, registry: ExtensionRegistry) -> None: ...
        async def bootstrap(self, ctx: PluginContext) -> None: ...
        async def shutdown(self) -> None:
            order.append(self.name)

    mgr = PluginManager()
    # Directly add manifests in a known order.
    mgr._manifests.append(PluginManifest(id="first", lifecycle=_Life("first")))
    mgr._manifests.append(PluginManifest(id="second", lifecycle=_Life("second")))
    await mgr.shutdown()
    assert order == ["second", "first"]
