"""Unit tests for the graph-types router."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from kt_api.graph_types import router as graph_types_router
from kt_config.plugin import (
    GraphTypeComposition,
    GraphTypePlugin,
    plugin_registry,
)


class _FakeGraphType(GraphTypePlugin):
    @property
    def graph_type_id(self) -> str:
        return "fake"

    @property
    def display_name(self) -> str:
        return "Fake Type"

    @property
    def current_version(self) -> int:
        return 3

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

    def config_schema(self) -> dict[str, Any]:
        return {"type": "object", "properties": {"search": {"type": "object"}}}


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    app.include_router(graph_types_router)
    return TestClient(app)


@pytest.fixture(autouse=True)
def _isolated_registry():
    """Snapshot + restore the global registry around each test."""
    originals = list(plugin_registry._graph_types)  # noqa: SLF001
    plugin_registry._graph_types.clear()  # noqa: SLF001
    yield
    plugin_registry._graph_types.clear()  # noqa: SLF001
    plugin_registry._graph_types.extend(originals)  # noqa: SLF001


def test_list_empty(client: TestClient) -> None:
    resp = client.get("/api/v1/graph-types")
    assert resp.status_code == 200
    assert resp.json() == []


def test_list_includes_registered_plugin(client: TestClient) -> None:
    plugin_registry.register_graph_type(_FakeGraphType())
    resp = client.get("/api/v1/graph-types")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    entry = body[0]
    assert entry["id"] == "fake"
    assert entry["display_name"] == "Fake Type"
    assert entry["current_version"] == 3
    assert entry["composition"]["fact_decomposition"] == "llm-default"
    assert entry["composition"]["search_providers"] == ["serper"]
    assert entry["composition"]["agentic_tasks"]["synthesizer"] == "langgraph-default"
    assert entry["default_phase_settings"] == {"search": {"providers": ["serper"]}}
    assert entry["config_schema"]["type"] == "object"
