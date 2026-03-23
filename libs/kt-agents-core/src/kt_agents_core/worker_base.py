"""BaseWorker -- shared patterns for agent workers."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from kt_agents_core.base import BaseAgent
from kt_agents_core.state import AgentContext
from kt_config.settings import get_settings

logger = logging.getLogger(__name__)


class BaseWorker:
    """Base class for agent workers.

    Extracts common init, event emission, and compile-and-invoke patterns
    shared by OrchestratorWorker, QueryWorker, and IngestWorker.
    """

    def __init__(self, ctx: AgentContext) -> None:
        self.ctx = ctx

    async def _emit_start(
        self,
        action: str,
        tool: str,
        nav_budget: int,
        explore_budget: int = 0,
    ) -> None:
        """Emit initial activity_log and budget_update events."""
        await self.ctx.emit("activity_log", action=action, tool=tool)
        await self.ctx.emit(
            "budget_update",
            data={
                "nav_remaining": nav_budget,
                "nav_total": nav_budget,
                "explore_remaining": explore_budget,
                "explore_total": explore_budget,
            },
        )

    async def _compile_and_invoke(
        self,
        agent: BaseAgent[Any],
        state: Any,
        recursion_limit: int,
        inactivity_timeout: int | None = None,
    ) -> Any:
        """Build graph, compile, and invoke. Returns raw final state.

        A watchdog task monitors ``ctx.last_activity_at`` (updated on
        every ``emit()`` call, including from sub-explorers).  If no
        activity is observed for *inactivity_timeout* seconds the
        invoke task is cancelled and a ``TimeoutError`` is raised.

        Does NOT handle other exceptions -- callers wrap in try/except
        with their own fallback logic.
        """
        if inactivity_timeout is None:
            inactivity_timeout = get_settings().agent_inactivity_timeout_seconds

        graph, _tools = agent.build_graph()
        compiled = graph.compile()

        # Reset activity clock before starting
        self.ctx.last_activity_at = time.monotonic()
        stalled = False

        async def _watchdog(task: asyncio.Task[Any]) -> None:
            nonlocal stalled
            poll_interval = 10.0
            while not task.done():
                await asyncio.sleep(poll_interval)
                if task.done():
                    break
                elapsed = time.monotonic() - self.ctx.last_activity_at
                if elapsed > inactivity_timeout:
                    stalled = True
                    logger.warning(
                        "Agent stalled -- no activity for %ds, cancelling",
                        inactivity_timeout,
                    )
                    task.cancel()
                    return

        invoke_task = asyncio.create_task(
            compiled.ainvoke(state, config={"recursion_limit": recursion_limit}),
        )
        watchdog_task = asyncio.create_task(_watchdog(invoke_task))

        try:
            return await invoke_task
        except asyncio.CancelledError:
            if stalled:
                raise TimeoutError(
                    f"Agent stalled -- no activity for {inactivity_timeout}s"
                ) from None
            raise
        finally:
            watchdog_task.cancel()
