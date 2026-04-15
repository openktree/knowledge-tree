"""Plugin entry point framework for Knowledge Tree.

Modeled after Backstage's plugin system. Plugins live in ``plugins/`` and
are registered explicitly at worker startup — never auto-imported.

Naming convention for plugin IDs:
  ``backend-engine-<feature>`` — modifies information flow (entity extraction, etc.)
  ``backend-<feature>``        — API-only plugin, no graph writes (future)
  ``frontend-<feature>``       — UI extension, TypeScript types only (future)

Only ``backend-engine`` plugins are active in this iteration.
"""

from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    from kt_facts.processing.extractor_base import EntityExtractor
    from kt_models.gateway import ModelGateway

logger = logging.getLogger(__name__)


# ── Plugin type enum ──────────────────────────────────────────────────────


class PluginType(str, Enum):
    backend_engine = "backend-engine"  # modifies data/information flow
    backend = "backend"                # API-only (future)
    frontend = "frontend"              # UI types (future)


# ── Entry point containers ────────────────────────────────────────────────


@dataclass
class PluginDatabase:
    """Declares a write-db schema owned by a plugin.

    The plugin is responsible for providing an Alembic config that:
    - targets the write-db URL
    - sets ``version_table_schema`` to ``schema_name``
    - sets ``search_path`` to ``schema_name, public``
    """

    plugin_id: str
    schema_name: str           # e.g. "plugin_hybrid_extractor"
    alembic_config_path: Path  # path to plugin's alembic.ini

    async def ensure_migrated(self, write_db_url: str) -> None:
        """Run ``alembic upgrade head`` for this plugin's schema.

        Idempotent — Alembic tracks applied versions in the schema.
        Runs in a thread to avoid blocking the event loop (Alembic
        internally calls ``asyncio.run()`` in its async env.py, which
        cannot nest inside a running loop).
        """
        config_path = self.alembic_config_path
        schema = self.schema_name
        # Normalise: strip +asyncpg so Alembic can also build a sync engine
        # when needed, but the plugin's own env.py drives the actual engine.
        safe_url = write_db_url.replace("%", "%%")

        def _run() -> None:
            from alembic import command
            from alembic.config import Config

            cfg = Config(str(config_path))
            cfg.set_main_option("sqlalchemy.url", safe_url)
            command.upgrade(cfg, "head")
            logger.info("Plugin DB migrated: %s (schema=%s)", self.plugin_id, schema)

        await asyncio.to_thread(_run)


@dataclass
class EntityExtractorContribution:
    """Declares a named EntityExtractor provided by a plugin.

    ``extractor_name`` must match ``settings.entity_extractor`` to be selected.
    ``factory`` is called lazily — only when the extractor is actually needed.
    """

    extractor_name: str   # e.g. "hybrid"
    factory: Callable[["ModelGateway"], "EntityExtractor"]


# ── Plugin type ABCs ──────────────────────────────────────────────────────


class BackendEnginePlugin(ABC):
    """Plugin that modifies the data/information flow of the processing pipeline.

    Plugin ID must follow convention: ``backend-engine-<feature>``.

    Override ``get_database()`` and/or ``get_entity_extractor()`` to contribute
    entry points. Base implementations return ``None`` (opt-out by default).
    """

    @property
    @abstractmethod
    def plugin_id(self) -> str:
        """Unique plugin ID, e.g. ``backend-engine-hybrid-extractor``."""

    @property
    def plugin_type(self) -> PluginType:
        return PluginType.backend_engine

    def get_database(self) -> PluginDatabase | None:
        """Return a PluginDatabase if this plugin owns a write-db schema."""
        return None

    def get_entity_extractor(self) -> EntityExtractorContribution | None:
        """Return an EntityExtractorContribution if this plugin provides one."""
        return None

    # Future entry points (not yet defined):
    # def get_search_provider(self) -> SearchProviderContribution | None: ...
    # def get_fact_processor(self) -> FactProcessorContribution | None: ...


class BackendPlugin(ABC):
    """API-only plugin. No graph writes allowed. ID: ``backend-<feature>``.

    Not active in this iteration — reserved for future API extension plugins.
    """

    @property
    @abstractmethod
    def plugin_id(self) -> str: ...


class FrontendPlugin(ABC):
    """Frontend extension. TypeScript types only. ID: ``frontend-<feature>``.

    Not active in this iteration — reserved for future UI extension plugins.
    """

    @property
    @abstractmethod
    def plugin_id(self) -> str: ...


# ── Plugin registry ───────────────────────────────────────────────────────


class PluginRegistry:
    """Central registry for all plugin entry points.

    Workers register plugins explicitly at startup (before ``worker.start()``).
    The registry is then used by ``worker_lifespan`` to run migrations and by
    ``DecompositionPipeline._make_extractor()`` to resolve named extractors.
    """

    def __init__(self) -> None:
        self._backend_engine: list[BackendEnginePlugin] = []

    def register_backend_engine(self, plugin: BackendEnginePlugin) -> None:
        """Register a BackendEnginePlugin.

        Idempotent by plugin_id — registering the same plugin twice is a no-op.
        """
        for existing in self._backend_engine:
            if existing.plugin_id == plugin.plugin_id:
                logger.debug("Plugin already registered, skipping: %s", plugin.plugin_id)
                return
        self._backend_engine.append(plugin)
        logger.debug("Registered backend-engine plugin: %s", plugin.plugin_id)

    async def run_database_migrations(self, write_db_url: str) -> None:
        """Run ``ensure_migrated`` for every registered plugin database.

        Never raises — a failed plugin migration is logged and skipped so that
        worker startup is not blocked by a plugin issue.
        """
        for plugin in self._backend_engine:
            db = plugin.get_database()
            if db is None:
                continue
            try:
                await db.ensure_migrated(write_db_url)
            except Exception:
                logger.exception(
                    "Plugin DB migration failed for %s (schema=%s) — skipping",
                    plugin.plugin_id,
                    db.schema_name,
                )

    def get_entity_extractor(
        self,
        name: str,
        gateway: "ModelGateway",
    ) -> "EntityExtractor | None":
        """Look up an EntityExtractor by name from registered plugins.

        Returns ``None`` if no plugin provides an extractor with that name.
        """
        for plugin in self._backend_engine:
            contrib = plugin.get_entity_extractor()
            if contrib is not None and contrib.extractor_name == name:
                return contrib.factory(gateway)
        return None


# Module-level singleton — import and use directly.
plugin_registry = PluginRegistry()
