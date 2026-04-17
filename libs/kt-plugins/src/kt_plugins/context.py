"""Runtime context handed to a plugin during the bootstrap phase."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from kt_plugins.hooks import HookRegistry


@dataclass
class PluginContext:
    """Services made available to a plugin after register() completes.

    Populated by the runtime (API or worker) before calling
    ``lifecycle.bootstrap(ctx)``. Session factories are optional because
    a plugin may run inside a lightweight (non-DB) context.
    """

    plugin_id: str
    settings: Any
    hook_registry: "HookRegistry"
    session_factory: "async_sessionmaker[AsyncSession] | None" = None
    write_session_factory: "async_sessionmaker[AsyncSession] | None" = None
    # Cross-cutting services — set by the runtime, None if not wired yet
    model_gateway: Any = None
    embedding_service: Any = None
    provider_registry: Any = None
