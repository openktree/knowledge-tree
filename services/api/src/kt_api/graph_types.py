"""Read-only endpoints over the GraphTypePlugin registry.

Exposes the pipeline recipes available for graph creation + the composition
currently backing each running graph. Users pick a type at create time
(``POST /api/v1/graphs``) — versions are internal and not user-selectable,
so this router surfaces metadata only; no config editing here.

Configuration source of truth stays YAML + Settings — the config_schema
returned below is purely for the frontend's read-only display.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel

from kt_config.plugin import GraphTypeComposition, plugin_registry

router = APIRouter(prefix="/api/v1/graph-types", tags=["graph-types"])


class GraphTypeCompositionResponse(BaseModel):
    fetch_chain: list[str]
    search_providers: list[str]
    fact_decomposition: str
    concept_extractor: str
    disambiguation: str
    seed_multiplex: str
    seed_promotion: str
    dimensions: str
    definition: str
    relations: str
    sync: str
    source_cache: str
    source_contribution: str
    agentic_tasks: dict[str, str]

    @classmethod
    def from_composition(cls, c: GraphTypeComposition) -> "GraphTypeCompositionResponse":
        return cls(
            fetch_chain=list(c.fetch_chain),
            search_providers=list(c.search_providers),
            fact_decomposition=c.fact_decomposition,
            concept_extractor=c.concept_extractor,
            disambiguation=c.disambiguation,
            seed_multiplex=c.seed_multiplex,
            seed_promotion=c.seed_promotion,
            dimensions=c.dimensions,
            definition=c.definition,
            relations=c.relations,
            sync=c.sync,
            source_cache=c.source_cache,
            source_contribution=c.source_contribution,
            agentic_tasks=dict(c.agentic_tasks),
        )


class GraphTypeResponse(BaseModel):
    id: str
    display_name: str
    current_version: int
    composition: GraphTypeCompositionResponse
    default_phase_settings: dict[str, dict[str, Any]]
    config_schema: dict[str, Any]


@router.get("", response_model=list[GraphTypeResponse])
async def list_graph_types() -> list[GraphTypeResponse]:
    """Return every registered graph type + its current composition."""
    out: list[GraphTypeResponse] = []
    for plugin in plugin_registry.list_graph_types():
        out.append(
            GraphTypeResponse(
                id=plugin.graph_type_id,
                display_name=plugin.display_name,
                current_version=plugin.current_version,
                composition=GraphTypeCompositionResponse.from_composition(plugin.composition()),
                default_phase_settings=plugin.default_phase_settings(),
                config_schema=plugin.config_schema(),
            )
        )
    return out
