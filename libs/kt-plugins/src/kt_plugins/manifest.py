"""Plugin manifest and lifecycle protocol."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from kt_plugins.context import PluginContext
    from kt_plugins.extension_points import ExtensionRegistry


@runtime_checkable
class PluginLifecycle(Protocol):
    """Protocol that plugin lifecycle implementations must satisfy.

    Plugins implement this to declare extensions (register) and
    perform setup with runtime services (bootstrap).
    """

    async def register(self, registry: ExtensionRegistry) -> None:
        """Called during registration phase.

        Declare routes, providers, hooks, workflows, etc.
        Cannot access other plugins or runtime services yet.
        """
        ...

    async def bootstrap(self, ctx: PluginContext) -> None:
        """Called after all plugins are registered.

        Access runtime services, subscribe to hooks, perform startup logic.
        """
        ...

    async def shutdown(self) -> None:
        """Called on application shutdown for cleanup."""
        ...


@dataclass
class PluginManifest:
    """Metadata and lifecycle for a plugin.

    Plugin packages expose a module-level instance of this class
    via a Python entry point in the ``kt.plugins`` group.
    """

    id: str
    name: str
    version: str
    description: str = ""
    author: str = ""
    license: str = ""
    requires_license_key: bool = False
    lifecycle: PluginLifecycle | None = None
    settings_class: type[Any] | None = None
    migration_path: str | None = None
    dependencies: list[str] = field(default_factory=list)
