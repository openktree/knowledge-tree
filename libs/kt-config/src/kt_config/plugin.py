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
from collections.abc import Awaitable, Iterable
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, ClassVar, Literal

if TYPE_CHECKING:
    from kt_core_engine_api.extractor import EntityExtractor
    from kt_core_engine_api.search import KnowledgeProvider
    from kt_models.gateway import ModelGateway

logger = logging.getLogger(__name__)


# ── Plugin type enum ──────────────────────────────────────────────────────


class PluginType(str, Enum):
    backend_engine = "backend-engine"  # modifies data/information flow
    backend = "backend"  # API-only (future)
    frontend = "frontend"  # UI types (future)


# ── Entry point containers ────────────────────────────────────────────────


DbTarget = Literal["graph", "write"]


@dataclass
class PluginDatabase:
    """Declares a database schema owned by a plugin.

    ``target`` selects which physical database the migration runs on:
      - ``"write"`` (default, back-compat): write-db
      - ``"graph"``: graph-db

    The plugin's Alembic config is responsible for:
    - targeting the correct DB URL (caller passes via ``sqlalchemy.url``)
    - honouring ``ALEMBIC_SCHEMA`` env var when set (for per-schema routing)
    - sets ``version_table_schema`` / ``search_path`` appropriately

    ``schema_env_var`` lets a plugin opt-in to per-schema migrations driven
    by the caller; set to ``"ALEMBIC_SCHEMA"`` for the kt-db convention.
    """

    plugin_id: str
    schema_name: str  # e.g. "plugin_hybrid_extractor" or "public"
    alembic_config_path: Path  # path to plugin's alembic.ini
    target: DbTarget = "write"
    schema_env_var: str | None = None

    async def ensure_migrated(
        self,
        db_url: str,
        *,
        schema: str | None = None,
    ) -> None:
        """Run ``alembic upgrade head`` for this plugin's schema.

        Idempotent — Alembic tracks applied versions in the schema.
        Runs in a thread to avoid blocking the event loop (Alembic
        internally calls ``asyncio.run()`` in its async env.py, which
        cannot nest inside a running loop).

        ``schema`` overrides ``schema_name`` for this call only; if
        ``schema_env_var`` is set, the value is exported into the env
        for the duration of the alembic invocation.
        """
        config_path = self.alembic_config_path
        active_schema = schema or self.schema_name
        plugin_id = self.plugin_id
        schema_env_var = self.schema_env_var
        # Normalise: strip %% so Alembic / ConfigParser can handle the URL
        safe_url = db_url.replace("%", "%%")

        def _run() -> None:
            import os as _os

            from alembic import command
            from alembic.config import Config

            prev_env: str | None = None
            if schema_env_var is not None:
                prev_env = _os.environ.get(schema_env_var)
                _os.environ[schema_env_var] = active_schema
            try:
                cfg = Config(str(config_path))
                cfg.set_main_option("sqlalchemy.url", safe_url)
                command.upgrade(cfg, "head")
                logger.info(
                    "Plugin DB migrated: %s (schema=%s, target=%s)",
                    plugin_id,
                    active_schema,
                    self.target,
                )
            except Exception:
                # Surface the failure here — exceptions raised from a
                # ``to_thread`` worker can otherwise be swallowed by the
                # surrounding lifespan plumbing, leaving startup hung with
                # no traceback in the log.
                logger.exception(
                    "Alembic upgrade failed: plugin=%s schema=%s target=%s url=%s config=%s",
                    plugin_id,
                    active_schema,
                    self.target,
                    db_url,
                    config_path,
                )
                raise
            finally:
                if schema_env_var is not None:
                    if prev_env is None:
                        _os.environ.pop(schema_env_var, None)
                    else:
                        _os.environ[schema_env_var] = prev_env

        await asyncio.to_thread(_run)


#: Signature: ``async def handler(write_session, items, scope) -> None``.
#: The pipeline invokes a ``PostExtractionHook`` for every matching
#: ``output_key`` returned by an extractor's ``get_last_side_outputs()``.
#: Plugin-specific persistence (e.g. writing shell candidates to a plugin
#: DB schema) lives in the hook, keeping ``kt-facts`` plugin-agnostic.
PostExtractionHandler = Callable[[Any, list, str], Awaitable[None]]


@dataclass
class PostExtractionHook:
    """Declares a plugin-owned handler for one side-output key.

    ``extractor_name`` scopes the hook to a specific extractor (e.g.
    ``"hybrid"``). ``None`` means "any extractor" — the handler fires
    whenever the matching ``output_key`` is present.

    ``output_key`` must match a key returned by
    :meth:`EntityExtractor.get_last_side_outputs`.
    """

    extractor_name: str | None
    output_key: str
    handler: PostExtractionHandler


@dataclass
class SearchProviderContribution:
    """Declares a KnowledgeProvider provided by a plugin.

    ``provider_id`` must match ``settings.default_search_provider`` (or be
    selected via ``"all"``) to be registered at worker startup.
    ``factory`` is called lazily — only when the provider is actually needed.
    ``is_available`` gates registration on runtime prerequisites (env vars,
    credentials, etc.); defaults to always-available.
    """

    provider_id: str
    factory: Callable[[], "KnowledgeProvider"]
    is_available: Callable[[], bool] = lambda: True


@dataclass
class EntityExtractorContribution:
    """Declares a named EntityExtractor provided by a plugin.

    ``extractor_name`` must match ``settings.entity_extractor`` to be selected.
    ``factory`` is called lazily — only when the extractor is actually needed.

    Side-output persistence (e.g. shell candidates, rejected terms) is
    handled by separate :class:`PostExtractionHook` contributions — not on
    this type. Add a hook via ``BackendEnginePlugin.get_post_extraction_hooks``.
    """

    extractor_name: str  # e.g. "hybrid"
    factory: Callable[["ModelGateway"], "EntityExtractor"]


# ── Plugin type ABCs ──────────────────────────────────────────────────────


class BackendEnginePlugin(ABC):
    """Plugin that modifies the data/information flow of the processing pipeline.

    Plugin ID must follow convention: ``backend-engine-<feature>``.

    Override ``get_database()`` and/or ``get_entity_extractor()`` to contribute
    entry points. Base implementations return ``None`` (opt-out by default).
    """

    plugin_id: ClassVar[str]
    """Unique plugin ID, e.g. ``backend-engine-hybrid-concept-extractor``. Subclasses must set."""

    @property
    def plugin_type(self) -> PluginType:
        return PluginType.backend_engine

    def get_database(self) -> PluginDatabase | None:
        """Return a PluginDatabase if this plugin owns a write-db schema."""
        return None

    def get_entity_extractor(self) -> EntityExtractorContribution | None:
        """Legacy single-extractor accessor.

        Prefer :meth:`get_entity_extractors` for plugins that provide more
        than one named extractor. Default returns ``None``.
        """
        return None

    def get_entity_extractors(self) -> Iterable[EntityExtractorContribution]:
        """Yield every EntityExtractorContribution this plugin provides.

        Default falls back to :meth:`get_entity_extractor` so existing
        single-extractor plugins keep working unchanged.
        """
        single = self.get_entity_extractor()
        if single is not None:
            yield single

    def get_search_provider(self) -> SearchProviderContribution | None:
        """Legacy single-provider accessor.

        Prefer :meth:`get_search_providers` for plugins that provide more
        than one named provider. Default returns ``None``.
        """
        return None

    def get_search_providers(self) -> Iterable[SearchProviderContribution]:
        """Yield every SearchProviderContribution this plugin provides.

        Default falls back to :meth:`get_search_provider` so existing
        single-provider plugins keep working unchanged.
        """
        single = self.get_search_provider()
        if single is not None:
            yield single

    def get_post_extraction_hooks(self) -> Iterable[PostExtractionHook]:
        """Yield any post-extraction hooks this plugin contributes.

        Default is empty. Plugins that persist extractor side outputs (e.g.
        shell candidates) return one hook per side-output key they handle.
        """
        return ()

    # Future entry points (not yet defined):
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

    def clear(self) -> None:
        """Remove every registered plugin. Intended for test isolation."""
        self._backend_engine.clear()

    async def run_database_migrations(
        self,
        write_db_urls: Iterable[str] | None = None,
        *,
        graph_db_urls: Iterable[str] | None = None,
        strict: bool = False,
        schema: str | None = None,
    ) -> None:
        """Run ``ensure_migrated`` for every registered plugin database.

        Each plugin's ``PluginDatabase.target`` selects whether it runs
        against the supplied graph-db URLs or write-db URLs. The URL list
        typically contains the system DB plus every active non-default
        graph's physical DB URL.

        When ``strict=False`` (default), a failed plugin migration is
        logged and skipped so worker startup is not blocked by a bad
        third-party plugin. When ``strict=True``, exceptions propagate —
        used by core migrations where a failure must abort startup.

        ``schema`` overrides each plugin's ``schema_name`` for this run —
        used for per-graph migrations that reuse the same Alembic config
        but target different schemas.
        """
        write_urls = list(dict.fromkeys(write_db_urls or ()))
        graph_urls = list(dict.fromkeys(graph_db_urls or ()))
        for plugin in self._backend_engine:
            db = plugin.get_database()
            if db is None:
                continue
            urls = graph_urls if db.target == "graph" else write_urls
            for url in urls:
                try:
                    await db.ensure_migrated(url, schema=schema)
                except Exception:
                    if strict:
                        raise
                    logger.exception(
                        "Plugin DB migration failed for %s (schema=%s, target=%s, url=%s) — skipping",
                        plugin.plugin_id,
                        db.schema_name,
                        db.target,
                        url,
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
            for contrib in plugin.get_entity_extractors():
                if contrib.extractor_name == name:
                    return contrib.factory(gateway)
        return None

    def iter_search_providers(self) -> Iterable[SearchProviderContribution]:
        """Yield every ``SearchProviderContribution`` across registered plugins."""
        for plugin in self._backend_engine:
            yield from plugin.get_search_providers()

    def iter_post_extraction_hooks(
        self,
        extractor_name: str,
    ) -> Iterable[PostExtractionHook]:
        """Yield hooks matching ``extractor_name`` (plus extractor-agnostic hooks).

        A hook with ``extractor_name=None`` fires for every extractor; a hook
        with a specific name fires only when that extractor is selected.
        """
        for plugin in self._backend_engine:
            for hook in plugin.get_post_extraction_hooks():
                if hook.extractor_name is None or hook.extractor_name == extractor_name:
                    yield hook


# Module-level singleton — import and use directly.
plugin_registry = PluginRegistry()


# ── Bootstrap helper ─────────────────────────────────────────────────────

_DEFAULT_PLUGIN_TARGETS: list[tuple[str, str]] = [
    ("kt_plugin_be_concept_extractor.plugin", "ConceptExtractorBackendEnginePlugin"),
    ("kt_plugin_be_search_providers.plugin", "SearchProvidersBackendEnginePlugin"),
]


def load_default_plugins(
    *,
    targets: list[tuple[str, str]] | None = None,
) -> None:
    """Import and register the standard set of backend-engine plugins.

    Each target is ``(module_path, class_name)``. Plugins that are not
    installed are silently skipped. Safe to call multiple times — the
    registry is idempotent.
    """
    import importlib

    for module_path, class_name in targets or _DEFAULT_PLUGIN_TARGETS:
        try:
            module = importlib.import_module(module_path)
        except ImportError:
            logger.debug("Plugin %s not installed — skipping", module_path)
            continue
        plugin = getattr(module, class_name)()
        plugin_registry.register_backend_engine(plugin)
