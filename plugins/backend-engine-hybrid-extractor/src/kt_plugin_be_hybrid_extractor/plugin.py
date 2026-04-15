"""Plugin declaration for backend-engine-hybrid-extractor."""

from __future__ import annotations

from pathlib import Path

from kt_config.plugin import (
    BackendEnginePlugin,
    EntityExtractorContribution,
    PluginDatabase,
)


class HybridExtractorBackendEnginePlugin(BackendEnginePlugin):
    """Backend-engine plugin providing the hybrid spaCy+LLM entity extractor.

    Entry points:
    - PluginDatabase: ``plugin_hybrid_extractor`` schema on write-db
    - EntityExtractorContribution: extractor name ``"hybrid"``
    """

    plugin_id = "backend-engine-hybrid-extractor"

    def get_database(self) -> PluginDatabase:
        return PluginDatabase(
            plugin_id=self.plugin_id,
            schema_name="plugin_hybrid_extractor",
            alembic_config_path=Path(__file__).parent.parent.parent.parent / "alembic_hybrid.ini",
        )

    def get_entity_extractor(self) -> EntityExtractorContribution:
        from kt_plugin_be_hybrid_extractor.extractor import HybridEntityExtractor

        return EntityExtractorContribution(
            extractor_name="hybrid",
            factory=lambda gateway: HybridEntityExtractor(gateway),
        )
