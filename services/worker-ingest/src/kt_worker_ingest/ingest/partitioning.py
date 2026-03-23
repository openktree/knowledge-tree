"""Partitioning logic for parallel ingest agents.

Splits a ContentIndex into partitions when the document exceeds
TOKEN_THRESHOLD. Each partition gets a proportional share of the
nav_budget based on its fact count.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from kt_worker_ingest.ingest.content_index import ContentIndex, IndexEntry

# Documents above this token count get split into parallel agents
TOKEN_THRESHOLD = 100_000


@dataclass
class IngestPartition:
    """A slice of the content index assigned to one ingest agent."""

    partition_id: str
    index_range: tuple[int, int]  # (start_idx, end_idx exclusive)
    entries: list[IndexEntry]
    nav_budget: int
    total_facts_in_partition: int


def partition_for_parallel(
    index: ContentIndex,
    total_nav_budget: int,
    threshold: int = TOKEN_THRESHOLD,
) -> list[IngestPartition]:
    """Split a content index into partitions for parallel ingest agents.

    Below threshold: returns a single partition (no split).
    Above: splits into ceil(total_tokens / threshold) partitions.
    Budget is distributed proportionally by fact_count per partition.

    Args:
        index: The content index to partition.
        total_nav_budget: Total nav budget to distribute.
        threshold: Token threshold for splitting (default 100K).

    Returns:
        List of IngestPartition, each with its own budget slice.
    """
    if not index.entries:
        return [
            IngestPartition(
                partition_id=str(uuid.uuid4()),
                index_range=(0, 0),
                entries=[],
                nav_budget=total_nav_budget,
                total_facts_in_partition=0,
            )
        ]

    total_tokens = index.total_tokens_approx

    if total_tokens <= threshold:
        return [
            IngestPartition(
                partition_id=str(uuid.uuid4()),
                index_range=(0, len(index.entries)),
                entries=list(index.entries),
                nav_budget=total_nav_budget,
                total_facts_in_partition=index.total_facts,
            )
        ]

    # Calculate target partition count
    n_partitions = max(2, total_tokens // threshold)
    n_partitions = min(n_partitions, len(index.entries))  # Can't have more partitions than entries

    # Split entries into roughly equal groups
    entries_per = len(index.entries) // n_partitions
    partitions: list[IngestPartition] = []

    total_facts = max(1, index.total_facts)
    budget_assigned = 0

    for i in range(n_partitions):
        start = i * entries_per
        end = (i + 1) * entries_per if i < n_partitions - 1 else len(index.entries)
        partition_entries = index.entries[start:end]
        partition_facts = sum(e.fact_count for e in partition_entries)

        # Budget proportional to facts (min 5 per partition)
        if i < n_partitions - 1:
            fact_ratio = partition_facts / total_facts
            partition_budget = max(5, int(total_nav_budget * fact_ratio))
        else:
            # Last partition gets whatever remains
            partition_budget = max(5, total_nav_budget - budget_assigned)

        budget_assigned += partition_budget

        partitions.append(
            IngestPartition(
                partition_id=str(uuid.uuid4()),
                index_range=(start, end),
                entries=partition_entries,
                nav_budget=partition_budget,
                total_facts_in_partition=partition_facts,
            )
        )

    return partitions
