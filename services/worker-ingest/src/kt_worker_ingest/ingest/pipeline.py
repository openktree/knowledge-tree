"""Ingest pipeline — process all ingest sources for a conversation."""

from __future__ import annotations

import hashlib
import logging
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

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
from kt_worker_ingest.ingest.processing import process_uploaded_file
from kt_worker_ingest.ingest.section_index import SectionMeta, build_section_index

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


async def process_ingest_sources(
    conversation_id: uuid.UUID,
    session: AsyncSession,
    file_data_store: FileDataStore,
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
    is_full_text: bool = True,
) -> RawSource:
    """Find an existing RawSource by id (deterministic from URI), or create one.

    Dual-writes to graph-db and write-db when ``write_session`` is provided.
    The synchronous ingest path needs the graph-db row immediately so the
    same-transaction FactSource FK can resolve; the write-db mirror keeps
    worker-sync's watermark in lockstep so it never re-emits the row.
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
        is_full_text=is_full_text,
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

    raw_source = await _lookup_raw_source(str(src.raw_source_id), write_session or session)
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

    # Stable cross-graph identifiers used by the multigraph public-cache
    # machinery (PR2 plumbs them through ProcessedSource; PR3 persists
    # them on RawSource.canonical_url / .doi).  Computed even on partial
    # successes — the helpers are pure and cheap.
    canonical = canonicalize_url(uri)
    doi = extract_doi(uri, fetch_result.html_metadata)

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
            canonical_url=canonical,
            doi=doi,
            is_public=fetch_result.is_public,
        )

    # Text/HTML/PDF content
    text = fetch_result.content
    if not text:
        return None

    content_hash = hashlib.sha256(text.encode()).hexdigest()
    # The DOI provider returns content_type="text/plain" when it only has
    # Crossref metadata (title/abstract), not the actual paper.  All other
    # providers (httpx, curl_cffi, flaresolverr) return full page content.
    fetcher_ct = fetch_result.content_type or "text/html"
    is_full = not (fetch_result.provider_id == "doi" and fetcher_ct == "text/plain")
    raw_source = await _find_or_create_raw_source(
        session,
        uri=uri,
        title=uri,
        raw_content=text[:50000],
        content_hash=content_hash,
        content_type=fetcher_ct,
        provider_id="ingest_link",
        write_session=write_session,
        is_full_text=is_full,
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
