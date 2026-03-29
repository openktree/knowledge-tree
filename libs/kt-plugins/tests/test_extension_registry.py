"""Tests for ExtensionRegistry — accumulation and retrieval."""

from __future__ import annotations

from unittest.mock import MagicMock

from kt_plugins.extension_points import ExtensionRegistry


def test_add_and_get_routes() -> None:
    reg = ExtensionRegistry()
    router = MagicMock()

    reg.add_routes(router, auth_required=True, prefix="billing", plugin_id="billing")

    routes = reg.get_routes()
    assert len(routes) == 1
    assert routes[0].router is router
    assert routes[0].auth_required is True
    assert routes[0].prefix == "billing"
    assert routes[0].plugin_id == "billing"


def test_add_routes_returns_copies() -> None:
    reg = ExtensionRegistry()
    reg.add_routes(MagicMock(), plugin_id="a")

    routes1 = reg.get_routes()
    routes2 = reg.get_routes()
    assert routes1 is not routes2


def test_add_and_get_providers() -> None:
    reg = ExtensionRegistry()
    factory = MagicMock()

    reg.add_provider(factory, plugin_id="jira")

    providers = reg.get_providers()
    assert len(providers) == 1
    assert providers[0].factory is factory
    assert providers[0].plugin_id == "jira"


def test_add_and_get_auth_backends() -> None:
    reg = ExtensionRegistry()
    factory = MagicMock()

    reg.add_auth_backend(factory, plugin_id="sso")

    backends = reg.get_auth_backends()
    assert len(backends) == 1
    assert backends[0].factory is factory


def test_add_and_get_workflows() -> None:
    reg = ExtensionRegistry()
    wf = MagicMock()

    reg.add_workflow(wf, plugin_id="billing")

    workflows = reg.get_workflows()
    assert len(workflows) == 1
    assert workflows[0].workflow is wf


def test_add_node_and_fact_types() -> None:
    reg = ExtensionRegistry()
    reg.add_node_type("custom_node", {"field": "value"})
    reg.add_fact_type("custom_fact")

    assert "custom_node" in reg.get_node_types()
    assert reg.get_node_types()["custom_node"] == {"field": "value"}
    assert "custom_fact" in reg.get_fact_types()
    assert reg.get_fact_types()["custom_fact"] == {}


def test_multiple_routes_from_different_plugins() -> None:
    reg = ExtensionRegistry()
    reg.add_routes(MagicMock(), plugin_id="billing")
    reg.add_routes(MagicMock(), plugin_id="sso", auth_required=False)

    routes = reg.get_routes()
    assert len(routes) == 2
    assert routes[0].plugin_id == "billing"
    assert routes[1].plugin_id == "sso"
    assert routes[1].auth_required is False
