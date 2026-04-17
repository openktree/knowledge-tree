"""Legacy ``BackendEnginePlugin`` ABC + adapter to the new manifest API.

Existing in-tree plugins (``backend-engine-concept-extractor``,
``backend-engine-search-providers``) and the core DB migration plugins
(``kt_db.core_plugin``) still target this ABC. New plugins should
instead expose a :class:`kt_plugins.manifest.PluginManifest` via an
``importlib.metadata`` entry point — but both paths funnel through the
same orchestrator.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from collections.abc import Iterable
from typing import TYPE_CHECKING, Any

from kt_plugins.extension_points import (
    EntityExtractorContribution,
    ExtensionRegistry,
    PluginDatabase,
    PluginType,
    PostExtractionHook,
    RouteContribution,
    SearchProviderContribution,
    WorkflowContribution,
)
from kt_plugins.manifest import PluginManifest

if TYPE_CHECKING:
    from kt_plugins.context import PluginContext

logger = logging.getLogger(__name__)


class BackendEnginePlugin(ABC):
    """Legacy plugin base — modifies data/information flow.

    Kept for back-compat with the 2 in-tree plugins and
    ``kt_db.core_plugin``. New plugins should use
    :class:`kt_plugins.manifest.PluginManifest` instead.
    """

    requires_license_key: bool = False

    @property
    @abstractmethod
    def plugin_id(self) -> str: ...

    @property
    def plugin_type(self) -> PluginType:
        return PluginType.backend_engine

    def get_database(self) -> PluginDatabase | None:
        return None

    def get_entity_extractor(self) -> EntityExtractorContribution | None:
        return None

    def get_entity_extractors(self) -> Iterable[EntityExtractorContribution]:
        single = self.get_entity_extractor()
        if single is not None:
            yield single

    def get_search_provider(self) -> SearchProviderContribution | None:
        return None

    def get_search_providers(self) -> Iterable[SearchProviderContribution]:
        single = self.get_search_provider()
        if single is not None:
            yield single

    def get_post_extraction_hooks(self) -> Iterable[PostExtractionHook]:
        return ()

    def get_routes(self) -> Iterable[RouteContribution]:
        return ()

    def get_workflows(self) -> Iterable[Any]:
        """Yield Hatchet workflow objects this plugin contributes."""
        return ()

    # Optional hook subscriptions: yield (hook_name, handler, priority)
    def get_hook_subscriptions(self) -> Iterable[tuple[str, Any, int]]:
        return ()


class BackendPlugin(ABC):
    """API-only plugin. No graph writes allowed. Reserved — no in-tree users."""

    @property
    @abstractmethod
    def plugin_id(self) -> str: ...


class FrontendPlugin(ABC):
    """Frontend extension. Reserved — no in-tree users."""

    @property
    @abstractmethod
    def plugin_id(self) -> str: ...


class _LegacyLifecycle:
    """Lifecycle adapter for a ``BackendEnginePlugin`` instance.

    ``register()`` pumps the ABC getters into the extension registry.
    ``bootstrap()`` wires hook subscriptions (so a legacy plugin can
    still subscribe to hooks without the new manifest API).
    """

    def __init__(self, plugin: BackendEnginePlugin) -> None:
        self._plugin = plugin

    async def register(self, registry: ExtensionRegistry) -> None:
        p = self._plugin
        db = p.get_database()
        if db is not None:
            registry.add_database(db)
        for contrib in p.get_entity_extractors():
            registry.add_entity_extractor(contrib)
        for contrib in p.get_search_providers():
            registry.add_provider(contrib)
        for hook in p.get_post_extraction_hooks():
            registry.add_post_extraction_hook(hook)
        for route in p.get_routes():
            registry.add_route(route)
        for wf in p.get_workflows():
            registry.workflows.append(WorkflowContribution(workflow=wf))

    async def bootstrap(self, ctx: "PluginContext") -> None:
        for hook_name, handler, priority in self._plugin.get_hook_subscriptions():
            ctx.hook_registry.register(
                hook_name,
                handler,
                priority=priority,
                plugin_id=ctx.plugin_id,
            )

    async def shutdown(self) -> None:
        return None


def legacy_to_manifest(plugin: BackendEnginePlugin) -> PluginManifest:
    """Wrap a ``BackendEnginePlugin`` instance in a ``PluginManifest``."""
    return PluginManifest(
        id=plugin.plugin_id,
        name=plugin.plugin_id,
        requires_license_key=plugin.requires_license_key,
        lifecycle=_LegacyLifecycle(plugin),
    )
