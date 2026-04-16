"""LlmEntityExtractor — implements EntityExtractor ABC via LLM extraction."""

from __future__ import annotations

from kt_core_engine_api.extractor import EntityExtractor, ExtractedEntity
from kt_models.gateway import ModelGateway

from .llm_extraction import extract_entities_from_facts


class LlmEntityExtractor(EntityExtractor):
    """LLM-based entity extractor.

    Wraps :func:`extract_entities_from_facts` and converts its dict output to
    :class:`ExtractedEntity` objects. Emits no shell candidates.
    """

    def __init__(self, gateway: ModelGateway) -> None:
        self._gateway = gateway

    async def extract(
        self,
        facts: list,
        *,
        scope: str = "",
    ) -> list[ExtractedEntity] | None:
        raw = await extract_entities_from_facts(facts, self._gateway, scope=scope)
        if not raw:
            return None
        return [
            ExtractedEntity(
                name=node["name"],
                fact_indices=node.get("fact_indices", []),
                aliases=node.get("aliases", []),
            )
            for node in raw
        ]
