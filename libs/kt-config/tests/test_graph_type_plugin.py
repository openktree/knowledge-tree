"""Tests for GraphTypePlugin registration + lookup."""

from __future__ import annotations

from typing import Any

import pytest

from kt_config.plugin import (
    GraphTypeComposition,
    GraphTypePlugin,
    PluginRegistry,
)


class _TestGraphType(GraphTypePlugin):
    def __init__(
        self,
        *,
        graph_type_id: str = "test",
        display_name: str = "Test",
        current_version: int = 1,
    ) -> None:
        self._id = graph_type_id
        self._name = display_name
        self._version = current_version

    @property
    def graph_type_id(self) -> str:
        return self._id

    @property
    def display_name(self) -> str:
        return self._name

    @property
    def current_version(self) -> int:
        return self._version

    def composition(self) -> GraphTypeComposition:
        return GraphTypeComposition(
            fetch_chain=["httpx"],
            search_providers=["serper"],
            fact_decomposition="llm-default",
            concept_extractor="hybrid",
            disambiguation="default",
            seed_multiplex="default",
            seed_promotion="default",
            dimensions="default",
            definition="default",
            relations="default",
            sync="default",
            source_cache="public-graph",
            source_contribution="public-graph",
            agentic_tasks={"synthesizer": "langgraph-default"},
        )

    def default_phase_settings(self) -> dict[str, dict[str, Any]]:
        return {"search": {"providers": ["serper"]}}


def test_register_and_lookup() -> None:
    registry = PluginRegistry()
    plugin = _TestGraphType()
    registry.register_graph_type(plugin)
    assert registry.get_graph_type("test") is plugin
    assert plugin in registry.list_graph_types()


def test_register_is_idempotent() -> None:
    registry = PluginRegistry()
    plugin = _TestGraphType()
    registry.register_graph_type(plugin)
    registry.register_graph_type(plugin)
    # Same id with a different instance is also skipped — id is the key.
    registry.register_graph_type(_TestGraphType(current_version=99))
    assert len(registry.list_graph_types()) == 1
    assert registry.get_graph_type("test").current_version == 1


def test_unknown_type_returns_none() -> None:
    registry = PluginRegistry()
    assert registry.get_graph_type("missing") is None


def test_list_in_registration_order() -> None:
    registry = PluginRegistry()
    a = _TestGraphType(graph_type_id="a")
    b = _TestGraphType(graph_type_id="b")
    c = _TestGraphType(graph_type_id="c")
    for p in (a, b, c):
        registry.register_graph_type(p)
    assert [p.graph_type_id for p in registry.list_graph_types()] == ["a", "b", "c"]


def test_clear_removes_both_registries() -> None:
    registry = PluginRegistry()
    registry.register_graph_type(_TestGraphType())
    registry.clear()
    assert registry.list_graph_types() == []


def test_composition_is_frozen() -> None:
    plugin = _TestGraphType()
    composition = plugin.composition()
    with pytest.raises(Exception):
        composition.fetch_chain = ["httpx", "doi"]  # type: ignore[misc]
