"""Hook registry — WordPress-inspired async hooks/filters for cross-cutting concerns."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

# Handler signature: async def handler(**kwargs) -> Any
HookHandler = Callable[..., Awaitable[Any]]


@dataclass(order=True)
class _HookRegistration:
    """Internal registration entry, ordered by priority."""

    priority: int
    plugin_id: str = field(compare=False)
    handler: HookHandler = field(compare=False)


class HookRegistry:
    """Central registry for named async hooks.

    Supports two invocation styles:

    - **trigger**: Fire all handlers, collecting results (action hooks).
    - **filter**: Chain handlers, each transforming a value (filter hooks).

    Priority ordering: lower priority values run first (default 100).
    """

    def __init__(self) -> None:
        self._handlers: dict[str, list[_HookRegistration]] = {}

    def register(
        self,
        hook_name: str,
        handler: HookHandler,
        *,
        priority: int = 100,
        plugin_id: str = "",
    ) -> None:
        """Register a handler for a named hook.

        Args:
            hook_name: The hook to subscribe to (e.g. ``"usage.record"``).
            handler: Async callable invoked when the hook fires.
            priority: Execution order; lower runs first. Default 100.
            plugin_id: Identifier of the registering plugin (for debugging).
        """
        reg = _HookRegistration(priority=priority, plugin_id=plugin_id, handler=handler)
        handlers = self._handlers.setdefault(hook_name, [])
        handlers.append(reg)
        handlers.sort()

    def unregister(self, hook_name: str, handler: HookHandler) -> bool:
        """Remove a previously registered handler. Returns True if found."""
        handlers = self._handlers.get(hook_name, [])
        for i, reg in enumerate(handlers):
            if reg.handler is handler:
                handlers.pop(i)
                return True
        return False

    async def trigger(self, hook_name: str, **kwargs: Any) -> list[Any]:
        """Fire all handlers for a hook, returning their results.

        If a handler raises an exception, it is logged and skipped.
        Remaining handlers still execute.
        """
        results: list[Any] = []
        for reg in self._handlers.get(hook_name, []):
            try:
                result = await reg.handler(**kwargs)
                results.append(result)
            except Exception:
                logger.exception(
                    "Hook handler failed: %s (plugin=%s, hook=%s)",
                    reg.handler,
                    reg.plugin_id,
                    hook_name,
                )
        return results

    async def filter(self, hook_name: str, value: Any, **kwargs: Any) -> Any:
        """Chain handlers as filters, each transforming the value.

        Each handler receives ``value`` as its first positional arg
        plus any extra ``kwargs``. The return value becomes the input
        to the next handler.
        """
        for reg in self._handlers.get(hook_name, []):
            try:
                value = await reg.handler(value, **kwargs)
            except Exception:
                logger.exception(
                    "Filter handler failed: %s (plugin=%s, hook=%s)",
                    reg.handler,
                    reg.plugin_id,
                    hook_name,
                )
        return value

    def has_handlers(self, hook_name: str) -> bool:
        """Check if any handlers are registered for a hook."""
        return bool(self._handlers.get(hook_name))

    def get_hook_names(self) -> list[str]:
        """Return all hook names that have at least one handler."""
        return [k for k, v in self._handlers.items() if v]
