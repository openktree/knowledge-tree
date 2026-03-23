"""Tests for token usage collection and merging helpers."""

from kt_hatchet.models import TokenUsageSummary
from kt_hatchet.usage_helpers import merge_usage


class TestMergeUsage:
    def test_merge_none_values(self) -> None:
        result = merge_usage(None, None)
        assert result.total_prompt_tokens == 0
        assert result.total_completion_tokens == 0
        assert result.total_cost_usd == 0.0
        assert result.by_model == {}
        assert result.by_task == {}

    def test_merge_single(self) -> None:
        s = TokenUsageSummary(
            total_prompt_tokens=100,
            total_completion_tokens=50,
            total_cost_usd=0.01,
            by_model={"model-a": {"prompt_tokens": 100, "completion_tokens": 50, "cost_usd": 0.01}},
        )
        result = merge_usage(s)
        assert result.total_prompt_tokens == 100
        assert result.total_completion_tokens == 50
        assert result.by_model["model-a"]["prompt_tokens"] == 100

    def test_merge_multiple(self) -> None:
        s1 = TokenUsageSummary(
            total_prompt_tokens=100,
            total_completion_tokens=50,
            total_cost_usd=0.01,
            by_model={"model-a": {"prompt_tokens": 100, "completion_tokens": 50, "cost_usd": 0.01}},
        )
        s2 = TokenUsageSummary(
            total_prompt_tokens=200,
            total_completion_tokens=100,
            total_cost_usd=0.03,
            by_model={
                "model-a": {"prompt_tokens": 150, "completion_tokens": 75, "cost_usd": 0.02},
                "model-b": {"prompt_tokens": 50, "completion_tokens": 25, "cost_usd": 0.01},
            },
        )
        result = merge_usage(s1, None, s2)
        assert result.total_prompt_tokens == 300
        assert result.total_completion_tokens == 150
        assert result.total_cost_usd == 0.04
        assert result.by_model["model-a"]["prompt_tokens"] == 250
        assert result.by_model["model-b"]["prompt_tokens"] == 50

    def test_merge_by_task(self) -> None:
        s1 = TokenUsageSummary(
            total_prompt_tokens=100,
            total_completion_tokens=50,
            total_cost_usd=0.01,
            by_model={"model-a": {"prompt_tokens": 100, "completion_tokens": 50, "cost_usd": 0.01}},
            by_task={"decomposition": {"prompt_tokens": 100, "completion_tokens": 50, "cost_usd": 0.01}},
        )
        s2 = TokenUsageSummary(
            total_prompt_tokens=200,
            total_completion_tokens=100,
            total_cost_usd=0.03,
            by_model={"model-a": {"prompt_tokens": 200, "completion_tokens": 100, "cost_usd": 0.03}},
            by_task={
                "decomposition": {"prompt_tokens": 150, "completion_tokens": 75, "cost_usd": 0.02},
                "entity_extraction": {"prompt_tokens": 50, "completion_tokens": 25, "cost_usd": 0.01},
            },
        )
        result = merge_usage(s1, s2)
        assert result.by_task["decomposition"]["prompt_tokens"] == 250
        assert result.by_task["entity_extraction"]["prompt_tokens"] == 50
