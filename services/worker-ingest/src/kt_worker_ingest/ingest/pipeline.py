"""Ingest pipeline — process all ingest sources for a conversation."""

from __future__ import annotations

import hashlib
import logging
import uuid
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from kt_config.settings import get_settings
from kt_db.models import RawSource
from kt_db.repositories.ingest_sources import IngestSourceRepository
from kt_providers.fetch import (
    FetchProviderRegistry,
    FileDataStore,
    build_fetch_registry,
)
from kt_worker_ingest.ingest.processing import process_uploaded_file
from kt_worker_ingest.ingest.section_index import SectionMeta, build_section_index

if TYPE_CHECKING:
    from kt_agents_core.state import AgentContext, EventCallback

logger = logging.getLogger(__name__)


@dataclass
class ProcessedSource:
    """A fully processed ingest source ready for the agent."""

    source_id: str
    name: str
    raw_source_id: str | None = None

    # Text sources
    sections: list[str] = field(default_factory=list)
    section_metas: list[SectionMeta] = field(default_factory=list)
    summary: str | None = None
    is_short: bool = False
    full_text: str | None = None

    # Image sources
    is_image: bool = False
    content_type: str | None = None


@dataclass
class ChunkInfo:
    """Info about a single chunk, with optional LLM recommendation."""

    source_id: str
    source_name: str
    chunk_index: int
    char_count: int
    preview: str
    is_image: bool = False
    recommended: bool = True
    reason: str = ""


@dataclass
class DecompositionSummary:
    """Summary of upfront decomposition of all sources."""

    total_facts: int
    fact_type_counts: dict[str, int] = field(default_factory=dict)
    source_summaries: list[dict[str, Any]] = field(default_factory=list)
    key_topics: list[str] = field(default_factory=list)
    total_chunks_processed: int = 0
    total_sources: int = 0


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


async def process_ingest_sources(
    conversation_id: uuid.UUID,
    session: AsyncSession,
    file_data_store: FileDataStore,
    emit: EventCallback | None = None,
    write_session: AsyncSession | None = None,
) -> list[ProcessedSource]:
    """Process all ingest sources for a conversation.

    Idempotent: if an IngestSource already has a raw_source_id, rebuilds the
    ProcessedSource from the existing RawSource rather than creating a duplicate.

    For each source:
    1. File uploads: classify and extract text or prepare image bytes
    2. Link sources: fetch via FetchProviderRegistry (fallback chain)
    3. Create RawSource record for provenance (or reuse existing)
    4. Build section index for text sources
    5. Update status

    Returns list of ProcessedSource ready for the ingest agent.
    """
    settings = get_settings()
    repo = IngestSourceRepository(session)
    sources = await repo.get_by_conversation(conversation_id)

    if not sources:
        return []

    fetch_registry = build_fetch_registry(settings)
    results: list[ProcessedSource] = []
    upload_base = Path(settings.ingest_upload_dir)

    try:
        for source in sources:
            # Capture ID eagerly before any DB operation can invalidate the session
            source_id = source.id
            source_name = source.original_name
            try:
                # Idempotent: if already processed, rebuild from existing RawSource
                if source.raw_source_id is not None:
                    rebuilt = await _rebuild_from_existing(
                        source,
                        upload_base,
                        file_data_store,
                        session,
                        write_session=write_session,
                    )
                    if rebuilt:
                        results.extend(rebuilt)
                        continue

                await repo.update_status(source_id, "processing")
                await session.flush()

                if emit:
                    await emit(
                        "activity_log",
                        action=f"Processing source: {source_name}",
                        tool="ingest",
                    )

                if source.source_type == "file":
                    file_results = await _process_file_source(
                        source,
                        upload_base,
                        file_data_store,
                        session,
                        write_session=write_session,
                    )
                    if file_results:
                        # Use first result (the main text source) for provenance
                        primary = file_results[0]
                        update_fields: dict[str, object] = {"status": "ready"}
                        if primary.raw_source_id:
                            update_fields["raw_source_id"] = uuid.UUID(primary.raw_source_id)
                        if primary.section_metas:
                            update_fields["section_count"] = len(primary.section_metas)
                        if primary.summary:
                            update_fields["summary"] = primary.summary
                        await repo.update_fields(source_id, **update_fields)
                        await session.flush()
                        results.extend(file_results)
                    else:
                        await repo.update_status(source_id, "failed", error="No content extracted")
                        await session.flush()
                else:
                    processed = await _process_link_source(
                        source,
                        fetch_registry,
                        file_data_store,
                        session,
                        write_session=write_session,
                    )

                    if processed is not None:
                        # Update ingest source with provenance link
                        update_fields_l: dict[str, object] = {"status": "ready"}
                        if processed.raw_source_id:
                            update_fields_l["raw_source_id"] = uuid.UUID(processed.raw_source_id)
                        if processed.section_metas:
                            update_fields_l["section_count"] = len(processed.section_metas)
                        if processed.summary:
                            update_fields_l["summary"] = processed.summary
                        await repo.update_fields(source_id, **update_fields_l)
                        await session.flush()
                        results.append(processed)
                    else:
                        await repo.update_status(source_id, "failed", error="No content extracted")
                        await session.flush()

            except Exception as e:
                logger.exception("Error processing ingest source %s", source_id)
                try:
                    await session.rollback()
                except Exception:
                    logger.debug("Rollback failed", exc_info=True)
                # Start a fresh transaction for the status update
                try:
                    await repo.update_status(source_id, "failed", error=str(e)[:500])
                    await session.commit()
                except Exception:
                    logger.debug("Failed to update source status after error", exc_info=True)
    finally:
        await fetch_registry.close()

    return results


def build_chunk_list(processed_sources: list[ProcessedSource]) -> list[ChunkInfo]:
    """Build a flat list of ChunkInfo from processed sources.

    Each entry gets a global index and preview. Images always get one entry.
    Short text sources get one entry. Long text sources get one entry per section.
    """
    chunks: list[ChunkInfo] = []
    idx = 0

    for ps in processed_sources:
        if ps.is_image:
            chunks.append(
                ChunkInfo(
                    source_id=ps.source_id,
                    source_name=ps.name,
                    chunk_index=idx,
                    char_count=0,
                    preview=f"[Image: {ps.name}]",
                    is_image=True,
                )
            )
            idx += 1
        elif ps.is_short and ps.full_text:
            preview = ps.full_text[:200].replace("\n", " ").strip()
            if len(ps.full_text) > 200:
                preview += "..."
            chunks.append(
                ChunkInfo(
                    source_id=ps.source_id,
                    source_name=ps.name,
                    chunk_index=idx,
                    char_count=len(ps.full_text),
                    preview=preview,
                )
            )
            idx += 1
        else:
            for meta in ps.section_metas:
                chunks.append(
                    ChunkInfo(
                        source_id=ps.source_id,
                        source_name=ps.name,
                        chunk_index=idx,
                        char_count=meta.char_count,
                        preview=meta.preview_text,
                    )
                )
                idx += 1

    return chunks


_REVIEW_PROMPT = """\
You are a document triage assistant. Given the chunk previews below from uploaded \
documents, decide which chunks contain substantive knowledge worth extracting \
and which are low-value (table of contents, indexes, bibliographies, blank pages, \
boilerplate, legal disclaimers, acknowledgements, appendices with raw data, etc.).

Respond with a JSON object:
{{"chunks": [
  {{"index": 0, "recommended": true, "reason": ""}},
  {{"index": 1, "recommended": false, "reason": "Table of contents — structural, no knowledge"}},
  ...
]}}

Rules:
- Mark a chunk as recommended=true if it likely contains knowledge worth extracting.
- Mark as recommended=false ONLY for clearly low-value content (TOC, indexes, \
boilerplate, blanks, purely structural).
- When in doubt, mark as recommended=true — it's better to over-include.
- Images are always recommended=true.
- Keep reasons very short (under 10 words).

Chunks:
"""


_REVIEW_BATCH_SIZE = 100


async def review_chunks(
    chunks: list[ChunkInfo],
    gateway: Any,
) -> list[ChunkInfo]:
    """Use a fast LLM to review chunk previews and recommend which to process.

    If there are more than 100 chunks, batches them into multiple LLM calls
    of up to 100 each. Returns the same list with recommended/reason fields
    filled in.
    """
    if not chunks:
        return chunks

    # Skip LLM review for small documents — not worth the cost
    if len(chunks) < 10:
        for c in chunks:
            c.recommended = True
            c.reason = ""
        return chunks

    # Batch into groups of _REVIEW_BATCH_SIZE
    batches: list[list[ChunkInfo]] = []
    for i in range(0, len(chunks), _REVIEW_BATCH_SIZE):
        batches.append(chunks[i : i + _REVIEW_BATCH_SIZE])

    import asyncio

    by_index: dict[int, dict[str, Any]] = {}
    failed_chunks: list[ChunkInfo] = []

    async def _review_batch(batch: list[ChunkInfo]) -> None:
        lines: list[str] = []
        for c in batch:
            tag = "[IMAGE]" if c.is_image else f"[{c.char_count} chars]"
            lines.append(f"[{c.chunk_index}] {tag} {c.preview}")

        prompt = _REVIEW_PROMPT + "\n".join(lines)

        try:
            data = await gateway.generate_json(
                model_id=gateway.decomposition_model,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=16000,
                reasoning_effort=gateway.decomposition_thinking_level or None,
            )

            reviewed = data.get("chunks", data) if isinstance(data, dict) else data
            for item in reviewed:
                if isinstance(item, dict) and "index" in item:
                    by_index[item["index"]] = item

        except Exception:
            logger.exception(
                "Chunk review LLM call failed for batch of %d — marking as recommended",
                len(batch),
            )
            failed_chunks.extend(batch)

    semaphore = asyncio.Semaphore(10)

    async def _limited(batch: list[ChunkInfo]) -> None:
        async with semaphore:
            await _review_batch(batch)

    await asyncio.gather(*[_limited(batch) for batch in batches])

    for c in failed_chunks:
        c.recommended = True

    for c in chunks:
        if c.chunk_index in by_index:
            entry = by_index[c.chunk_index]
            c.recommended = bool(entry.get("recommended", True))
            c.reason = str(entry.get("reason", ""))
        # Images always recommended
        if c.is_image:
            c.recommended = True

    return chunks


# ── Chunk selection type ──────────────────────────────────────────

# Maps source_id -> set of section indices to include.
# None means "all chunks" (no filtering).
ChunkSelection = dict[str, set[int]] | None


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
    #
    # Each _ExtractionTask describes one LLM call to make.  We collect
    # them all first, then run Phase 1 (extract) in parallel and
    # Phase 2 (store) sequentially.

    @dataclass
    class _ExtractionTask:
        """One unit of work for the extraction phase."""

        source_name: str
        source_id: str
        raw_source: RawSourceModel
        # For text chunks: the section text to decompose
        section_text: str | None = None
        # For images: flag + optional data
        is_image: bool = False
        # Extraction result (filled in Phase 1)
        extracted: list[ExtractedFactWithAttribution] = field(default_factory=list)
        error: str | None = None

    tasks: list[_ExtractionTask] = []
    source_summaries: list[dict[str, Any]] = []
    # Track which source each task belongs to, for aggregating summaries
    source_task_ranges: list[tuple[str, int, int]] = []  # (source_name, start_idx, end_idx)

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

        # Check if this source has any selected chunks
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
            # All sections were empty or skipped
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

    # Extract key topics from fact content (simple frequency analysis)
    key_topics = _extract_key_topics(all_facts)

    return DecompositionSummary(
        total_facts=len(all_facts),
        fact_type_counts=type_counts,
        source_summaries=source_summaries,
        key_topics=key_topics[:20],
        total_chunks_processed=len(tasks),
        total_sources=len(processed_sources),
    )


def _extract_key_topics(facts: list[Any]) -> list[str]:
    """Extract key topic names from facts using simple word frequency."""
    from collections import Counter as Ctr

    # Count capitalized multi-word phrases as potential concepts
    word_counts: Ctr[str] = Ctr()
    for f in facts:
        content = f.content if hasattr(f, "content") else str(f)
        # Extract capitalized words as potential concepts
        words = content.split()
        for w in words:
            cleaned = w.strip(".,;:!?\"'()[]{}").strip()
            if len(cleaned) > 2 and cleaned[0].isupper():
                word_counts[cleaned] += 1

    # Return top concepts by frequency
    return [word for word, _count in word_counts.most_common(20)]


async def _lookup_raw_source(
    raw_source_id: str | None,
    session: AsyncSession,
) -> RawSource | None:
    """Look up a RawSource by ID from write-db (WriteRawSource).

    Returns a graph-db RawSource-shaped object constructed from the
    write-db record so callers (decomposition pipeline) can use it
    without a graph-db connection.
    """
    if raw_source_id is None:
        return None
    try:
        from kt_db.write_models import WriteRawSource

        result = await session.execute(select(WriteRawSource).where(WriteRawSource.id == uuid.UUID(raw_source_id)))
        ws = result.scalar_one_or_none()
        if ws is None:
            return None
        # Build a RawSource-shaped object for callers
        return RawSource(
            id=ws.id,
            uri=ws.uri,
            title=ws.title,
            raw_content=ws.raw_content,
            content_hash=ws.content_hash,
            content_type=ws.content_type,
            provider_id=ws.provider_id,
            is_full_text=ws.is_full_text,
        )
    except Exception:
        logger.exception("Error looking up raw source %s", raw_source_id)
        return None


async def _persist_fetcher_audit(
    write_session: AsyncSession | None,
    source_id: uuid.UUID,
    *,
    winner: str | None,
    attempts: list[dict],
) -> None:
    """Best-effort write of the fetcher audit trail to write-db.

    Failures here must not block the ingest pipeline — the user already
    has their content; persistence of the strategy log is purely
    diagnostic.  Logged at warning so an outage of the write-db (or any
    other persistent failure mode) doesn't silently swallow the only
    breadcrumb explaining why a fetch took the path it did.
    """
    if write_session is None:
        return
    try:
        from kt_db.repositories.write_sources import WriteSourceRepository

        write_repo = WriteSourceRepository(write_session)
        await write_repo.mark_fetch_attempted(
            source_id,
            error=None,
            fetcher_winner=winner,
            fetcher_attempts=attempts,
        )
    except Exception:
        logger.warning("failed to persist fetcher audit for %s", source_id, exc_info=True)


async def _find_or_create_raw_source(
    session: AsyncSession,
    *,
    uri: str,
    title: str,
    raw_content: str,
    content_hash: str,
    content_type: str,
    provider_id: str,
    write_session: AsyncSession | None = None,
) -> RawSource:
    """Find an existing RawSource by content_hash, or create a new one.

    When write_session is provided, creates via WriteSourceRepository on
    write-db instead of writing to graph-db directly.
    """
    if write_session is not None:
        from kt_db.repositories.write_sources import WriteSourceRepository

        write_repo = WriteSourceRepository(write_session)
        ws = await write_repo.create_or_get(
            uri=uri,
            title=title,
            raw_content=raw_content,
            content_hash=content_hash,
            provider_id=provider_id,
        )
        # Return a RawSource-shaped object with matching id for callers
        return RawSource(
            id=ws.id,
            uri=uri,
            title=title,
            raw_content=raw_content,
            content_hash=content_hash,
            content_type=content_type,
            provider_id=provider_id,
            is_full_text=True,
        )

    result = await session.execute(select(RawSource).where(RawSource.content_hash == content_hash))
    existing = result.scalar_one_or_none()
    if existing is not None:
        return existing

    from kt_db.keys import uri_to_source_id

    raw_source = RawSource(
        id=uri_to_source_id(uri),
        uri=uri,
        title=title,
        raw_content=raw_content,
        content_hash=content_hash,
        content_type=content_type,
        provider_id=provider_id,
        is_full_text=True,
    )
    session.add(raw_source)
    await session.flush()
    return raw_source


async def _rebuild_from_existing(
    source: Any,
    upload_base: Path,
    file_data_store: FileDataStore,
    session: AsyncSession,
    write_session: AsyncSession | None = None,
) -> list[ProcessedSource]:
    """Rebuild ProcessedSource(s) from an existing RawSource (idempotent re-run).

    Returns a list because PDF files may produce both a text source and
    additional image-page sources.
    """
    from kt_db.models import IngestSource
    from kt_facts.processing.file_processing import classify_content_type, extract_pdf_pages

    src: IngestSource = source  # type: ignore[assignment]

    raw_source = await _lookup_raw_source(str(src.raw_source_id), session)
    if raw_source is None:
        return []

    results: list[ProcessedSource] = []

    result = ProcessedSource(
        source_id=str(src.id),
        name=src.original_name,
        raw_source_id=str(raw_source.id),
    )

    # Determine if image or text
    ct = raw_source.content_type or ""
    mime = src.mime_type or ""

    if ct.startswith("image/"):
        result.is_image = True
        result.content_type = ct
        # Re-load image bytes into file_data_store for decomposition
        if src.stored_path:
            file_path = upload_base / src.stored_path
            if file_path.exists():
                file_data_store.store(raw_source.uri, file_path.read_bytes())
        results.append(result)

    elif classify_content_type(mime) == "pdf" and src.stored_path:
        # PDF: re-extract pages to separate text-only vs image-heavy pages
        file_path = upload_base / src.stored_path
        if not file_path.exists():
            return []
        uri = f"ingest://upload/{src.conversation_id}/{src.original_name}"
        pdf_pages = extract_pdf_pages(file_path.read_bytes())

        # Build text from text-only pages only
        text_parts: list[str] = []
        for page in pdf_pages:
            if page.is_image and page.image_bytes:
                img_uri = f"{uri}/page-{page.page_number}"
                file_data_store.store(img_uri, page.image_bytes)
                page_hash = hashlib.sha256(page.image_bytes).hexdigest()
                page_raw_source = await _find_or_create_raw_source(
                    session,
                    uri=img_uri,
                    title=f"{src.original_name} — Page {page.page_number + 1}",
                    raw_content=f"[PDF Image Page: {src.original_name} p{page.page_number + 1}]",
                    content_hash=page_hash,
                    content_type="image/png",
                    provider_id="ingest_upload",
                    write_session=write_session,
                )
                results.append(
                    ProcessedSource(
                        source_id=f"{src.id}:page-{page.page_number}",
                        name=f"{src.original_name} — Page {page.page_number + 1}",
                        raw_source_id=str(page_raw_source.id),
                        is_image=True,
                        content_type="image/png",
                    )
                )
            elif page.text:
                text_parts.append(page.text)

        # Build text ProcessedSource from text-only pages
        text = "\n\n".join(text_parts) if text_parts else None
        if text:
            settings = get_settings()
            sections, metas = build_section_index(text)
            result.sections = sections
            result.section_metas = metas
            result.is_short = len(text) <= settings.ingest_short_content_threshold
            if result.is_short:
                result.full_text = text
            results.append(result)

    else:
        text = raw_source.raw_content or ""
        if not text:
            return []
        settings = get_settings()
        sections, metas = build_section_index(text)
        result.sections = sections
        result.section_metas = metas
        result.is_short = len(text) <= settings.ingest_short_content_threshold
        if result.is_short:
            result.full_text = text
        results.append(result)

    return results


async def _process_file_source(
    source: object,
    upload_base: Path,
    file_data_store: FileDataStore,
    session: AsyncSession,
    write_session: AsyncSession | None = None,
) -> list[ProcessedSource]:
    """Process a file upload source.

    Returns a list of ProcessedSource — one for text content, plus one per
    image-heavy PDF page rendered as PNG for the vision model.
    """
    from kt_db.models import IngestSource

    src: IngestSource = source  # type: ignore[assignment]
    if not src.stored_path:
        return []

    file_path = upload_base / src.stored_path
    if not file_path.exists():
        return []

    file_bytes = file_path.read_bytes()
    mime_type = src.mime_type or "application/octet-stream"
    uri = f"ingest://upload/{src.conversation_id}/{src.original_name}"

    processed = await process_uploaded_file(file_bytes, mime_type, uri, file_data_store)

    # Find or create RawSource for provenance
    content_hash = hashlib.sha256(file_bytes).hexdigest()
    raw_source = await _find_or_create_raw_source(
        session,
        uri=uri,
        title=src.original_name,
        raw_content=processed.text[:50000] if processed.text else f"[Image: {src.original_name}]",
        content_hash=content_hash,
        content_type=mime_type,
        provider_id="ingest_upload",
        write_session=write_session,
    )

    results: list[ProcessedSource] = []

    if processed.is_image:
        # Standalone image file (not PDF)
        results.append(
            ProcessedSource(
                source_id=str(src.id),
                name=src.original_name,
                raw_source_id=str(raw_source.id),
                is_image=True,
                content_type=processed.content_type,
            )
        )
    elif processed.text:
        # Text content (from text-only PDF pages or plain text files)
        settings = get_settings()
        sections, metas = build_section_index(processed.text)
        results.append(
            ProcessedSource(
                source_id=str(src.id),
                name=src.original_name,
                raw_source_id=str(raw_source.id),
                sections=sections,
                section_metas=metas,
                is_short=len(processed.text) <= settings.ingest_short_content_threshold,
                full_text=processed.text if len(processed.text) <= settings.ingest_short_content_threshold else None,
            )
        )

    # PDF image pages — each becomes a separate image ProcessedSource
    logger.info(
        "File source '%s': %d text results, %d PDF image pages to process",
        src.original_name,
        len(results),
        len(processed.pdf_image_pages),
    )
    for img_page in processed.pdf_image_pages:
        img_uri = f"{uri}/page-{img_page.page_number}"
        file_data_store.store(img_uri, img_page.image_bytes)
        # Create a RawSource for the image page
        page_hash = hashlib.sha256(img_page.image_bytes).hexdigest()
        page_raw_source = await _find_or_create_raw_source(
            session,
            uri=img_uri,
            title=f"{src.original_name} — Page {img_page.page_number + 1}",
            raw_content=f"[PDF Image Page: {src.original_name} p{img_page.page_number + 1}]",
            content_hash=page_hash,
            content_type="image/png",
            provider_id="ingest_upload",
            write_session=write_session,
        )
        results.append(
            ProcessedSource(
                source_id=f"{src.id}:page-{img_page.page_number}",
                name=f"{src.original_name} — Page {img_page.page_number + 1}",
                raw_source_id=str(page_raw_source.id),
                is_image=True,
                content_type="image/png",
            )
        )

    return results


async def _process_link_source(
    source: object,
    fetch_registry: FetchProviderRegistry,
    file_data_store: FileDataStore,
    session: AsyncSession,
    write_session: AsyncSession | None = None,
) -> ProcessedSource | None:
    """Process a link source by fetching the URL via the fetch provider chain.

    Persists the strategy audit trail (which providers were tried, who won)
    onto the resulting RawSource via WriteSourceRepository.mark_fetch_attempted
    so the API/UI can later show "blocked by Cloudflare (tried httpx →
    curl_cffi → flaresolverr)" instead of a generic "failed fetch".
    """
    from kt_db.models import IngestSource

    src: IngestSource = source  # type: ignore[assignment]
    uri = src.original_name  # For links, original_name is the URL

    fetch_result = await fetch_registry.fetch(uri)
    fetcher_attempts = [a.to_dict() for a in fetch_result.attempts]

    if not fetch_result.success and not fetch_result.is_image:
        # Hard failure across the entire chain.  Best-effort: persist the
        # audit trail on the existing source row if one exists, so users
        # see *why* it failed.  No row exists yet for fresh links, so the
        # call is wrapped in try/except — the ingest_sources status update
        # already records the user-visible failure.
        if write_session is not None:
            try:
                from kt_db.keys import uri_to_source_id
                from kt_db.repositories.write_sources import WriteSourceRepository

                existing_id = uri_to_source_id(uri)
                write_repo = WriteSourceRepository(write_session)
                if (await write_repo.get_by_id(existing_id)) is not None:
                    await write_repo.mark_fetch_attempted(
                        existing_id,
                        error=fetch_result.error,
                        fetcher_attempts=fetcher_attempts,
                    )
            except Exception:
                logger.warning("failed to persist fetch attempts for %s", uri, exc_info=True)
        return None

    # Handle image URLs
    if fetch_result.is_image and fetch_result.raw_bytes:
        file_data_store.store(uri, fetch_result.raw_bytes)
        content_hash = hashlib.sha256(fetch_result.raw_bytes).hexdigest()
        raw_source = await _find_or_create_raw_source(
            session,
            uri=uri,
            title=uri,
            raw_content=f"[Image: {uri}]",
            content_hash=content_hash,
            content_type=fetch_result.content_type or "image/png",
            provider_id="ingest_link",
            write_session=write_session,
        )
        await _persist_fetcher_audit(
            write_session,
            raw_source.id,
            winner=fetch_result.provider_id,
            attempts=fetcher_attempts,
        )

        return ProcessedSource(
            source_id=str(src.id),
            name=uri,
            raw_source_id=str(raw_source.id),
            is_image=True,
            content_type=fetch_result.content_type,
        )

    # Text/HTML/PDF content
    text = fetch_result.content
    if not text:
        return None

    content_hash = hashlib.sha256(text.encode()).hexdigest()
    raw_source = await _find_or_create_raw_source(
        session,
        uri=uri,
        title=uri,
        raw_content=text[:50000],
        content_hash=content_hash,
        content_type=fetch_result.content_type or "text/html",
        provider_id="ingest_link",
        write_session=write_session,
    )
    await _persist_fetcher_audit(
        write_session,
        raw_source.id,
        winner=fetch_result.provider_id,
        attempts=fetcher_attempts,
    )

    settings = get_settings()
    sections, metas = build_section_index(text)
    is_short = len(text) <= settings.ingest_short_content_threshold

    return ProcessedSource(
        source_id=str(src.id),
        name=uri,
        raw_source_id=str(raw_source.id),
        sections=sections,
        section_metas=metas,
        is_short=is_short,
        full_text=text if is_short else None,
    )
