"""Fact insert path (post-job dedup design).

The old :func:`deduplicate_facts` function embedded each item, called
Qdrant ``find_most_similar`` inline, and conditionally upserted new
vectors — which created both an intra-batch race (multiple identical
items in the same batch all missing the Qdrant search) and a cross-call
race (two parallel pipelines both missing before either flushed).

Under the new design dedup is a **post-job workflow stage** — the
``dedup_pending_facts_wf`` Hatchet workflow runs between scope
extraction and autograph, snapshots the just-inserted facts, clusters
them by embedding similarity, and calls
:func:`kt_facts.processing.merge.merge_into_fast` for each loser.

Consequently the insert path has no dedup responsibility at all — it
just upserts each ``(content, fact_type)`` item into ``write_facts``
with ``dedup_status='pending'`` and returns the UUIDs in input order.
No Qdrant calls, no embeddings, no races.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from kt_config.types import COMPOUND_FACT_TYPES

if TYPE_CHECKING:
    from kt_db.repositories.write_facts import WriteFactRepository

logger = logging.getLogger(__name__)


# ── Thresholds (still used by the dedup workflow) ─────────────────────

_ATOMIC_THRESHOLD = 0.92
_COMPOUND_THRESHOLD = 0.85


def _threshold_for_type(fact_type: str) -> float:
    """Return the cosine-similarity threshold for a given fact type.

    Compound types (quote, procedure, reference, code, account) use a
    lower threshold (0.85) because longer content has more natural
    variance. Atomic types use 0.92.
    """
    return _COMPOUND_THRESHOLD if fact_type in COMPOUND_FACT_TYPES else _ATOMIC_THRESHOLD


# ── Result container ─────────────────────────────────────────────────


@dataclass
class InsertFactsPendingResult:
    """Result of :func:`insert_facts_pending`.

    ``fact_ids`` lists the freshly-upserted fact UUIDs in input order.
    """

    fact_ids: list[uuid.UUID] = field(default_factory=list)


# ── Insert path ──────────────────────────────────────────────────────


async def insert_facts_pending(
    items: list[tuple[str, str]],
    write_fact_repo: "WriteFactRepository | None",
) -> InsertFactsPendingResult:
    """Insert raw facts with ``dedup_status='pending'`` and return their IDs.

    Each entry in ``items`` is ``(content, fact_type)``. A fresh UUID
    is generated per item and upserted into ``write_facts``; the dedup
    worker will later take ownership via the snapshot step and
    potentially collapse losers into canonical survivors.

    Returns an :class:`InsertFactsPendingResult` whose ``fact_ids``
    mirrors the input order.

    Raises:
        RuntimeError: If ``write_fact_repo`` is ``None``. All worker
            pipelines must pass a write-db session.
    """
    if not items:
        return InsertFactsPendingResult()

    if write_fact_repo is None:
        raise RuntimeError(
            "insert_facts_pending: write_fact_repo is required but was None. "
            "All worker pipelines must pass a write-db session to GraphEngine."
        )

    fact_ids: list[uuid.UUID] = []
    for content, fact_type in items:
        new_id = uuid.uuid4()
        await write_fact_repo.upsert(
            fact_id=new_id,
            content=content,
            fact_type=fact_type,
        )
        fact_ids.append(new_id)

    return InsertFactsPendingResult(fact_ids=fact_ids)
