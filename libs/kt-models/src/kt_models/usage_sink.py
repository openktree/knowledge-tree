"""Process-wide async sink that persists LLM usage rows to write-db.

The gateway's recorder calls :func:`record_llm_usage` once per LLM
call with the :class:`ExpenseContext` read from the ContextVar; a
background drain batches the rows into ``write_llm_usage``. Lifecycle
is owned by the worker / API lifespan (``UsageSink.install`` at
startup, ``UsageSink.shutdown`` at exit).

When no sink is installed (tests that don't touch the DB, ad-hoc
scripts) the recorder is a no-op after a single DEBUG log. The
fail-fast check for a missing context lives upstream in
:func:`kt_models.expense.require_current_expense`, so a dropped row
never means a mistagged one.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from kt_models.expense import ExpenseContext

logger = logging.getLogger(__name__)

_DEFAULT_BATCH_SIZE = 50
_DEFAULT_FLUSH_INTERVAL_S = 0.5
_QUEUE_MAXSIZE = 10_000


@dataclass(frozen=True, slots=True)
class _PendingRow:
    model_id: str
    prompt_tokens: int
    completion_tokens: int
    cost_usd: float
    expense: ExpenseContext


class UsageSink:
    """Process-wide LLM usage writer.

    Singleton per process — installed during lifespan startup.
    """

    _instance: "UsageSink | None" = None

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        batch_size: int = _DEFAULT_BATCH_SIZE,
        flush_interval_s: float = _DEFAULT_FLUSH_INTERVAL_S,
    ) -> None:
        self._session_factory = session_factory
        self._batch_size = batch_size
        self._flush_interval_s = flush_interval_s
        self._queue: asyncio.Queue[_PendingRow] = asyncio.Queue(maxsize=_QUEUE_MAXSIZE)
        self._task: asyncio.Task[None] | None = None
        self._stopping = asyncio.Event()

    @classmethod
    def current(cls) -> "UsageSink | None":
        return cls._instance

    @classmethod
    def install(
        cls,
        session_factory: async_sessionmaker[AsyncSession],
        **kwargs: float | int,
    ) -> "UsageSink":
        if cls._instance is not None:
            if cls._instance._session_factory is not session_factory:
                raise RuntimeError(
                    "UsageSink already installed with a different session_factory. "
                    "Call UsageSink.shutdown() first before re-installing — "
                    "reusing the stale instance would write usage rows to the "
                    "previous DB, not the new one."
                )
            logger.debug("UsageSink already installed; reusing existing instance")
            return cls._instance
        inst = cls(session_factory, **kwargs)  # type: ignore[arg-type]
        inst._start()
        cls._instance = inst
        return inst

    @classmethod
    async def shutdown(cls) -> None:
        inst = cls._instance
        if inst is None:
            return
        await inst._stop()
        cls._instance = None

    def _start(self) -> None:
        if self._task is not None:
            return
        self._task = asyncio.create_task(self._drain_loop(), name="kt-usage-sink-drain")

    async def _stop(self) -> None:
        self._stopping.set()
        if self._task is not None:
            try:
                await asyncio.wait_for(self._task, timeout=5.0)
            except asyncio.TimeoutError:
                logger.warning("UsageSink drain task did not finish within 5s; cancelling")
                self._task.cancel()
                try:
                    await self._task
                except (asyncio.CancelledError, Exception):  # noqa: BLE001
                    pass
            self._task = None

    def record(
        self,
        *,
        model_id: str,
        prompt_tokens: int,
        completion_tokens: int,
        cost_usd: float,
        expense: ExpenseContext,
    ) -> None:
        """Non-blocking enqueue of a usage row."""
        if prompt_tokens == 0 and completion_tokens == 0:
            return
        row = _PendingRow(
            model_id=model_id,
            prompt_tokens=int(prompt_tokens),
            completion_tokens=int(completion_tokens),
            cost_usd=float(cost_usd),
            expense=expense,
        )
        try:
            self._queue.put_nowait(row)
        except asyncio.QueueFull:
            logger.warning(
                "UsageSink queue full (size=%d); dropping row for model=%s task_type=%s",
                _QUEUE_MAXSIZE,
                row.model_id,
                expense.task_type,
            )

    async def _drain_loop(self) -> None:
        while not self._stopping.is_set():
            batch = await self._collect_batch()
            if batch:
                await self._flush(batch)

        remaining: list[_PendingRow] = []
        while not self._queue.empty():
            try:
                remaining.append(self._queue.get_nowait())
            except asyncio.QueueEmpty:
                break
        if remaining:
            await self._flush(remaining)

    async def _collect_batch(self) -> list[_PendingRow]:
        batch: list[_PendingRow] = []
        try:
            first = await asyncio.wait_for(self._queue.get(), timeout=self._flush_interval_s)
            batch.append(first)
        except asyncio.TimeoutError:
            return batch
        while len(batch) < self._batch_size:
            try:
                batch.append(self._queue.get_nowait())
            except asyncio.QueueEmpty:
                break
        return batch

    async def _flush(self, batch: list[_PendingRow]) -> None:
        from kt_db.repositories.write_llm_usage import WriteLlmUsageRepository
        from kt_db.write_models import WriteLlmUsage

        records = [
            WriteLlmUsage(
                id=uuid.uuid4(),
                conversation_id=row.expense.conversation_id or "",
                message_id=row.expense.message_id or "",
                task_type=row.expense.task_type,
                workflow_run_id=row.expense.workflow_run_id,
                model_id=row.model_id,
                prompt_tokens=row.prompt_tokens,
                completion_tokens=row.completion_tokens,
                cost_usd=row.cost_usd,
            )
            for row in batch
        ]
        try:
            async with self._session_factory() as session:
                repo = WriteLlmUsageRepository(session)
                await repo.bulk_insert(records)
                await session.commit()
        except Exception:
            logger.exception("UsageSink flush failed (%d rows dropped)", len(records))


def record_llm_usage(
    *,
    model_id: str,
    prompt_tokens: int,
    completion_tokens: int,
    cost_usd: float,
    expense: ExpenseContext,
) -> None:
    """Gateway entry point — enqueue a usage row if the sink is installed.

    Missing context is rejected upstream (``require_current_expense``);
    here we only handle the "no sink installed" case, which happens in
    tests / CLI tools that never start a worker lifespan.
    """
    sink = UsageSink.current()
    if sink is None:
        logger.debug(
            "UsageSink not installed; dropping usage for model=%s task_type=%s prompt=%d completion=%d",
            model_id,
            expense.task_type,
            prompt_tokens,
            completion_tokens,
        )
        return
    sink.record(
        model_id=model_id,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        cost_usd=cost_usd,
        expense=expense,
    )
