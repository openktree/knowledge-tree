"""Async hook registry — trigger (action) + filter (value chain) hooks.

Priority-ordered. Lower priority runs first (default 100). Handlers that
raise are logged and skipped — one bad handler must not take down the rest
of the chain.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable
from dataclasses import dataclass, field
from typing import Any, Callable

logger = logging.getLogger(__name__)


HookHandler = Callable[..., Awaitable[Any]]


@dataclass(order=True)
class _Registration:
    priority: int
    plugin_id: str = field(compare=False)
    handler: HookHandler = field(compare=False)


class HookRegistry:
    """Central registry for named async hooks.

    Two invocation styles:
      - ``trigger``: fire every handler, collect results (fire-and-forget
        semantics; results list returned for callers that want them).
      - ``filter``: chain handlers; each transforms the value.

    ``fire_and_forget`` schedules handlers on the running event loop and
    returns immediately — used for hot-path hooks (e.g.
    ``auth.permission_check``) that must never block core evaluation.
    """

    def __init__(self) -> None:
        self._handlers: dict[str, list[_Registration]] = {}

    def register(
        self,
        hook_name: str,
        handler: HookHandler,
        *,
        priority: int = 100,
        plugin_id: str = "",
    ) -> None:
        reg = _Registration(priority=priority, plugin_id=plugin_id, handler=handler)
        handlers = self._handlers.setdefault(hook_name, [])
        handlers.append(reg)
        handlers.sort()

    def unregister(self, hook_name: str, handler: HookHandler) -> bool:
        handlers = self._handlers.get(hook_name, [])
        for i, reg in enumerate(handlers):
            if reg.handler is handler:
                handlers.pop(i)
                return True
        return False

    def clear(self, hook_name: str | None = None) -> None:
        if hook_name is None:
            self._handlers.clear()
        else:
            self._handlers.pop(hook_name, None)

    async def trigger(self, hook_name: str, **kwargs: Any) -> list[Any]:
        """Fire all handlers, collect results. Exceptions logged & swallowed."""
        results: list[Any] = []
        for reg in list(self._handlers.get(hook_name, [])):
            try:
                result = await reg.handler(**kwargs)
                results.append(result)
            except Exception:
                logger.exception(
                    "hook trigger handler failed: hook=%s plugin=%s handler=%s",
                    hook_name,
                    reg.plugin_id,
                    reg.handler,
                )
        return results

    async def filter(self, hook_name: str, value: Any, **kwargs: Any) -> Any:
        """Chain handlers; each receives the current value plus kwargs."""
        for reg in list(self._handlers.get(hook_name, [])):
            try:
                value = await reg.handler(value, **kwargs)
            except Exception:
                logger.exception(
                    "hook filter handler failed: hook=%s plugin=%s handler=%s",
                    hook_name,
                    reg.plugin_id,
                    reg.handler,
                )
        return value

    def fire_and_forget(self, hook_name: str, **kwargs: Any) -> None:
        """Schedule trigger without awaiting it.

        Safe to call from non-async code paths — drops silently when no
        event loop is running. Used for hot-path audit hooks that must
        not block the caller (e.g. permission checks).
        """
        handlers = self._handlers.get(hook_name)
        if not handlers:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        loop.create_task(self.trigger(hook_name, **kwargs))

    def has_handlers(self, hook_name: str) -> bool:
        return bool(self._handlers.get(hook_name))

    def get_hook_names(self) -> list[str]:
        return [k for k, v in self._handlers.items() if v]
