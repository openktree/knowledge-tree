"""Pytest helpers for plugin-registry lifecycle in tests.

Import into a ``conftest.py`` to share across a package::

    from kt_config.testing import plugin_registry_clean, registered_plugins

``plugin_registry_clean`` auto-clears the singleton before every test.
``registered_plugins`` is a factory fixture — pass in one or more
``BackendEnginePlugin`` instances to register for the duration of a test.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Callable

import pytest

from kt_config.plugin import BackendEnginePlugin, PluginRegistry, plugin_registry


@pytest.fixture
def plugin_registry_clean() -> Iterator[PluginRegistry]:
    """Yield the module-level ``plugin_registry`` cleared of all plugins.

    The registry is cleared before and after the test so stray registrations
    from other test modules don't bleed across.
    """
    plugin_registry.clear()
    try:
        yield plugin_registry
    finally:
        plugin_registry.clear()


@pytest.fixture
def registered_plugins(
    plugin_registry_clean: PluginRegistry,
) -> Callable[..., PluginRegistry]:
    """Factory that registers any number of plugins and returns the registry.

    Usage::

        def test_foo(registered_plugins):
            registered_plugins(MyPlugin(), OtherPlugin())
            ...
    """

    def _register(*plugins: BackendEnginePlugin) -> PluginRegistry:
        for plugin in plugins:
            plugin_registry_clean.register_backend_engine(plugin)
        return plugin_registry_clean

    return _register
