"""Helpers to collect and merge token usage from ContextVar accumulator.

Used by Hatchet tasks to capture per-task usage and by parent workflows
to merge child task usage into a single summary.
"""

from __future__ import annotations

import logging
import uuid

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from kt_hatchet.models import TokenUsageSummary

logger = logging.getLogger(__name__)


def collect_task_usage() -> TokenUsageSummary | None:
    """Read and clear the ContextVar accumulator, returning a summary.

    Returns None if tracking was not started or no usage was recorded.
    """
    from kt_models.usage import stop_usage_tracking

    acc = stop_usage_tracking()
    if acc is None or not acc.records:
        return None

    return TokenUsageSummary(
        total_prompt_tokens=acc.total_prompt_tokens,
        total_completion_tokens=acc.total_completion_tokens,
        total_cost_usd=acc.total_cost_usd,
        by_model={
            model: {
                "prompt_tokens": int(data["prompt_tokens"]),
                "completion_tokens": int(data["completion_tokens"]),
                "cost_usd": float(data["cost_usd"]),
            }
            for model, data in acc.by_model().items()
        },
        by_task={
            task: {
                "prompt_tokens": int(data["prompt_tokens"]),
                "completion_tokens": int(data["completion_tokens"]),
                "cost_usd": float(data["cost_usd"]),
            }
            for task, data in acc.by_task().items()
        },
    )


async def flush_usage_to_db(
    write_session_factory: async_sessionmaker[AsyncSession],
    conversation_id: str,
    message_id: str,
    task_type: str,
    workflow_run_id: str | None = None,
) -> None:
    """Stop the ContextVar accumulator and write one row per model to write-db.

    Opens its own session, commits, and closes — independent of the task's
    business transaction so that usage data persists even if the business
    transaction rolls back.

    No-op if the accumulator is empty.
    """
    from kt_models.usage import stop_usage_tracking

    acc = stop_usage_tracking()
    if acc is None or not acc.records:
        return

    by_model = acc.by_model()
    if not by_model:
        return

    from kt_db.repositories.write_llm_usage import WriteLlmUsageRepository
    from kt_db.write_models import WriteLlmUsage

    records = [
        WriteLlmUsage(
            id=uuid.uuid4(),
            conversation_id=conversation_id,
            message_id=message_id,
            task_type=task_type,
            workflow_run_id=workflow_run_id,
            model_id=model_id,
            prompt_tokens=int(data.get("prompt_tokens", 0)),
            completion_tokens=int(data.get("completion_tokens", 0)),
            cost_usd=float(data.get("cost_usd", 0.0)),
        )
        for model_id, data in by_model.items()
    ]

    try:
        async with write_session_factory() as session:
            repo = WriteLlmUsageRepository(session)
            await repo.bulk_insert(records)
            await session.commit()
    except Exception:
        logger.error("Failed to flush usage to DB for task_type=%s", task_type, exc_info=True)


def merge_usage(*summaries: TokenUsageSummary | None) -> TokenUsageSummary:
    """Merge multiple usage summaries into one.

    .. deprecated::
        Use ``flush_usage_to_db`` instead. Each task self-reports usage
        to the flat ``write_llm_usage`` table; aggregation happens at
        query time via SQL.
    """
    merged_by_model: dict[str, dict[str, int | float]] = {}
    merged_by_task: dict[str, dict[str, int | float]] = {}
    total_prompt = 0
    total_completion = 0
    total_cost = 0.0

    for s in summaries:
        if s is None:
            continue
        total_prompt += s.total_prompt_tokens
        total_completion += s.total_completion_tokens
        total_cost += s.total_cost_usd
        for model, data in s.by_model.items():
            if model not in merged_by_model:
                merged_by_model[model] = {
                    "prompt_tokens": 0,
                    "completion_tokens": 0,
                    "cost_usd": 0.0,
                }
            merged_by_model[model]["prompt_tokens"] += data["prompt_tokens"]
            merged_by_model[model]["completion_tokens"] += data["completion_tokens"]
            merged_by_model[model]["cost_usd"] += data["cost_usd"]
        for task, data in s.by_task.items():
            if task not in merged_by_task:
                merged_by_task[task] = {
                    "prompt_tokens": 0,
                    "completion_tokens": 0,
                    "cost_usd": 0.0,
                }
            merged_by_task[task]["prompt_tokens"] += data["prompt_tokens"]
            merged_by_task[task]["completion_tokens"] += data["completion_tokens"]
            merged_by_task[task]["cost_usd"] += data["cost_usd"]

    return TokenUsageSummary(
        total_prompt_tokens=total_prompt,
        total_completion_tokens=total_completion,
        total_cost_usd=total_cost,
        by_model=merged_by_model,
        by_task=merged_by_task,
    )
