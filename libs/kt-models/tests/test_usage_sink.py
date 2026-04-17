"""Tests for the async UsageSink.

The sink is the gateway's one-way street to write-db. Here we verify the
queue, batching, and the no-op-when-uninstalled behaviour. DB-touching
integration tests live elsewhere.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from kt_models.expense import ExpenseContext
from kt_models.usage_sink import UsageSink, record_llm_usage


@pytest.fixture(autouse=True)
async def _clean_sink() -> Any:
    await UsageSink.shutdown()
    yield
    await UsageSink.shutdown()


def _make_session_factory() -> tuple[MagicMock, MagicMock]:
    """Build a mock async_sessionmaker that records bulk_insert calls."""
    session = MagicMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=None)
    session.commit = AsyncMock()
    session.close = AsyncMock()
    session.execute = AsyncMock()
    session.add = MagicMock()

    factory = MagicMock(return_value=session)
    return factory, session


async def test_record_no_op_when_uninstalled() -> None:
    # Must not raise even though sink is not installed.
    record_llm_usage(
        model_id="m",
        prompt_tokens=100,
        completion_tokens=20,
        cost_usd=0.01,
        expense=ExpenseContext(task_type="x"),
    )


async def test_install_is_idempotent() -> None:
    factory, _ = _make_session_factory()
    sink1 = UsageSink.install(factory)
    sink2 = UsageSink.install(factory)
    assert sink1 is sink2


async def test_install_rejects_conflicting_factory() -> None:
    factory_a, _ = _make_session_factory()
    factory_b, _ = _make_session_factory()
    UsageSink.install(factory_a)
    with pytest.raises(RuntimeError, match="different session_factory"):
        UsageSink.install(factory_b)


async def test_record_skips_zero_token_rows() -> None:
    factory, _ = _make_session_factory()
    UsageSink.install(factory, flush_interval_s=0.05)
    record_llm_usage(
        model_id="m",
        prompt_tokens=0,
        completion_tokens=0,
        cost_usd=0.0,
        expense=ExpenseContext(task_type="x"),
    )
    await asyncio.sleep(0.1)
    factory.assert_not_called()


async def test_records_flow_through_batch(monkeypatch: pytest.MonkeyPatch) -> None:
    factory, _ = _make_session_factory()

    # Stub repository + model so the sink doesn't need real kt-db.
    inserted: list[list[Any]] = []

    class _StubRepo:
        def __init__(self, session: Any) -> None:
            self._session = session

        async def bulk_insert(self, records: list[Any]) -> None:
            inserted.append(list(records))

    import kt_db.repositories.write_llm_usage as repo_mod

    monkeypatch.setattr(repo_mod, "WriteLlmUsageRepository", _StubRepo)

    UsageSink.install(factory, batch_size=2, flush_interval_s=0.05)
    for i in range(3):
        record_llm_usage(
            model_id=f"model-{i}",
            prompt_tokens=10,
            completion_tokens=5,
            cost_usd=0.001,
            expense=ExpenseContext(task_type="test", conversation_id=f"c{i}"),
        )

    await UsageSink.shutdown()

    flat = [r for batch in inserted for r in batch]
    assert len(flat) == 3
    assert {r.model_id for r in flat} == {"model-0", "model-1", "model-2"}
    assert all(r.task_type == "test" for r in flat)
