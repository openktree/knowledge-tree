"""Plugin manifest + lifecycle protocol.

The manifest is the modern contribution API. The legacy
``BackendEnginePlugin`` ABC is kept around in :mod:`kt_plugins.legacy`
for existing in-tree plugins, and is wrapped into a ``PluginManifest``
during discovery so both styles share a single orchestration path.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from kt_plugins.context import PluginContext
    from kt_plugins.extension_points import ExtensionRegistry


@runtime_checkable
class PluginLifecycle(Protocol):
    """Protocol plugin lifecycle implementations must satisfy.

    Plugins declare extensions during ``register()`` (no runtime
    services available) and subscribe to hooks / finish setup during
    ``bootstrap(ctx)`` (full ``PluginContext`` available).
    """

    async def register(self, registry: "ExtensionRegistry") -> None: ...

    async def bootstrap(self, ctx: "PluginContext") -> None: ...

    async def shutdown(self) -> None: ...


@dataclass
class PluginManifest:
    """Declarative description of a plugin.

    Discovered via ``importlib.metadata`` entry points in group
    ``kt.plugins``. The entry point must resolve to a
    ``PluginManifest`` instance (preferred) or to a
    ``BackendEnginePlugin`` subclass (legacy — auto-wrapped).
    """

    id: str
    name: str = ""
    version: str = "0.0.0"
    description: str = ""
    author: str = ""
    license: str = "unknown"
    requires_license_key: bool = False
    lifecycle: PluginLifecycle | None = None
    settings_class: Any = None
    dependencies: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.name:
            self.name = self.id
