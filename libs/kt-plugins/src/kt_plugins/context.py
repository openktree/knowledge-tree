"""Plugin context — scoped services provided to plugins during bootstrap."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from kt_plugins.hooks import HookRegistry


@dataclass
class PluginContext:
    """Services available to a plugin during the bootstrap phase.

    Each plugin receives its own ``PluginContext`` with access to
    shared infrastructure. Plugins should NOT store references to
    the session factories beyond bootstrap — use them to create
    sessions as needed at runtime.
    """

    plugin_id: str
    settings: Any  # Plugin's own resolved Pydantic settings, or None
    hook_registry: HookRegistry
    session_factory: async_sessionmaker[AsyncSession] | None = None
    write_session_factory: async_sessionmaker[AsyncSession] | None = None
