"""Verify the pytest fixtures in ``kt_config.testing``."""

from __future__ import annotations

from kt_config.plugin import BackendEnginePlugin, plugin_registry
from kt_config.testing import plugin_registry_clean, registered_plugins  # noqa: F401


class _Dummy(BackendEnginePlugin):
    plugin_id = "backend-engine-dummy"


class _Other(BackendEnginePlugin):
    plugin_id = "backend-engine-other"


def test_plugin_registry_clean_resets(plugin_registry_clean):  # noqa: F811 — pytest fixture param shadows import
    assert plugin_registry_clean is plugin_registry
    plugin_registry.register_backend_engine(_Dummy())
    assert len(plugin_registry._backend_engine) == 1


def test_registered_plugins_factory(registered_plugins):  # noqa: F811 — pytest fixture param shadows import
    reg = registered_plugins(_Dummy(), _Other())
    ids = [p.plugin_id for p in reg._backend_engine]
    assert ids == ["backend-engine-dummy", "backend-engine-other"]


def test_registry_cleared_between_tests():
    # Previous test registered plugins; this test uses no fixture and
    # relies on the prior test's cleanup. If the fixture's finalizer runs
    # correctly, the registry is empty again here.
    assert plugin_registry._backend_engine == []
