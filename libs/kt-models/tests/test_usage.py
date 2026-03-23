"""Tests for the ContextVar-based LLM usage accumulator."""

import asyncio

import pytest

from kt_models.usage import (
    UsageAccumulator,
    clear_usage_task,
    get_current_accumulator,
    record_usage,
    set_usage_task,
    start_usage_tracking,
    stop_usage_tracking,
    usage_task,
)


class TestUsageAccumulator:
    def test_empty_accumulator(self) -> None:
        acc = UsageAccumulator()
        assert acc.total_prompt_tokens == 0
        assert acc.total_completion_tokens == 0
        assert acc.total_cost_usd == 0.0
        assert acc.by_model() == {}

    def test_record_and_aggregate(self) -> None:
        acc = UsageAccumulator()
        acc.record("model-a", 100, 50, 0.01)
        acc.record("model-a", 200, 100, 0.02)
        acc.record("model-b", 300, 150, 0.05)

        assert acc.total_prompt_tokens == 600
        assert acc.total_completion_tokens == 300
        assert acc.total_cost_usd == pytest.approx(0.08)
        assert len(acc.records) == 3

    def test_by_model(self) -> None:
        acc = UsageAccumulator()
        acc.record("model-a", 100, 50, 0.01)
        acc.record("model-a", 200, 100, 0.02)
        acc.record("model-b", 300, 150, 0.05)

        by_model = acc.by_model()
        assert len(by_model) == 2
        assert by_model["model-a"]["prompt_tokens"] == 300
        assert by_model["model-a"]["completion_tokens"] == 150
        assert by_model["model-a"]["cost_usd"] == pytest.approx(0.03)
        assert by_model["model-b"]["prompt_tokens"] == 300

    def test_by_task(self) -> None:
        acc = UsageAccumulator()
        acc.record("model-a", 100, 50, 0.01, task_label="decomposition")
        acc.record("model-a", 200, 100, 0.02, task_label="decomposition")
        acc.record("model-b", 300, 150, 0.05, task_label="entity_extraction")
        acc.record("model-a", 50, 25, 0.005)  # no task label → "other"

        by_task = acc.by_task()
        assert len(by_task) == 3
        assert by_task["decomposition"]["prompt_tokens"] == 300
        assert by_task["decomposition"]["completion_tokens"] == 150
        assert by_task["entity_extraction"]["prompt_tokens"] == 300
        assert by_task["other"]["prompt_tokens"] == 50

    def test_to_dict(self) -> None:
        acc = UsageAccumulator()
        acc.record("model-a", 100, 50, 0.01)
        d = acc.to_dict()
        assert d["total_prompt_tokens"] == 100
        assert d["total_completion_tokens"] == 50
        assert "by_model" in d
        assert "by_task" in d


class TestContextVarTracking:
    @pytest.mark.asyncio
    async def test_start_stop(self) -> None:
        acc = start_usage_tracking()
        assert get_current_accumulator() is acc
        record_usage("model-x", 10, 5, 0.001)
        result = stop_usage_tracking()
        assert result is acc
        assert result.total_prompt_tokens == 10
        assert get_current_accumulator() is None

    @pytest.mark.asyncio
    async def test_no_tracking_noop(self) -> None:
        assert get_current_accumulator() is None
        record_usage("model-x", 10, 5, 0.001)  # should not raise
        assert stop_usage_tracking() is None

    @pytest.mark.asyncio
    async def test_isolation_across_tasks(self) -> None:
        """Each async task should get independent tracking."""
        results: list[int] = []

        async def task_a() -> None:
            start_usage_tracking()
            record_usage("a", 100, 0)
            await asyncio.sleep(0.01)
            acc = stop_usage_tracking()
            results.append(acc.total_prompt_tokens if acc else 0)

        async def task_b() -> None:
            start_usage_tracking()
            record_usage("b", 200, 0)
            await asyncio.sleep(0.01)
            acc = stop_usage_tracking()
            results.append(acc.total_prompt_tokens if acc else 0)

        await asyncio.gather(task_a(), task_b())
        assert sorted(results) == [100, 200]

    @pytest.mark.asyncio
    async def test_task_label_tracking(self) -> None:
        """Task labels are auto-attached to records via ContextVar."""
        start_usage_tracking()
        set_usage_task("decomposition")
        record_usage("model-a", 100, 50, 0.01)
        record_usage("model-a", 200, 100, 0.02)
        clear_usage_task()

        set_usage_task("entity_extraction")
        record_usage("model-b", 300, 150, 0.05)
        clear_usage_task()

        # Unlabeled call
        record_usage("model-a", 50, 25, 0.005)

        acc = stop_usage_tracking()
        assert acc is not None
        by_task = acc.by_task()
        assert by_task["decomposition"]["prompt_tokens"] == 300
        assert by_task["entity_extraction"]["prompt_tokens"] == 300
        assert by_task["other"]["prompt_tokens"] == 50

    @pytest.mark.asyncio
    async def test_usage_task_context_manager(self) -> None:
        """usage_task() context manager sets and clears the task label."""
        start_usage_tracking()
        with usage_task("dimensions"):
            record_usage("model-a", 100, 50, 0.01)
        # After exiting context manager, label should be cleared
        record_usage("model-a", 50, 25, 0.005)
        acc = stop_usage_tracking()
        assert acc is not None
        by_task = acc.by_task()
        assert by_task["dimensions"]["prompt_tokens"] == 100
        assert by_task["other"]["prompt_tokens"] == 50
