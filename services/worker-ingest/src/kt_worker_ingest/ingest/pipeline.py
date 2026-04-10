"""Ingest pipeline — worker-specific decomposition and reconstruction.

The prepare-layer functions (process_ingest_sources, build_chunk_list,
review_chunks) and their data models live in ``kt_providers.ingest`` and
are re-exported here for backwards compatibility.  This module adds the
heavier decomposition/extraction layer that depends on agent context.
"""

from __future__ import annotations

import logging
import uuid
from collections import Counter
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

# ── Re-exports from kt_providers.ingest ────────────────────────────
# Kept so that existing ``from kt_worker_ingest.ingest.pipeline import …``
# continues to work throughout the worker codebase.
from kt_providers.ingest.pipeline import (  # noqa: F401 — re-export
    ChunkInfo,
    ChunkSelection,
    ProcessedSource,
    _lookup_raw_source,
    build_chunk_list,
    process_ingest_sources,
    review_chunks,
)

if TYPE_CHECKING:
    from kt_agents_core.state import AgentContext, EventCallback

logger = logging.getLogger(__name__)


@dataclass
class DecompositionSummary:
    """Summary of upfront decomposition of all sources."""

    total_facts: int
    fact_type_counts: dict[str, int] = field(default_factory=dict)
    source_summaries: list[dict[str, Any]] = field(default_factory=list)
    key_topics: list[str] = field(default_factory=list)
    total_chunks_processed: int = 0
    total_sources: int = 0
    inserted_fact_ids: list[str] = field(default_factory=list)
    """UUIDs (as str) of facts inserted during decomposition.

    Forwarded to ``dedup_pending_facts_wf`` by the ingest decompose
    workflow so that the dedup workflow can collapse duplicates before
    ``ingest_build_wf`` consumes them.
    """


async def reconstruct_decomp_summary(
    conversation_id: uuid.UUID,
    session: AsyncSession,
) -> DecompositionSummary:
    """Reconstruct a DecompositionSummary from already-stored facts.

    Used for ingest expansion: the facts are already in the DB from the
    initial decomposition, so we just query the counts rather than
    re-running extraction.
    """
    from kt_db.models import Fact, FactSource, IngestSource

    # Get raw_source_ids linked to this conversation's ingest sources
    src_result = await session.execute(
        select(IngestSource.raw_source_id).where(
            IngestSource.conversation_id == conversation_id,
            IngestSource.raw_source_id.isnot(None),
        )
    )
    raw_source_ids = [row[0] for row in src_result.all()]

    if not raw_source_ids:
        return DecompositionSummary(total_facts=0)

    # Get all facts linked to those raw sources
    fact_result = await session.execute(
        select(Fact.id, Fact.fact_type, Fact.content, FactSource.raw_source_id)
        .join(FactSource, Fact.id == FactSource.fact_id)
        .where(FactSource.raw_source_id.in_(raw_source_ids))
    )
    rows = fact_result.all()

    if not rows:
        return DecompositionSummary(total_facts=0, total_sources=len(raw_source_ids))

    # Count by fact type
    type_counts: dict[str, int] = {}
    per_source: dict[uuid.UUID, int] = {}
    fact_ids: set[uuid.UUID] = set()
    contents: list[str] = []

    for fact_id, fact_type, content, rs_id in rows:
        if fact_id not in fact_ids:
            fact_ids.add(fact_id)
            type_counts[fact_type] = type_counts.get(fact_type, 0) + 1
            contents.append(content)
        per_source[rs_id] = per_source.get(rs_id, 0) + 1

    # Build per-source summaries
    source_summaries: list[dict[str, Any]] = []
    name_result = await session.execute(
        select(IngestSource.original_name, IngestSource.raw_source_id).where(
            IngestSource.conversation_id == conversation_id,
            IngestSource.raw_source_id.isnot(None),
        )
    )
    for name, rs_id in name_result.all():
        source_summaries.append(
            {
                "name": name,
                "fact_count": per_source.get(rs_id, 0),
            }
        )

    # Extract key topics (reuse existing helper)
    # Build minimal fact-like objects for _extract_key_topics
    class _FakeFact:
        def __init__(self, c: str) -> None:
            self.content = c

    key_topics = _extract_key_topics([_FakeFact(c) for c in contents])

    return DecompositionSummary(
        total_facts=len(fact_ids),
        fact_type_counts=type_counts,
        source_summaries=source_summaries,
        key_topics=key_topics[:20],
        total_chunks_processed=0,
        total_sources=len(raw_source_ids),
    )


async def reconstruct_processed_sources(
    conversation_id: uuid.UUID,
    session: AsyncSession,
) -> list[ProcessedSource]:
    """Reconstruct minimal ProcessedSource list from DB for expansion.

    Returns lightweight ProcessedSource objects (no section text) —
    sufficient for the ingest agent prompt context.
    """
    from kt_db.models import IngestSource

    result = await session.execute(
        select(IngestSource)
        .where(
            IngestSource.conversation_id == conversation_id,
            IngestSource.status == "ready",
        )
        .order_by(IngestSource.created_at)
    )
    sources = list(result.scalars().all())

    processed: list[ProcessedSource] = []
    for src in sources:
        ps = ProcessedSource(
            source_id=str(src.id),
            name=src.original_name,
            raw_source_id=str(src.raw_source_id) if src.raw_source_id else None,
            is_image=bool(src.mime_type and src.mime_type.startswith("image/")),
        )
        processed.append(ps)

    return processed


async def decompose_all_sources(
    processed_sources: list[ProcessedSource],
    ctx: AgentContext,
    emit: EventCallback | None = None,
    chunk_selection: ChunkSelection = None,
    max_concurrent: int = 10,
) -> DecompositionSummary:
    """Decompose processed sources upfront, filling the fact pool.

    For each ProcessedSource:
    - Text sources: for each selected section, extract facts via LLM
    - Image sources: extract facts via multimodal vision model

    Extraction (LLM calls) runs in parallel capped by ``max_concurrent``.
    Storage (dedup + DB writes) runs sequentially on the shared session.

    If chunk_selection is provided, only the selected chunks are processed.
    chunk_selection maps source_id -> set of local section indices to include.

    Returns a DecompositionSummary with counts by type and key topics.
    """
    import asyncio

    from kt_db.models import Fact
    from kt_db.models import RawSource as RawSourceModel
    from kt_facts.models import ExtractedFactWithAttribution
    from kt_facts.pipeline import DecompositionPipeline

    # ── Phase 0: Build extraction task list ─────────────────────────

    @dataclass
    class _ExtractionTask:
        """One unit of work for the extraction phase."""

        source_name: str
        source_id: str
        raw_source: RawSourceModel
        section_text: str | None = None
        is_image: bool = False
        extracted: list[ExtractedFactWithAttribution] = field(default_factory=list)
        error: str | None = None

    tasks: list[_ExtractionTask] = []
    source_summaries: list[dict[str, Any]] = []
    source_task_ranges: list[tuple[str, int, int]] = []

    for ps in processed_sources:
        raw_source = await _lookup_raw_source(ps.raw_source_id, ctx.graph_engine._write_session)
        if raw_source is None:
            logger.warning("No raw source for %s, skipping decomposition", ps.source_id)
            source_summaries.append(
                {
                    "name": ps.name,
                    "fact_count": 0,
                    "error": "No raw source record",
                }
            )
            continue

        selected_indices: set[int] | None = None
        if chunk_selection is not None:
            selected_indices = chunk_selection.get(ps.source_id)
            if selected_indices is not None and len(selected_indices) == 0:
                source_summaries.append(
                    {
                        "name": ps.name,
                        "fact_count": 0,
                        "skipped": True,
                    }
                )
                continue

        task_start = len(tasks)

        if ps.is_image:
            if selected_indices is not None and 0 not in selected_indices:
                source_summaries.append(
                    {
                        "name": ps.name,
                        "fact_count": 0,
                        "skipped": True,
                    }
                )
                continue

            tasks.append(
                _ExtractionTask(
                    source_name=ps.name,
                    source_id=ps.source_id,
                    raw_source=raw_source,
                    is_image=True,
                )
            )
        else:
            sections = ps.sections
            if ps.is_short and ps.full_text:
                sections = [ps.full_text]

            for i, section_text in enumerate(sections):
                if selected_indices is not None and i not in selected_indices:
                    continue
                if not section_text.strip():
                    continue
                tasks.append(
                    _ExtractionTask(
                        source_name=ps.name,
                        source_id=ps.source_id,
                        raw_source=raw_source,
                        section_text=section_text,
                    )
                )

        task_end = len(tasks)
        if task_end > task_start:
            source_task_ranges.append((ps.name, task_start, task_end))
        elif not any(s["name"] == ps.name for s in source_summaries):
            source_summaries.append(
                {
                    "name": ps.name,
                    "fact_count": 0,
                    "skipped": True,
                }
            )

    if not tasks:
        return DecompositionSummary(
            total_facts=0,
            source_summaries=source_summaries,
            total_chunks_processed=0,
            total_sources=len(processed_sources),
        )

    # ── Phase 1: Extract in parallel (LLM calls, no DB) ────────────

    decomp_pipeline = DecompositionPipeline(ctx.model_gateway)
    semaphore = asyncio.Semaphore(max_concurrent)
    completed_count = 0
    total_tasks = len(tasks)

    async def _run_extraction(task: _ExtractionTask) -> None:
        nonlocal completed_count
        async with semaphore:
            try:
                if task.is_image:
                    task.extracted = await decomp_pipeline.extract_image(
                        task.raw_source,
                        task.source_name,
                        f"Ingest: {task.source_name}",
                        ctx.file_data_store,
                    )
                else:
                    assert task.section_text is not None
                    task.extracted = await decomp_pipeline.extract_text(
                        task.section_text,
                        task.source_name,
                        f"Ingest: {task.source_name}",
                    )
            except Exception as exc:
                logger.exception(
                    "Error extracting from %s (%s)",
                    task.source_name,
                    "image" if task.is_image else "text",
                )
                task.error = str(exc)

            completed_count += 1
            if emit:
                await emit(
                    "activity_log",
                    action=f"Extracted {completed_count}/{total_tasks} chunks",
                    tool="ingest",
                )

    if emit:
        await emit(
            "activity_log",
            action=f"Extracting facts from {total_tasks} chunks ({max_concurrent} parallel)...",
            tool="ingest",
        )

    await asyncio.gather(*[_run_extraction(t) for t in tasks])

    # ── Phase 2: Store sequentially (dedup + DB writes) ─────────────

    storable_tasks = [t for t in tasks if not t.error and t.extracted]
    total_facts_to_store = sum(len(t.extracted) for t in storable_tasks)

    if emit:
        await emit(
            "activity_log",
            action=f"Storing {total_facts_to_store} facts from {len(storable_tasks)} chunks...",
            tool="ingest",
        )

    all_facts: list[Fact] = []
    stored_count = 0

    for i, task in enumerate(storable_tasks):
        try:
            facts = await decomp_pipeline.store_extracted_facts(
                task.extracted,
                task.raw_source,
                None,  # graph-db session no longer needed (FactRepository is vestigial)
                ctx.embedding_service,
                qdrant_client=ctx.qdrant_client,
                write_session=ctx.graph_engine._write_session,
            )
            all_facts.extend(facts)
            stored_count += len(facts)
        except Exception:
            logger.exception(
                "Error storing facts for %s",
                task.source_name,
            )
        if ctx.graph_engine._write_session is not None:
            await ctx.graph_engine.commit()

        if emit and (i + 1) % 3 == 0:
            await emit(
                "activity_log",
                action=f"Stored {stored_count} facts ({i + 1}/{len(storable_tasks)} chunks)",
                tool="ingest",
            )

    if emit:
        await emit(
            "activity_log",
            action=f"Storage complete: {stored_count} facts stored",
            tool="ingest",
        )

    # Clean up image data from ephemeral store
    if ctx.file_data_store:
        for task in tasks:
            if task.is_image:
                ctx.file_data_store.remove(task.raw_source.uri)

    # ── Build per-source summaries from task results ────────────────

    for source_name, start, end in source_task_ranges:
        source_tasks = tasks[start:end]
        fact_count = sum(len(t.extracted) for t in source_tasks if not t.error)
        errors = [t.error for t in source_tasks if t.error]
        summary: dict[str, Any] = {
            "name": source_name,
            "fact_count": fact_count,
            "chunk_count": len(source_tasks),
        }
        if any(t.is_image for t in source_tasks):
            summary["is_image"] = True
        if errors:
            summary["errors"] = len(errors)
        source_summaries.append(summary)

    # Build summary
    type_counts: dict[str, int] = dict(Counter(f.fact_type for f in all_facts))

    key_topics = _extract_key_topics(all_facts)

    return DecompositionSummary(
        total_facts=len(all_facts),
        fact_type_counts=type_counts,
        source_summaries=source_summaries,
        key_topics=key_topics[:20],
        total_chunks_processed=len(tasks),
        total_sources=len(processed_sources),
        inserted_fact_ids=[str(f.id) for f in all_facts if f.id is not None],
    )


def _extract_key_topics(facts: list[Any]) -> list[str]:
    """Extract key topic names from facts using simple word frequency."""
    from collections import Counter as Ctr

    word_counts: Ctr[str] = Ctr()
    for f in facts:
        content = f.content if hasattr(f, "content") else str(f)
        words = content.split()
        for w in words:
            cleaned = w.strip(".,;:!?\"'()[]{}").strip()
            if len(cleaned) > 2 and cleaned[0].isupper():
                word_counts[cleaned] += 1

    return [word for word, _count in word_counts.most_common(20)]
