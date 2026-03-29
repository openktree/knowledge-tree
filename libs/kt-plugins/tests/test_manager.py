"""Tests for PluginManager lifecycle — initialize, bootstrap, shutdown."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from kt_plugins.errors import PluginLoadError
from kt_plugins.extension_points import ExtensionRegistry
from kt_plugins.hooks import HookRegistry
from kt_plugins.license import generate_license_key
from kt_plugins.manager import PluginManager
from kt_plugins.manifest import PluginManifest


class FakeLifecycle:
    """Test lifecycle that tracks calls."""

    def __init__(self) -> None:
        self.registered = False
        self.bootstrapped = False
        self.shut_down = False
        self.ctx = None

    async def register(self, registry: ExtensionRegistry) -> None:
        self.registered = True

    async def bootstrap(self, ctx: object) -> None:
        self.bootstrapped = True
        self.ctx = ctx

    async def shutdown(self) -> None:
        self.shut_down = True


def _make_manifest(plugin_id: str = "test", lifecycle: object | None = None, **kwargs: object) -> PluginManifest:
    return PluginManifest(
        id=plugin_id,
        name=plugin_id.title(),
        version="1.0.0",
        lifecycle=lifecycle,
        **kwargs,
    )


async def test_initialize_no_plugins() -> None:
    manager = PluginManager()
    with patch("kt_plugins.manager.discover_plugins", return_value=[]):
        await manager.initialize()
    assert manager.manifests == []


async def test_full_lifecycle() -> None:
    lc = FakeLifecycle()
    manifest = _make_manifest(lifecycle=lc)

    manager = PluginManager()
    with patch("kt_plugins.manager.discover_plugins", return_value=[manifest]):
        await manager.initialize()

    assert lc.registered
    assert not lc.bootstrapped

    await manager.bootstrap()

    assert lc.bootstrapped
    assert lc.ctx is not None
    assert lc.ctx.plugin_id == "test"

    await manager.shutdown()
    assert lc.shut_down


async def test_shutdown_reverse_order() -> None:
    order: list[str] = []

    class TrackShutdown:
        def __init__(self, name: str) -> None:
            self._name = name

        async def register(self, registry: ExtensionRegistry) -> None:
            pass

        async def bootstrap(self, ctx: object) -> None:
            pass

        async def shutdown(self) -> None:
            order.append(self._name)

    m1 = _make_manifest("first", lifecycle=TrackShutdown("first"))
    m2 = _make_manifest("second", lifecycle=TrackShutdown("second"))

    manager = PluginManager()
    with patch("kt_plugins.manager.discover_plugins", return_value=[m1, m2]):
        await manager.initialize()
    await manager.bootstrap()
    await manager.shutdown()

    assert order == ["second", "first"]


async def test_license_validation_skips_invalid() -> None:
    manifest = _make_manifest(requires_license_key=True)

    manager = PluginManager()
    with patch("kt_plugins.manager.discover_plugins", return_value=[manifest]):
        await manager.initialize(license_keys={"test": "bad.key"})

    # Plugin should be skipped due to invalid license
    assert len(manager.manifests) == 0


async def test_license_validation_passes_with_valid_key() -> None:
    lc = FakeLifecycle()
    manifest = _make_manifest(lifecycle=lc, requires_license_key=True)
    valid_key = generate_license_key("test-org")

    manager = PluginManager()
    with patch("kt_plugins.manager.discover_plugins", return_value=[manifest]):
        await manager.initialize(license_keys={"test": valid_key})

    assert len(manager.manifests) == 1
    assert lc.registered


async def test_double_initialize_skips() -> None:
    manager = PluginManager()
    with patch("kt_plugins.manager.discover_plugins", return_value=[]) as mock_discover:
        await manager.initialize()
        await manager.initialize()

    assert mock_discover.call_count == 1


async def test_bootstrap_before_initialize_raises() -> None:
    manager = PluginManager()
    with pytest.raises(RuntimeError, match="initialize"):
        await manager.bootstrap()


async def test_hook_registry_accessible() -> None:
    manager = PluginManager()
    assert isinstance(manager.hook_registry, HookRegistry)


async def test_get_plugin_routes_empty() -> None:
    manager = PluginManager()
    with patch("kt_plugins.manager.discover_plugins", return_value=[]):
        await manager.initialize()
    assert manager.get_plugin_routes() == []


async def test_register_failure_raises() -> None:
    class BadLifecycle:
        async def register(self, registry: ExtensionRegistry) -> None:
            raise ValueError("register boom")

        async def bootstrap(self, ctx: object) -> None:
            pass

        async def shutdown(self) -> None:
            pass

    manifest = _make_manifest(lifecycle=BadLifecycle())

    manager = PluginManager()
    with patch("kt_plugins.manager.discover_plugins", return_value=[manifest]):
        with pytest.raises(PluginLoadError, match="register"):
            await manager.initialize()
