"""Plugin extension-point contribution types.

These dataclasses declare what a plugin contributes to the platform.
They are collected either via the legacy ``BackendEnginePlugin`` ABC
methods or the new ``PluginManifest`` / ``PluginLifecycle`` protocol.

Auth backends are deliberately NOT a contribution type — authentication
stays in core (``services/api/src/kt_api/auth/``). Plugins that need
audit or enforcement use the ``auth.*`` hooks instead.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Iterable
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Literal

if TYPE_CHECKING:
    from fastapi import APIRouter

logger = logging.getLogger(__name__)


# ── Plugin type enum ──────────────────────────────────────────────────────


class PluginType(str, Enum):
    backend_engine = "backend-engine"  # modifies data/information flow
    backend = "backend"                # API-only
    frontend = "frontend"              # UI types


# ── DB migration contribution ─────────────────────────────────────────────


DbTarget = Literal["graph", "write"]


@dataclass
class PluginDatabase:
    """Declares a database schema owned by a plugin.

    ``target`` selects which physical DB the migration runs on:
    ``"write"`` (default, back-compat) or ``"graph"``.

    The plugin's Alembic config honours ``schema_env_var`` when set, so a
    single config can be reused across schemas (multigraph per-schema
    migrations pass the schema name via env at upgrade time).
    """

    plugin_id: str
    schema_name: str
    alembic_config_path: Path
    target: DbTarget = "write"
    schema_env_var: str | None = None

    async def ensure_migrated(
        self,
        db_url: str,
        *,
        schema: str | None = None,
    ) -> None:
        """Run ``alembic upgrade head`` for this plugin's schema.

        Runs Alembic in a thread because its env.py may call
        ``asyncio.run()`` which cannot nest inside a running loop.
        """
        config_path = self.alembic_config_path
        active_schema = schema or self.schema_name
        plugin_id = self.plugin_id
        schema_env_var = self.schema_env_var
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
                    "plugin DB migrated: %s (schema=%s, target=%s)",
                    plugin_id,
                    active_schema,
                    self.target,
                )
            except Exception:
                logger.exception(
                    "alembic upgrade failed: plugin=%s schema=%s target=%s url=%s config=%s",
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


# ── Extractor / provider / post-extraction contributions ──────────────────


#: Signature: ``async def handler(write_session, items, scope) -> None``.
PostExtractionHandler = Callable[[Any, list, str], Awaitable[None]]


@dataclass
class PostExtractionHook:
    """Declares a plugin-owned handler for one extractor side-output key.

    ``extractor_name=None`` fires for any extractor.
    """

    extractor_name: str | None
    output_key: str
    handler: PostExtractionHandler


@dataclass
class SearchProviderContribution:
    """Declares a ``KnowledgeProvider`` instance the plugin can create.

    ``factory`` is called lazily; ``is_available`` gates registration on
    runtime prerequisites (env vars, credentials).
    """

    provider_id: str
    factory: Callable[[], Any]  # () -> kt_core_engine_api.search.KnowledgeProvider
    is_available: Callable[[], bool] = lambda: True


@dataclass
class EntityExtractorContribution:
    """Declares a named ``EntityExtractor`` the plugin can create.

    ``extractor_name`` must match ``settings.entity_extractor`` to be
    selected. Side-output persistence is handled by
    :class:`PostExtractionHook` contributions — not on this type.
    """

    extractor_name: str
    factory: Callable[[Any], Any]  # (ModelGateway) -> EntityExtractor


# ── Route / workflow contributions (new) ──────────────────────────────────


@dataclass
class RouteContribution:
    """Declares a FastAPI router the plugin contributes.

    Mounted under ``/api/v1/plugins/{prefix}``. When
    ``require_permission`` is set, the mounted router is guarded by a
    dependency that enforces that ``kt_rbac.Permission`` before any
    route on it runs.
    """

    router: "APIRouter"
    prefix: str
    auth_required: bool = True
    require_permission: Any | None = None  # kt_rbac.Permission — typed at use-site


@dataclass
class WorkflowContribution:
    """Declares a Hatchet workflow the plugin contributes."""

    workflow: Any


#: Signature: ``async def handler(**kwargs) -> Any`` for trigger hooks;
#: ``async def handler(value, **kwargs) -> Any`` for filter hooks.
HookSubscription = tuple[str, Callable[..., Awaitable[Any]], int]  # (hook_name, handler, priority)


# ── ExtensionRegistry ─────────────────────────────────────────────────────


@dataclass
class ExtensionRegistry:
    """Collects contributions across all plugins during the register phase."""

    routes: list[RouteContribution] = field(default_factory=list)
    workflows: list[WorkflowContribution] = field(default_factory=list)
    providers: list[SearchProviderContribution] = field(default_factory=list)
    entity_extractors: list[EntityExtractorContribution] = field(default_factory=list)
    post_extraction_hooks: list[PostExtractionHook] = field(default_factory=list)
    databases: list[PluginDatabase] = field(default_factory=list)

    def add_route(self, contrib: RouteContribution) -> None:
        self.routes.append(contrib)

    def add_workflow(self, workflow: Any) -> None:
        self.workflows.append(WorkflowContribution(workflow=workflow))

    def add_provider(self, contrib: SearchProviderContribution) -> None:
        self.providers.append(contrib)

    def add_entity_extractor(self, contrib: EntityExtractorContribution) -> None:
        self.entity_extractors.append(contrib)

    def add_post_extraction_hook(self, contrib: PostExtractionHook) -> None:
        self.post_extraction_hooks.append(contrib)

    def add_database(self, contrib: PluginDatabase) -> None:
        self.databases.append(contrib)

    def iter_post_extraction_hooks(self, extractor_name: str) -> Iterable[PostExtractionHook]:
        for hook in self.post_extraction_hooks:
            if hook.extractor_name is None or hook.extractor_name == extractor_name:
                yield hook

    def get_entity_extractor_factory(self, name: str) -> Callable[[Any], Any] | None:
        for contrib in self.entity_extractors:
            if contrib.extractor_name == name:
                return contrib.factory
        return None
