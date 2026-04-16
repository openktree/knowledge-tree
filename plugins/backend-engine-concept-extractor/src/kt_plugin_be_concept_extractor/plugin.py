"""Plugin declaration for backend-engine-concept-extractor.

Single plugin exposing three concept-extraction strategies:
- ``spacy``  — pure spaCy NER + noun chunks, no LLM cost.
- ``llm``    — per-fact LLM extraction with dedup + alias generation.
- ``hybrid`` — spaCy recall + LLM shell classifier + alias generator.

The shared plugin DB schema (``plugin_hybrid_extractor``) and the shell
persistence hook are active only when the hybrid strategy is selected;
the other two strategies pay no DB cost.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import Any

from kt_config.plugin import (
    BackendEnginePlugin,
    EntityExtractorContribution,
    PluginDatabase,
    PostExtractionHook,
)


def _locate_alembic_ini() -> Path:
    """Walk up from this module until we find ``alembic_hybrid.ini``."""
    start = Path(__file__).resolve()
    for parent in start.parents:
        candidate = parent / "alembic_hybrid.ini"
        if candidate.is_file():
            return candidate
    raise FileNotFoundError(f"alembic_hybrid.ini not found in any parent of {start}")


async def _persist_shells(write_session: Any, shells: list, scope: str) -> None:
    """Forward shells captured by the hybrid extractor to the plugin DB.

    Does not commit — the caller (DecompositionPipeline) owns the transaction.
    """
    from kt_plugin_be_concept_extractor.repository import ShellCandidateRepository

    repo = ShellCandidateRepository(write_session)
    await repo.bulk_insert(shells, scope=scope)


class ConceptExtractorBackendEnginePlugin(BackendEnginePlugin):
    """Unified backend-engine plugin for every concept-extraction strategy.

    Entry points:
    - PluginDatabase: ``plugin_hybrid_extractor`` schema on write-db
      (only exercised when hybrid strategy is selected + used)
    - EntityExtractorContribution x 3: ``spacy`` / ``llm`` / ``hybrid``
    - PostExtractionHook: persists ``"shells"`` side output for hybrid
    """

    plugin_id = "backend-engine-concept-extractor"

    def get_database(self) -> PluginDatabase:
        return PluginDatabase(
            plugin_id=self.plugin_id,
            schema_name="plugin_hybrid_extractor",
            alembic_config_path=_locate_alembic_ini(),
        )

    def get_entity_extractors(self) -> Iterable[EntityExtractorContribution]:
        # Import lazily so plugin registration stays cheap and optional
        # dependencies (spaCy model, LLM gateway) are only pulled in at
        # extractor-instantiation time.
        from kt_plugin_be_concept_extractor.strategies.hybrid import HybridEntityExtractor
        from kt_plugin_be_concept_extractor.strategies.llm import LlmEntityExtractor
        from kt_plugin_be_concept_extractor.strategies.spacy import SpacyEntityExtractor

        yield EntityExtractorContribution(
            extractor_name="spacy",
            factory=lambda _gateway: SpacyEntityExtractor(),
        )
        yield EntityExtractorContribution(
            extractor_name="llm",
            factory=lambda gateway: LlmEntityExtractor(gateway),
        )
        yield EntityExtractorContribution(
            extractor_name="hybrid",
            factory=lambda gateway: HybridEntityExtractor(gateway),
        )

    def get_post_extraction_hooks(self) -> Iterable[PostExtractionHook]:
        yield PostExtractionHook(
            extractor_name="hybrid",
            output_key="shells",
            handler=_persist_shells,
        )
