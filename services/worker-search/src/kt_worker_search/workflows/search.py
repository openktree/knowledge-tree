"""Search & decomposition workflows.

Three Hatchet registrations:
1. ``search_wf``          — Web search workflow; fans out page decomposition.
2. ``decompose_page_wf``  — Page decomposition workflow; fans out chunk tasks.
3. ``decompose_chunk_task`` — Standalone task for a single chunk.

Data flows via return values (replacing Redis barriers and pub/sub).
Parent workflows await children with ``aio_run_many`` and aggregate counts.
"""

from __future__ import annotations

import logging
import uuid
from datetime import timedelta
from typing import cast

from hatchet_sdk import Context

from kt_config.settings import get_settings
from kt_hatchet.client import get_hatchet
from kt_hatchet.lifespan import WorkerState
from kt_hatchet.models import (
    DecomposeChunkInput,
    DecomposeChunkOutput,
    DecomposePageInput,
    DecomposePageOutput,
    SearchOutput,
    WebSearchInput,
)

logger = logging.getLogger(__name__)

hatchet = get_hatchet()
_schedule_timeout = timedelta(minutes=get_settings().hatchet_schedule_timeout_minutes)

# ---------------------------------------------------------------------------
# Decompose-chunk: standalone task (leaf of the fan-out tree)
# ---------------------------------------------------------------------------


@hatchet.task(
    name="decompose_chunk",
    input_validator=DecomposeChunkInput,
    execution_timeout=timedelta(minutes=10),
    schedule_timeout=_schedule_timeout,
)
async def decompose_chunk_task(input: DecomposeChunkInput, ctx: Context) -> dict:
    """Decompose a single text chunk into provenance-tracked facts."""
    from kt_hatchet.usage_helpers import flush_usage_to_db
    from kt_models.usage import start_usage_tracking

    state = cast(WorkerState, ctx.lifespan)
    start_usage_tracking()

    from kt_db.repositories.write_sources import WriteSourceRepository
    from kt_facts.pipeline import DecompositionPipeline
    from kt_models.embeddings import EmbeddingService
    from kt_models.gateway import ModelGateway

    model_gateway = cast(ModelGateway, state.model_gateway)
    embedding_service = cast(EmbeddingService, state.embedding_service)

    pipeline = DecompositionPipeline(model_gateway)

    # Extract facts from chunk text (pure LLM, no DB)
    extracted = await pipeline.extract_text(
        input.content,
        input.concept,
        query_context=input.query_context,
    )

    fact_count = 0
    fact_ids: list[str] = []

    # Read source from write-db to avoid graph-db pool pressure
    write_session = (await state.resolve_sessions(input.graph_id))[1]()
    try:
        write_source_repo = WriteSourceRepository(write_session)
        source = await write_source_repo.get_by_id(uuid.UUID(input.raw_source_id))

        if source and extracted:
            facts = await pipeline.store_extracted_facts(
                extracted,
                source,  # type: ignore[arg-type]  # WriteRawSource duck-types as RawSource
                write_session,
                embedding_service,
                qdrant_client=state.qdrant_client,
                write_session=write_session,
            )
            fact_count = len(facts)
            fact_ids = [str(f.id) for f in facts]
            await write_session.commit()
    finally:
        await write_session.close()

    logger.info(
        "decompose_chunk completed: source=%s chunk=%d facts=%d",
        input.raw_source_id,
        input.chunk_index,
        fact_count,
    )

    await flush_usage_to_db(state.write_session_factory, input.conversation_id, input.message_id, "decomposition")

    return DecomposeChunkOutput(
        fact_count=fact_count,
        fact_ids=fact_ids,
    ).model_dump()


# ---------------------------------------------------------------------------
# Decompose-page: workflow that fans out chunk tasks
# ---------------------------------------------------------------------------

decompose_page_wf = hatchet.workflow(
    name="decompose_page",
    input_validator=DecomposePageInput,
)


@decompose_page_wf.task(execution_timeout=timedelta(minutes=10), schedule_timeout=_schedule_timeout)
async def decompose_page(input: DecomposePageInput, ctx: Context) -> dict:
    """Load a page, segment into chunks, fan out decompose_chunk tasks."""
    state = cast(WorkerState, ctx.lifespan)

    from kt_db.repositories.write_sources import WriteSourceRepository
    from kt_facts.processing.segmenter import chunk_if_needed

    # Read source from write-db to avoid graph-db pool pressure
    write_session = (await state.resolve_sessions(input.graph_id))[1]()
    try:
        write_source_repo = WriteSourceRepository(write_session)
        source = await write_source_repo.get_by_id(uuid.UUID(input.raw_source_id))
    finally:
        await write_session.close()

    if source is None:
        logger.warning(
            "decompose_page: source %s not found",
            input.raw_source_id,
        )
        return DecomposePageOutput(fact_count=0).model_dump()

    content = source.raw_content or ""
    if len(content) < 50:
        logger.info(
            "decompose_page: source %s too short (%d chars), skipping",
            input.raw_source_id,
            len(content),
        )
        return DecomposePageOutput(fact_count=0).model_dump()

    # Segment into chunks
    chunks = chunk_if_needed(content)
    if not chunks:
        return DecomposePageOutput(fact_count=0).model_dump()

    # Derive a short concept from the query context (matches old worker logic)
    concept_words = input.query_context.split()[:5]
    concept_str = " ".join(concept_words) if concept_words else input.url

    # Fan out decompose_chunk tasks via aio_run_many
    bulk_items = [
        decompose_chunk_task.create_bulk_run_item(
            input=DecomposeChunkInput(
                raw_source_id=input.raw_source_id,
                chunk_index=i,
                content=chunk,
                concept=concept_str,
                query_context=input.query_context,
                message_id=input.message_id,
                conversation_id=input.conversation_id,
            ),
        )
        for i, chunk in enumerate(chunks)
    ]

    chunk_results = await decompose_chunk_task.aio_run_many(bulk_items)

    # Aggregate fact counts from all chunks
    total_facts = 0
    for result in chunk_results:
        chunk_output = DecomposeChunkOutput.model_validate(result)
        total_facts += chunk_output.fact_count

    logger.info(
        "decompose_page completed: source=%s chunks=%d total_facts=%d",
        input.raw_source_id,
        len(chunks),
        total_facts,
    )

    return DecomposePageOutput(fact_count=total_facts).model_dump()


# ---------------------------------------------------------------------------
# Web search: top-level workflow that fans out page decomposition
# ---------------------------------------------------------------------------

search_wf = hatchet.workflow(name="search", input_validator=WebSearchInput)


@search_wf.task(execution_timeout=timedelta(minutes=10), schedule_timeout=_schedule_timeout)
async def web_search(input: WebSearchInput, ctx: Context) -> dict:
    """Execute web search, store results, fan out page decomposition."""
    state = cast(WorkerState, ctx.lifespan)

    from kt_config.types import RawSearchResult
    from kt_db.repositories.write_sources import WriteSourceRepository
    from kt_providers.fetch import FetchProviderRegistry
    from kt_providers.registry import ProviderRegistry

    provider_registry = cast(ProviderRegistry, state.provider_registry)
    fetch_registry = cast(FetchProviderRegistry | None, state.fetch_registry)

    # 1. Search all providers
    results: list[RawSearchResult] = await provider_registry.search_all(
        input.query,
        max_results=input.max_results,
    )

    # 2. Store results in write-db and optionally fetch full text
    #    (sync worker propagates to graph-db — no direct graph-db writes)
    write_session = (await state.resolve_sessions(input.graph_id))[1]()
    write_source_repo = WriteSourceRepository(write_session)
    sources = []
    try:
        for result in results:
            source = await write_source_repo.create_or_get(
                uri=result.uri,
                title=result.title,
                raw_content=result.raw_content,
                provider_id=result.provider_id,
                provider_metadata=result.provider_metadata,
            )
            sources.append(source)

        # Fetch full-text content for top N URLs if a fetch registry exists
        if sources and fetch_registry is not None:
            urls_to_fetch: list[tuple[int, str]] = []
            for i, source in enumerate(sources):
                if source.is_full_text:
                    continue
                if source.uri and len(urls_to_fetch) < state.settings.full_text_fetch_max_urls:
                    urls_to_fetch.append((i, source.uri))

            if urls_to_fetch:
                uris = [uri for _, uri in urls_to_fetch]
                fetch_results = await fetch_registry.fetch_many(uris)

                for (idx, _uri), fetch_result in zip(urls_to_fetch, fetch_results):
                    src = sources[idx]
                    src.fetch_attempted = True
                    fetch_err = fetch_result.error if not fetch_result.success else None
                    fetcher_attempts = [a.to_dict() for a in fetch_result.attempts]
                    await write_source_repo.mark_fetch_attempted(
                        src.id,
                        error=fetch_err,
                        fetcher_winner=fetch_result.provider_id if fetch_result.success else None,
                        fetcher_attempts=fetcher_attempts,
                    )
                    if fetch_result.success and fetch_result.content:
                        try:
                            updated = await write_source_repo.update_content(
                                src.id,
                                fetch_result.content,
                                is_full_text=True,
                                content_type=fetch_result.content_type,
                            )
                            if updated:
                                src.raw_content = fetch_result.content
                                src.is_full_text = True
                                if fetch_result.content_type:
                                    src.content_type = fetch_result.content_type
                        except Exception:
                            logger.debug(
                                "Failed to update source %s with full text",
                                src.id,
                                exc_info=True,
                            )

        await write_session.commit()
    except Exception:
        try:
            await write_session.rollback()
        except Exception:
            pass
        raise
    finally:
        await write_session.close()

    # 3. Filter to full-text pages with content worth decomposing (>50 chars)
    pages = [s for s in sources if s.is_full_text and s.raw_content and len(s.raw_content) > 50]

    if not pages:
        logger.info(
            "web_search completed (no pages): query=%r scope_id=%s",
            input.query,
            input.scope_id,
        )
        return SearchOutput(total_facts=0, page_count=0).model_dump()

    # 4. Fan out decompose_page workflows via aio_run_many
    bulk_items = [
        decompose_page_wf.create_bulk_run_item(
            input=DecomposePageInput(
                raw_source_id=str(source.id),
                url=source.uri,
                query_context=input.query,
                message_id=input.message_id,
                conversation_id=input.conversation_id,
            ),
        )
        for source in pages
    ]

    page_results = await decompose_page_wf.aio_run_many(bulk_items)

    # 5. Aggregate fact counts from all pages
    total_facts = 0
    for result in page_results:
        page_output = DecomposePageOutput.model_validate(result)
        total_facts += page_output.fact_count

    logger.info(
        "web_search completed: query=%r scope_id=%s pages=%d total_facts=%d",
        input.query,
        input.scope_id,
        len(pages),
        total_facts,
    )

    return SearchOutput(
        total_facts=total_facts,
        page_count=len(pages),
    ).model_dump()
