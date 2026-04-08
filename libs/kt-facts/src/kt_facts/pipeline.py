"""Fact decomposition pipeline.

Provides DecompositionPipeline — the main entry point for extracting,
deduplicating, and storing facts from raw sources. Uses strategy-based
extractors (TextExtractor, ImageExtractor) and a composable prompt
builder.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from sqlalchemy.ext.asyncio import AsyncSession

from kt_db.models import Fact, RawSource
from kt_db.repositories.facts import FactRepository
from kt_facts.author import (
    AuthorInfo,
    SourceContext,
    build_author_chain,
    extract_author,
)
from kt_facts.models import (
    _VALID_TYPES,
    ExtractedFactWithAttribution,
    ProhibitedChunk,
    _format_attribution,
)
from kt_facts.processing.dedup import deduplicate_facts
from kt_models.embeddings import EmbeddingService
from kt_models.gateway import ModelGateway
from kt_providers.fetch import FileDataStore

if TYPE_CHECKING:
    from qdrant_client import AsyncQdrantClient

    from kt_db.repositories.write_facts import WriteFactRepository

logger = logging.getLogger(__name__)


@dataclass
class DecompositionResult:
    """Result of a full decompose() call."""

    facts: list[Fact] = field(default_factory=list)
    extracted_nodes: list[dict[str, Any]] = field(default_factory=list)
    seed_keys: list[str] = field(default_factory=list)
    prohibited_chunks: list[tuple[str, ProhibitedChunk]] = field(default_factory=list)


# ── DecompositionPipeline ────────────────────────────────────────────


class DecompositionPipeline:
    """Main entry point for fact extraction, dedup, and storage."""

    def __init__(
        self,
        gateway: ModelGateway,
        text_extractor: object | None = None,
        image_extractor: object | None = None,
    ) -> None:
        self._gateway = gateway

        from kt_facts.extraction import ImageExtractor, TextExtractor

        self._text_extractor: TextExtractor = (
            text_extractor if isinstance(text_extractor, TextExtractor) else TextExtractor(gateway)
        )
        self._image_extractor: ImageExtractor = (
            image_extractor if isinstance(image_extractor, ImageExtractor) else ImageExtractor(gateway)
        )

    async def decompose(
        self,
        raw_sources: list[RawSource],
        concept: str,
        session: AsyncSession,
        embedding_service: EmbeddingService | None = None,
        query_context: str | None = None,
        file_data_store: FileDataStore | None = None,
        qdrant_client: AsyncQdrantClient | None = None,
        write_session: AsyncSession | None = None,
    ) -> DecompositionResult:
        """Extract facts from raw sources and store them.

        Phase 1 (parallel): LLM extraction runs concurrently across all sources
        and their chunks. Author extraction runs in parallel too.

        Phase 2 (sequential): Dedup and storage run on the shared session one
        source at a time.

        Phase 3: Entity extraction + seed creation from extracted entities.

        When ``write_session`` is provided, new facts and fact-source provenance
        are written to the write-db instead of graph-db.
        """
        valid_sources = [s for s in raw_sources if s.raw_content]
        if not valid_sources:
            return DecompositionResult()

        # Split sources into text and image
        text_sources: list[RawSource] = []
        image_sources: list[RawSource] = []
        for s in valid_sources:
            if file_data_store and file_data_store.has(s.uri):
                image_sources.append(s)
            else:
                text_sources.append(s)

        # Phase 1a: Extract from text sources in parallel
        text_extraction_tasks = [
            self.extract_text(
                s.raw_content or "",
                concept,
                query_context,
                source_url=s.uri,
                source_title=getattr(s, "title", None),
            )
            for s in text_sources
        ]

        # Phase 1b: Extract from image sources in parallel
        image_extraction_tasks = [
            self._extract_image_source(s, concept, query_context, file_data_store) for s in image_sources
        ]

        # Phase 1c: Extract authors in parallel (one call per unique source)
        all_sources = text_sources + image_sources
        author_tasks = [self._extract_source_author(s) for s in all_sources]

        # Run all extractions + author extraction in parallel
        all_tasks = text_extraction_tasks + image_extraction_tasks
        extraction_results = await asyncio.gather(*all_tasks, return_exceptions=True)
        author_results = await asyncio.gather(*author_tasks, return_exceptions=True)

        # Build source → AuthorInfo map
        source_authors: dict[int, AuthorInfo] = {}
        for source, author_result in zip(all_sources, author_results):
            if isinstance(author_result, BaseException):
                logger.debug("Author extraction failed for %s: %s", source.uri, author_result)
                source_authors[id(source)] = AuthorInfo()
            else:
                source_authors[id(source)] = author_result

        # Capture source attributes now — a rollback during Phase 2 would
        # expire all ORM objects, causing MissingGreenlet on subsequent
        # attribute access (lazy load in async context).
        source_attrs: dict[int, tuple[str, str, str | None, str | None, str]] = {
            id(s): (
                s.uri,
                s.raw_content or "",
                getattr(s, "title", None),
                getattr(s, "content_hash", None) or "",
                getattr(s, "provider_id", None) or "",
            )
            for s in all_sources
        }

        # Build write-db fact repository if write session available
        write_fact_repo: WriteFactRepository | None = None
        if write_session is not None:
            from kt_db.repositories.write_facts import WriteFactRepository as _WFR

            write_fact_repo = _WFR(write_session)

        # Phase 2: Store extracted facts sequentially
        repo = FactRepository(session)
        all_facts: list[Fact] = []
        for source, result in zip(all_sources, extraction_results):
            src_uri, src_content, src_title, src_hash, src_provider = source_attrs[id(source)]
            if isinstance(result, BaseException):
                logger.exception("Error extracting from source %s: %s", src_uri, result)
                continue
            try:
                author = source_authors.get(id(source), AuthorInfo())
                facts = await _store_extracted_facts_impl(
                    result,
                    source,
                    repo,
                    embedding_service,
                    source_content=src_content,
                    qdrant_client=qdrant_client,
                    write_fact_repo=write_fact_repo,
                    source_uri=src_uri,
                    source_title=src_title,
                    source_content_hash=src_hash,
                    source_provider_id=src_provider,
                    author_person=author.person,
                    author_org=author.organization,
                )
                all_facts.extend(facts)
            except Exception:
                logger.exception("Error storing facts for source %s", src_uri)
                # Roll back BOTH sessions so subsequent sources can still
                # use clean transactions.  The write-db session is the one
                # that actually failed (upsert into write_facts), but we
                # rollback both to be safe.
                for s in (write_session, session):
                    if s is not None:
                        try:
                            await s.rollback()
                        except Exception:
                            logger.debug("Rollback failed after decompose error", exc_info=True)

        # Collect prohibited chunks from text extractor
        prohibited_chunks: list[tuple[str, ProhibitedChunk]] = []
        for pc in self._text_extractor._prohibited_chunks:
            # Associate each prohibited chunk with the source content_hash
            # We use the first text source's content_hash as a best-effort association
            for source in text_sources:
                src_hash = source_attrs[id(source)][3]  # content_hash
                if src_hash:
                    prohibited_chunks.append((src_hash, pc))
                    break

        # Store prohibited chunks in write-db if available
        if prohibited_chunks and write_session is not None:
            from kt_db.repositories.write_prohibited_chunks import (
                WriteProhibitedChunkRepository as _WPCR,
            )

            pc_repo = _WPCR(write_session)
            for content_hash, pc in prohibited_chunks:
                try:
                    await pc_repo.create(
                        source_content_hash=content_hash,
                        chunk_text=pc.chunk_text,
                        model_id=pc.model_id,
                        error_message=pc.error_message,
                        fallback_model_id=pc.fallback_model_id,
                        fallback_error=pc.fallback_error,
                    )
                except Exception:
                    logger.debug("Failed to store prohibited chunk", exc_info=True)

        # Clean up processed image data
        if file_data_store:
            for s in image_sources:
                file_data_store.remove(s.uri)

        # Commit fact writes before attempting seed creation so facts
        # are durable regardless of whether seed operations succeed.
        if write_session is not None:
            try:
                await write_session.commit()
            except Exception:
                logger.exception("Failed to commit write session after fact storage")

        # Phase 3: Entity extraction + seed creation
        extracted_nodes: list[dict[str, Any]] = []
        seed_keys: list[str] = []
        if all_facts:
            try:
                from kt_facts.processing.entity_extraction import extract_entities_from_facts

                extracted_nodes = (
                    await extract_entities_from_facts(
                        all_facts,
                        self._gateway,
                        scope=concept,
                    )
                    or []
                )
            except Exception:
                logger.exception("Entity extraction failed (non-fatal)")

            # Create seeds from extraction output.
            # Seeds are committed immediately so they are durable and
            # available for Hatchet dedup tasks dispatched by the caller.
            if extracted_nodes and write_session is not None:
                from kt_db.repositories.write_seeds import WriteSeedRepository
                from kt_facts.processing.seed_extraction import (
                    store_seeds_from_extracted_nodes,
                )
                from kt_qdrant.repositories.seeds import QdrantSeedRepository

                if qdrant_client is None:
                    raise RuntimeError("Qdrant client is required for seed extraction but was not provided")
                if embedding_service is None:
                    raise RuntimeError("Embedding service is required for seed extraction but was not provided")

                write_seed_repo = WriteSeedRepository(write_session)
                qdrant_seed_repo = QdrantSeedRepository(qdrant_client)

                try:
                    _link_count, seed_keys = await store_seeds_from_extracted_nodes(
                        extracted_nodes,
                        all_facts,
                        write_seed_repo,
                        embedding_service=embedding_service,
                        qdrant_seed_repo=qdrant_seed_repo,
                    )
                    # Commit seeds immediately — makes them durable and
                    # ensures the write_session is in a clean state.
                    await write_session.commit()
                except Exception:
                    logger.exception("Seed storage failed — rolling back")
                    try:
                        await write_session.rollback()
                    except Exception:
                        pass
                    seed_keys = []

            # Create author seeds (without fact linking — author provenance
            # lives on write_fact_sources.author_person/author_org and is
            # queried dynamically when needed).
            if write_session is not None:
                from kt_db.keys import make_seed_key
                from kt_db.repositories.write_seeds import WriteSeedRepository as _WSR
                from kt_facts.processing.entity_extraction import _is_valid_entity_name

                author_seed_repo = _WSR(write_session)
                author_seeds_data: list[dict[str, Any]] = []

                for source in all_sources:
                    author = source_authors.get(id(source), AuthorInfo())
                    if author.person:
                        for name in author.person.split(","):
                            name = name.strip()
                            if name and _is_valid_entity_name(name):
                                author_seeds_data.append(
                                    {
                                        "key": make_seed_key("entity", name),
                                        "name": name,
                                        "node_type": "entity",
                                        "entity_subtype": "person",
                                    }
                                )
                    if author.organization:
                        for name in author.organization.split(","):
                            name = name.strip()
                            if name and _is_valid_entity_name(name):
                                author_seeds_data.append(
                                    {
                                        "key": make_seed_key("entity", name),
                                        "name": name,
                                        "node_type": "entity",
                                        "entity_subtype": "organization",
                                    }
                                )

                if author_seeds_data:
                    try:
                        await author_seed_repo.upsert_seeds_batch(author_seeds_data)
                        await write_session.commit()
                    except Exception:
                        logger.exception("Author seed creation failed — rolling back")
                        try:
                            await write_session.rollback()
                        except Exception:
                            pass

        return DecompositionResult(
            facts=all_facts,
            extracted_nodes=extracted_nodes,
            seed_keys=seed_keys,
            prohibited_chunks=prohibited_chunks,
        )

    async def extract_text(
        self,
        content: str,
        concept: str,
        query_context: str | None = None,
        source_url: str | None = None,
        source_title: str | None = None,
    ) -> list[ExtractedFactWithAttribution]:
        """Extract facts from text content (chunking if needed).

        Pure LLM calls — no DB interaction. Safe to run concurrently.
        """
        return await self._text_extractor.extract(
            content,
            concept,
            query_context,
            source_url=source_url,
            source_title=source_title,
        )

    async def extract_image(
        self,
        source: RawSource,
        concept: str,
        query_context: str | None,
        file_data_store: FileDataStore | None,
    ) -> list[ExtractedFactWithAttribution]:
        """Extract facts from an image source via multimodal vision model.

        Pure LLM call — no DB interaction. Safe to run concurrently.
        """
        return await self._extract_image_source(source, concept, query_context, file_data_store)

    async def store_extracted_facts(
        self,
        extracted: list[ExtractedFactWithAttribution],
        source: RawSource,
        session: AsyncSession,
        embedding_service: EmbeddingService | None,
        qdrant_client: AsyncQdrantClient | None = None,
        write_session: AsyncSession | None = None,
    ) -> list[Fact]:
        """Dedup and store extracted facts for a single source.

        Performs DB writes — must be called sequentially on a single session.
        """
        write_fact_repo: WriteFactRepository | None = None
        if write_session is not None:
            from kt_db.repositories.write_facts import WriteFactRepository as _WFR

            write_fact_repo = _WFR(write_session)

        repo = FactRepository(session)
        return await _store_extracted_facts_impl(
            extracted,
            source,
            repo,
            embedding_service,
            qdrant_client=qdrant_client,
            write_fact_repo=write_fact_repo,
            source_uri=source.uri,
            source_title=getattr(source, "title", None),
            source_content_hash=getattr(source, "content_hash", None) or "",
            source_provider_id=getattr(source, "provider_id", None) or "",
        )

    async def _extract_source_author(self, source: RawSource) -> AuthorInfo:
        """Extract author info for a single source. Safe to run concurrently."""
        content = source.raw_content or ""
        header = content[:500]
        uri = source.uri or ""

        # Check for PDF/HTML metadata in provider_metadata
        provider_meta = getattr(source, "provider_metadata", None) or {}
        pdf_meta = provider_meta.get("pdf_metadata") if isinstance(provider_meta, dict) else None
        html_meta = provider_meta.get("html_metadata") if isinstance(provider_meta, dict) else None

        is_pdf = bool(pdf_meta) or (getattr(source, "content_type", None) or "").startswith("application/pdf")

        ctx = SourceContext(url=uri, header_text=header, pdf_metadata=pdf_meta, html_metadata=html_meta)
        chain = build_author_chain(self._gateway, is_pdf=is_pdf)
        return await extract_author(chain, ctx)

    async def _extract_image_source(
        self,
        source: RawSource,
        concept: str,
        query_context: str | None,
        file_data_store: FileDataStore | None,
    ) -> list[ExtractedFactWithAttribution]:
        """Extract facts from an image source, updating source raw_content."""
        if file_data_store is None:
            return []

        image_bytes = file_data_store.get(source.uri)
        if image_bytes is None:
            return []

        content_type = getattr(source, "content_type", None) or "image/png"
        facts, description = await self._image_extractor.extract_with_description(
            image_bytes,
            content_type,
            concept,
            query_context,
        )

        # Update the source's raw_content with the AI-generated description
        source.raw_content = description

        return facts


# ── Internal storage logic ───────────────────────────────────────────


async def _store_extracted_facts_impl(
    extracted: list[ExtractedFactWithAttribution],
    source: RawSource,
    repo: FactRepository,
    embedding_service: EmbeddingService | None,
    source_content: str | None = None,
    qdrant_client: AsyncQdrantClient | None = None,
    write_fact_repo: WriteFactRepository | None = None,
    source_uri: str | None = None,
    source_title: str | None = None,
    source_content_hash: str | None = None,
    source_provider_id: str | None = None,
    author_person: str | None = None,
    author_org: str | None = None,
) -> list[Fact]:
    """Dedup and store extracted facts for a single source. Must run on a single session.

    New facts always go to write-db and provenance is stored as
    WriteFactSource.  ``write_fact_repo`` is required for all worker
    pipelines.

    Raises:
        RuntimeError: If ``write_fact_repo`` is None.
    """
    if not extracted:
        return []

    # Use pre-captured content to avoid MissingGreenlet on expired ORM objects
    content = source_content if source_content is not None else (source.raw_content or "")

    # Normalize fact types upfront
    normalized_types: list[str] = []
    for ef in extracted:
        ft = ef.fact_type.strip().lower()
        if ft not in _VALID_TYPES:
            ft = "claim"
        normalized_types.append(ft)

    # Batch dedup: one embed_batch() call for all facts in this source
    items = [(ef.content, ft) for ef, ft in zip(extracted, normalized_types)]
    dedup_results = await deduplicate_facts(
        items,
        repo,
        embedding_service,
        qdrant_client=qdrant_client,
        write_fact_repo=write_fact_repo,
    )

    # Resolve source metadata for provenance
    uri = source_uri or getattr(source, "uri", "")
    title = source_title
    content_hash = source_content_hash or getattr(source, "content_hash", "") or ""
    provider_id = source_provider_id or getattr(source, "provider_id", "") or ""

    # Require write-db for all worker pipelines
    if write_fact_repo is None:
        raise RuntimeError(
            "_store_extracted_facts_impl: write_fact_repo is required but was None. "
            "All worker pipelines must pass a write-db session to GraphEngine."
        )

    # Link each fact to the source via write-db.
    # Wrapped in try/except for compensating Qdrant delete on failure:
    # if the post-dedup logic fails, the DB transaction will roll back but
    # Qdrant already has the points — delete them to prevent orphans.
    try:
        facts: list[Fact] = []
        successful_extracted: list[ExtractedFactWithAttribution] = []
        for ef, (fact_id, _is_new) in zip(extracted, dedup_results):
            try:
                attribution_str = _format_attribution(ef)

                await write_fact_repo.create_fact_source(
                    fact_id=fact_id,
                    raw_source_uri=uri,
                    raw_source_title=title,
                    raw_source_content_hash=content_hash,
                    raw_source_provider_id=provider_id,
                    context_snippet=content[:500],
                    attribution=attribution_str,
                    author_person=author_person,
                    author_org=author_org,
                )
                # Return a transient Fact-like object for pipeline compatibility
                fact = Fact(
                    id=fact_id,
                    content=ef.content,
                    fact_type=ef.fact_type,
                )
                facts.append(fact)
                successful_extracted.append(ef)

            except Exception:
                logger.exception("Error storing extracted fact: %s", ef.content[:100])
                continue

        return facts
    except Exception:
        # Compensating delete: remove newly-created Qdrant points to prevent
        # orphans when the DB transaction rolls back.
        _new_ids = getattr(dedup_results, "new_qdrant_ids", [])
        if _new_ids and qdrant_client is not None:
            try:
                from kt_qdrant.repositories.facts import QdrantFactRepository

                await QdrantFactRepository(qdrant_client).delete_batch(_new_ids)
                logger.info("Compensating delete: removed %d Qdrant points", len(_new_ids))
            except Exception:
                logger.warning("Failed compensating Qdrant delete for %d points", len(_new_ids), exc_info=True)
        raise
