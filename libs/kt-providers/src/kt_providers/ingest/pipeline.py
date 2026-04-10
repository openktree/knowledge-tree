"""Ingest pipeline — prepare layer.

Contains source processing, chunking, and LLM-based chunk review.
These functions are shared between the API (synchronous prepare endpoint)
and the ingest worker.  The heavier decomposition/extraction layer remains
in ``kt_worker_ingest``.
"""

from __future__ import annotations

import hashlib
import logging
import uuid
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
from kt_providers.fetch.canonical import canonicalize_url, extract_doi
from kt_providers.ingest.processing import process_uploaded_file
from kt_providers.ingest.section_index import SectionMeta, build_section_index

if TYPE_CHECKING:
    from kt_agents_core.state import EventCallback

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

    # Multigraph public-cache identifiers (PR2/PR3).  Computed at fetch time
    # for link sources via ``canonicalize_url`` / ``extract_doi``; remain
    # None for file uploads (which have no canonical URL by design — file
    # uploads are always private and never participate in the public
    # graph cache).  Persisted to RawSource.canonical_url / .doi in PR3.
    canonical_url: str | None = None
    doi: str | None = None

    # Public-cache eligibility (PR1/PR5). Mirrors ``FetchResult.is_public``,
    # which the fetch registry stamps from the winning provider's
    # ``is_public`` class flag (with optional operator overrides). Drives
    # both the public-graph cache lookup BEFORE decomposition and the
    # contribute-back hook AFTER it. ``None`` for file uploads — they are
    # always private and never participate in the public graph.
    is_public: bool | None = None


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


# Maps source_id -> set of section indices to include.
# None means "all chunks" (no filtering).
ChunkSelection = dict[str, set[int]] | None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


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

                if source.source_type == "file":
                    processed_list = await _process_file_source(
                        source,
                        upload_base,
                        file_data_store,
                        session,
                        write_session=write_session,
                    )
                    if processed_list:
                        for ps in processed_list:
                            results.append(ps)
                            await repo.update_status(
                                uuid.UUID(ps.source_id.split(":")[0]),
                                "ready",
                                raw_source_id=ps.raw_source_id,
                            )
                    else:
                        await repo.update_status(source_id, "error")
                elif source.source_type == "link":
                    result = await _process_link_source(
                        source,
                        fetch_registry,
                        file_data_store,
                        session,
                        write_session=write_session,
                    )
                    if result:
                        results.append(result)
                        await repo.update_status(source_id, "ready", raw_source_id=result.raw_source_id)
                    else:
                        await repo.update_status(source_id, "error")
                else:
                    await repo.update_status(source_id, "error")
            except Exception:
                logger.exception("Error processing source %s (%s)", source_id, source_name)
                try:
                    await repo.update_status(source_id, "error")
                except Exception:
                    logger.exception("Failed to mark source %s as error", source_id)
    except Exception:
        logger.exception("Fatal error in process_ingest_sources for conversation %s", conversation_id)

    await session.commit()

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


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


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
    """Best-effort write of the fetcher audit trail to write-db."""
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
    """Find an existing RawSource by id (deterministic from URI), or create one.

    Dual-writes to graph-db and write-db when ``write_session`` is provided.
    """
    from kt_db.keys import uri_to_source_id

    deterministic_id = uri_to_source_id(uri)

    if write_session is not None:
        from kt_db.repositories.write_sources import WriteSourceRepository

        write_repo = WriteSourceRepository(write_session)
        await write_repo.create_or_get(
            uri=uri,
            title=title,
            raw_content=raw_content,
            content_hash=content_hash,
            provider_id=provider_id,
        )

    result = await session.execute(select(RawSource).where(RawSource.id == deterministic_id))
    existing = result.scalar_one_or_none()
    if existing is not None:
        return existing

    raw_source = RawSource(
        id=deterministic_id,
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
    """Rebuild ProcessedSource(s) from an existing RawSource (idempotent re-run)."""
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
    """Process a file upload source."""
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
    """Process a link source by fetching the URL via the fetch provider chain."""
    from kt_db.models import IngestSource

    src: IngestSource = source  # type: ignore[assignment]
    uri = src.original_name  # For links, original_name is the URL

    fetch_result = await fetch_registry.fetch(uri)
    fetcher_attempts = [a.to_dict() for a in fetch_result.attempts]

    canonical = canonicalize_url(uri)
    doi = extract_doi(uri, fetch_result.html_metadata)

    if not fetch_result.success and not fetch_result.is_image:
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
            canonical_url=canonical,
            doi=doi,
            is_public=fetch_result.is_public,
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
        canonical_url=canonical,
        doi=doi,
        is_public=fetch_result.is_public,
    )
