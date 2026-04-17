"""Tests for GraphConfigResolver — YAML resolution order + caching."""

from __future__ import annotations

import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from kt_config.graph_config import GraphConfig, GraphConfigResolver
from kt_config.plugin import (
    GraphTypeComposition,
    GraphTypePlugin,
    plugin_registry,
)


class _TestGraphType(GraphTypePlugin):
    @property
    def graph_type_id(self) -> str:
        return "test-default"

    @property
    def display_name(self) -> str:
        return "Test"

    @property
    def current_version(self) -> int:
        return 1

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
        return {
            "fact_decomposition": {"model": "plugin-default-model"},
            "search": {"providers": ["plugin-default-provider"]},
        }


@pytest.fixture(autouse=True)
def _isolated_registry():
    originals = list(plugin_registry._graph_types)  # noqa: SLF001
    plugin_registry._graph_types.clear()  # noqa: SLF001
    plugin_registry.register_graph_type(_TestGraphType())
    yield
    plugin_registry._graph_types.clear()  # noqa: SLF001
    plugin_registry._graph_types.extend(originals)  # noqa: SLF001


def _write_config_yaml(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "config.yaml"
    path.write_text(body)
    return path


def _fake_graph(slug: str, graph_type_id: str = "test-default", version: int = 1):
    return SimpleNamespace(
        id=uuid.uuid4(),
        slug=slug,
        graph_type_id=graph_type_id,
        graph_type_version=version,
    )


@pytest.mark.asyncio
async def test_plugin_defaults_only(tmp_path: Path) -> None:
    """No YAML graphs: section → plugin defaults surface verbatim."""
    path = _write_config_yaml(tmp_path, "models:\n  default: foo\n")
    resolver = GraphConfigResolver(yaml_path=path)
    config = await resolver.resolve(_fake_graph("research"))
    assert config.get("fact_decomposition.model") == "plugin-default-model"
    assert config.for_phase("search")["providers"] == ["plugin-default-provider"]


@pytest.mark.asyncio
async def test_shared_overrides_plugin_default(tmp_path: Path) -> None:
    path = _write_config_yaml(
        tmp_path,
        """
graphs:
  _shared:
    fact_decomposition:
      model: shared-model
""",
    )
    resolver = GraphConfigResolver(yaml_path=path)
    config = await resolver.resolve(_fake_graph("research"))
    assert config.get("fact_decomposition.model") == "shared-model"


@pytest.mark.asyncio
async def test_per_graph_overrides_shared(tmp_path: Path) -> None:
    path = _write_config_yaml(
        tmp_path,
        """
graphs:
  _shared:
    fact_decomposition:
      model: shared-model
  research:
    fact_decomposition:
      model: research-specific-model
""",
    )
    resolver = GraphConfigResolver(yaml_path=path)
    research = await resolver.resolve(_fake_graph("research"))
    other = await resolver.resolve(_fake_graph("other"))
    assert research.get("fact_decomposition.model") == "research-specific-model"
    assert other.get("fact_decomposition.model") == "shared-model"


@pytest.mark.asyncio
async def test_composition_from_plugin(tmp_path: Path) -> None:
    path = _write_config_yaml(tmp_path, "graphs:\n  _shared: {}\n")
    resolver = GraphConfigResolver(yaml_path=path)
    config = await resolver.resolve(_fake_graph("research"))
    assert config.composition.concept_extractor == "hybrid"
    assert config.composition.search_providers == ["serper"]


@pytest.mark.asyncio
async def test_unknown_type_falls_back_to_default(tmp_path: Path) -> None:
    """Unregistered graph_type_id logs a warning and uses 'default' plugin.

    We registered our plugin as 'test-default' above; 'default' is not
    registered in the isolated registry. The resolver must still return
    a usable empty composition.
    """
    path = _write_config_yaml(tmp_path, "")
    resolver = GraphConfigResolver(yaml_path=path)
    config = await resolver.resolve(_fake_graph("x", graph_type_id="ghost"))
    assert isinstance(config, GraphConfig)
    # Empty-composition fallback — safe defaults so pipelines still run.
    assert config.composition.fetch_chain == []


@pytest.mark.asyncio
async def test_cache_hits_and_invalidate(tmp_path: Path) -> None:
    path = _write_config_yaml(tmp_path, "")
    resolver = GraphConfigResolver(yaml_path=path)
    graph = _fake_graph("research")
    first = await resolver.resolve(graph)
    second = await resolver.resolve(graph)
    assert first is second
    resolver.invalidate(graph.id)
    third = await resolver.resolve(graph)
    assert third is not second


@pytest.mark.asyncio
async def test_none_graph_returns_default_config(tmp_path: Path) -> None:
    path = _write_config_yaml(tmp_path, "")
    resolver = GraphConfigResolver(yaml_path=path)
    config = await resolver.resolve(None)
    assert config.graph_type_id == "default"
    assert config.graph_type_version == 1


@pytest.mark.asyncio
async def test_get_requires_dotted_path(tmp_path: Path) -> None:
    path = _write_config_yaml(tmp_path, "")
    resolver = GraphConfigResolver(yaml_path=path)
    config = await resolver.resolve(_fake_graph("research"))
    with pytest.raises(ValueError):
        config.get("not_a_dotted_path")


@pytest.mark.asyncio
async def test_get_returns_default_when_missing(tmp_path: Path) -> None:
    path = _write_config_yaml(tmp_path, "")
    resolver = GraphConfigResolver(yaml_path=path)
    config = await resolver.resolve(_fake_graph("research"))
    assert config.get("nonexistent.key", default="fallback") == "fallback"
