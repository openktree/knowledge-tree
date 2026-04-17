"""Default graph type plugin — the baseline pipeline recipe.

Ships as an internal plugin (like ``CoreGraphDbPlugin`` / ``CoreWriteDbPlugin``
in ``core_plugin.py``), registered at startup by
``kt_config.plugin.load_default_plugins``. The composition mirrors today's
behaviour so existing graphs keep working unchanged once the versioning
scaffolding lands.

Phase-plan context: during Phase 1 many of the provider ids below refer to
inline implementations inside the libs/workers — the registry lookups only
start driving real dispatch in Phase 2+ as each phase is extracted to a
plugin. The ids themselves are stable contracts, so the composition does
not change as code migrates into ``plugins/backend-engine-*``.
"""

from __future__ import annotations

from typing import Any

from kt_config.plugin import GraphTypeComposition, GraphTypePlugin


class DefaultGraphTypePlugin(GraphTypePlugin):
    """Baseline graph type — the recipe every graph starts from."""

    @property
    def graph_type_id(self) -> str:
        return "default"

    @property
    def display_name(self) -> str:
        return "Default"

    @property
    def current_version(self) -> int:
        return 1

    def composition(self) -> GraphTypeComposition:
        return GraphTypeComposition(
            fetch_chain=["doi", "curl_cffi", "httpx", "flaresolverr"],
            search_providers=["serper", "brave_search", "openalex"],
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
            agentic_tasks={
                "synthesizer": "langgraph-default",
                "super_synthesizer": "langgraph-default",
            },
        )

    def default_phase_settings(self) -> dict[str, dict[str, Any]]:
        # Phase 2 populates this with the per-phase defaults now scattered
        # across ``config.yaml``. During Phase 1 the resolver still falls
        # back to flat ``Settings`` fields, so an empty dict is correct.
        return {}

    def config_schema(self) -> dict[str, Any]:
        # Phase 4 fills this out once the settings page renders a form.
        return {"type": "object", "properties": {}}
