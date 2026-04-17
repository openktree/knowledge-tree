"""Source decomposition workflows (scope-based, Flow B).

Three Hatchet registrations:
1. ``decompose_source_task``  — Decompose a single raw source into facts.
2. ``entity_extraction_task`` — Extract entities from facts + create seeds.
3. ``decompose_sources_wf``   — Fan-out per-source decomposition → entity extraction.

This brings the same durability that Flow A (search → decompose_page → decompose_chunk)
already has to scope-based extraction (Flow B), where GatherFactsPipeline.gather()
calls DecompositionPipeline.decompose() on sources from external search.
"""

from __future__ import annotations

import logging
import uuid
from datetime import timedelta
from typing import Any, cast

from hatchet_sdk import ConcurrencyExpression, ConcurrencyLimitStrategy, Context

from kt_config.settings import get_settings
from kt_hatchet.client import get_hatchet
from kt_hatchet.lifespan import WorkerState
from kt_hatchet.models import (
    DecomposeSourceInput,
    DecomposeSourceOutput,
    DecomposeSourcesInput,
    DecomposeSourcesOutput,
    EntityExtractionInput,
    EntityExtractionOutput,
)
from kt_hatchet.tracked_task import tracked_task

logger = logging.getLogger(__name__)

hatchet = get_hatchet()
_schedule_timeout = timedelta(minutes=get_settings().hatchet_schedule_timeout_minutes)


# ---------------------------------------------------------------------------
# decompose_source_task: single source → facts + author info
# ---------------------------------------------------------------------------


@tracked_task(
    hatchet,
    task_type="decomposition",
    name="decompose_source",
    input_validator=DecomposeSourceInput,
    execution_timeout=timedelta(minutes=30),
    schedule_timeout=_schedule_timeout,
    concurrency=ConcurrencyExpression(
        expression="'decompose_source'",
        max_runs=50,
        limit_strategy=ConcurrencyLimitStrategy.GROUP_ROUND_ROBIN,
    ),
)
async def decompose_source_task(input: DecomposeSourceInput, ctx: Context) -> dict:
    """Decompose a single raw source into provenance-tracked facts."""
    state = cast(WorkerState, ctx.lifespan)

    from kt_db.repositories.write_sources import WriteSourceRepository
    from kt_facts.pipeline import DecompositionPipeline, _store_extracted_facts_impl
    from kt_models.embeddings import EmbeddingService
    from kt_models.gateway import ModelGateway

    model_gateway = cast(ModelGateway, state.model_gateway)
    embedding_service = cast(EmbeddingService, state.embedding_service)

    pipeline = DecompositionPipeline(model_gateway)

    fact_count = 0
    fact_ids: list[str] = []
    author_person: str | None = None
    author_org: str | None = None

    # Read source from write-db (behind pgbouncer) instead of graph-db
    # to avoid exhausting the graph-db connection pool when many
    # decompose tasks run concurrently.
    write_session = (await state.resolve_sessions(input.graph_id))[1]()
    try:
        write_source_repo = WriteSourceRepository(write_session)
        source = await write_source_repo.get_by_id(uuid.UUID(input.raw_source_id))

        if source is None or not source.raw_content:
            logger.warning(
                "decompose_source: source %s not found or empty",
                input.raw_source_id,
            )
            return DecomposeSourceOutput().model_dump()

        # Safety net: skip super sources that slipped past the gathering filter
        # (unless force=True, e.g. manual reingest)
        if getattr(source, "is_super_source", False) and not input.force:
            logger.info("decompose_source: skipping super source %s", input.raw_source_id)
            return DecomposeSourceOutput().model_dump()

        # Snapshot source attributes before any session issues
        src_uri = source.uri
        src_content = source.raw_content or ""
        src_title = getattr(source, "title", None)
        src_hash = getattr(source, "content_hash", None) or ""
        src_provider = getattr(source, "provider_id", None) or ""

        from kt_models.expense import expense_subtask

        # Phase 1a: Extract facts from text (pure LLM, no DB)
        with expense_subtask("decomposition"):
            extracted = await pipeline.extract_text(
                src_content,
                input.concept,
                query_context=input.query_context,
                source_url=src_uri,
                source_title=src_title,
            )

        # Phase 1b: Extract author info (pure LLM, no DB)
        try:
            with expense_subtask("author_extraction"):
                author = await pipeline._extract_source_author(source)
                author_person = author.person
                author_org = author.organization
        except Exception:
            logger.debug(
                "Author extraction failed for %s",
                src_uri,
                exc_info=True,
            )

        # Phase 2: Dedup and store facts
        if extracted:
            from kt_db.repositories.facts import FactRepository
            from kt_db.repositories.write_facts import WriteFactRepository

            write_fact_repo = WriteFactRepository(write_session)
            # FactRepository passed for API compat; dedup is fully Qdrant-based
            repo = FactRepository(write_session)

            facts = await _store_extracted_facts_impl(
                extracted,
                source,  # type: ignore[arg-type]  # WriteRawSource duck-types as RawSource
                repo,
                embedding_service,
                source_content=src_content,
                qdrant_client=state.qdrant_client,
                write_fact_repo=write_fact_repo,
                source_uri=src_uri,
                source_title=src_title,
                source_content_hash=src_hash,
                source_provider_id=src_provider,
                author_person=author_person,
                author_org=author_org,
            )
            fact_count = len(facts)
            fact_ids = [str(f.id) for f in facts]
            await write_session.commit()
    except Exception:
        logger.exception(
            "decompose_source: failed for %s",
            input.raw_source_id,
        )
        try:
            await write_session.rollback()
        except Exception:
            pass
    finally:
        await write_session.close()

    logger.info(
        "decompose_source completed: source=%s facts=%d",
        input.raw_source_id,
        fact_count,
    )

    return DecomposeSourceOutput(
        fact_count=fact_count,
        fact_ids=fact_ids,
        author_person=author_person,
        author_org=author_org,
    ).model_dump()


# ---------------------------------------------------------------------------
# entity_extraction_task: facts → entities + seeds
# ---------------------------------------------------------------------------


@tracked_task(
    hatchet,
    task_type="entity_extraction",
    name="entity_extraction",
    input_validator=EntityExtractionInput,
    execution_timeout=timedelta(minutes=30),
    schedule_timeout=_schedule_timeout,
)
async def entity_extraction_task(input: EntityExtractionInput, ctx: Context) -> dict:
    """Extract entities from facts via LLM.

    Seed storage is NOT done here — the orchestrator (decompose_sources)
    collects results from all extraction tasks and writes seeds in a
    single batch to avoid hot-row contention on write_seeds.
    """
    state = cast(WorkerState, ctx.lifespan)

    from kt_db.repositories.write_facts import WriteFactRepository
    from kt_models.gateway import ModelGateway

    model_gateway = cast(ModelGateway, state.model_gateway)

    if not input.fact_ids:
        return EntityExtractionOutput().model_dump()

    # Load facts from write-db by IDs (read-only)
    fact_uuids = [uuid.UUID(fid) for fid in input.fact_ids]
    write_session = (await state.resolve_sessions(input.graph_id))[1]()
    try:
        write_fact_repo = WriteFactRepository(write_session)
        write_facts = await write_fact_repo.get_by_ids(fact_uuids)

        if not write_facts:
            return EntityExtractionOutput().model_dump()

        # Extract entities using the configured extractor plugin.
        from kt_core_engine_api.extractor import ExtractedEntity
        from kt_facts.pipeline import get_entity_extractor

        extractor = get_entity_extractor(model_gateway)
        raw_entities: list[ExtractedEntity] = await extractor.extract(write_facts, scope=input.concept) or []

        # Convert to dict format expected by downstream seed storage
        extracted_nodes: list[dict] = []
        for entity in raw_entities:
            node: dict = {
                "name": entity.name,
                "fact_indices": entity.fact_indices,
                "aliases": entity.aliases,
            }
            # Resolve fact_indices (1-indexed into write_facts) to fact UUIDs
            resolved: list[str] = []
            for idx in entity.fact_indices:
                if isinstance(idx, int) and 1 <= idx <= len(write_facts):
                    resolved.append(str(write_facts[idx - 1].id))
            node["fact_ids"] = resolved
            extracted_nodes.append(node)

        # Serialize extracted_nodes for output (include fact_ids, aliases,
        # extraction_role so the orchestrator can pass them to seed storage)
        serializable_nodes: list[dict[str, Any]] = []
        for node in extracted_nodes:
            serializable_nodes.append(
                {
                    "name": node.get("name", ""),
                    "node_type": node.get("node_type", "concept"),
                    "entity_subtype": node.get("entity_subtype"),
                    "fact_indices": node.get("fact_indices", []),
                    "fact_ids": node.get("fact_ids", []),
                    "aliases": node.get("aliases", []),
                    "extraction_role": node.get("extraction_role", "mentioned"),
                }
            )

        logger.info(
            "entity_extraction completed: facts=%d entities=%d",
            len(write_facts),
            len(serializable_nodes),
        )

        return EntityExtractionOutput(
            extracted_nodes=serializable_nodes,
        ).model_dump()

    finally:
        await write_session.close()


# ---------------------------------------------------------------------------
# decompose_sources_wf: orchestrate per-source fan-out → entity extraction
# ---------------------------------------------------------------------------

decompose_sources_wf = hatchet.workflow(
    name="decompose_sources",
    input_validator=DecomposeSourcesInput,
)


@tracked_task(
    decompose_sources_wf,
    task_type="decompose_sources",
    execution_timeout=timedelta(minutes=60),
    schedule_timeout=_schedule_timeout,
)
async def decompose_sources(input: DecomposeSourcesInput, ctx: Context) -> dict:
    """Fan out per-source decomposition, then run entity extraction."""
    state = cast(WorkerState, ctx.lifespan)

    image_set = set(input.image_source_ids)

    # Only fan out text sources — images stay inline in the caller
    text_source_ids = [sid for sid in input.raw_source_ids if sid not in image_set]

    if not text_source_ids:
        return DecomposeSourcesOutput().model_dump()

    # Fan out decompose_source_task per text source
    bulk_items = [
        decompose_source_task.create_bulk_run_item(
            input=DecomposeSourceInput(
                raw_source_id=sid,
                concept=input.concept,
                query_context=input.query_context,
                is_image=False,
                message_id=input.message_id,
                conversation_id=input.conversation_id,
                graph_id=input.graph_id,
            ),
        )
        for sid in text_source_ids
    ]

    source_results = await decompose_source_task.aio_run_many(bulk_items)

    # Collect all fact_ids and author info
    all_fact_ids: list[str] = []
    total_fact_count = 0
    source_authors: list[dict[str, Any]] = []

    for result in source_results:
        output = DecomposeSourceOutput.model_validate(result)
        total_fact_count += output.fact_count
        all_fact_ids.extend(output.fact_ids)
        if output.author_person or output.author_org:
            source_authors.append(
                {
                    "author_person": output.author_person,
                    "author_org": output.author_org,
                }
            )

    # Run entity extraction if we have facts
    extracted_nodes: list[dict[str, Any]] = []
    seed_keys: list[str] = []

    if all_fact_ids:
        # Split into chunks of ≤1000 facts to run extraction tasks in parallel
        chunk_size = 1000
        fact_chunks = [all_fact_ids[i : i + chunk_size] for i in range(0, len(all_fact_ids), chunk_size)]

        if len(fact_chunks) == 1:
            # Single chunk — dispatch directly
            entity_result = await entity_extraction_task.aio_run(
                EntityExtractionInput(
                    fact_ids=all_fact_ids,
                    concept=input.concept,
                    source_authors=source_authors,
                    message_id=input.message_id,
                    conversation_id=input.conversation_id,
                    graph_id=input.graph_id,
                ),
            )
            entity_output = EntityExtractionOutput.model_validate(entity_result)
            extracted_nodes = entity_output.extracted_nodes
        else:
            # Multiple chunks — fan out in parallel
            logger.info(
                "Splitting entity extraction: %d facts → %d chunks of ≤%d",
                len(all_fact_ids),
                len(fact_chunks),
                chunk_size,
            )
            bulk_items = [
                entity_extraction_task.create_bulk_run_item(
                    input=EntityExtractionInput(
                        fact_ids=chunk,
                        concept=input.concept,
                        # Only first chunk gets source_authors to avoid
                        # duplicate author-entity seeds
                        source_authors=source_authors if i == 0 else [],
                        message_id=input.message_id,
                        conversation_id=input.conversation_id,
                        graph_id=input.graph_id,
                    ),
                )
                for i, chunk in enumerate(fact_chunks)
            ]
            chunk_results = await entity_extraction_task.aio_run_many(bulk_items)

            # Merge results across chunks
            for result in chunk_results:
                entity_output = EntityExtractionOutput.model_validate(result)
                extracted_nodes.extend(entity_output.extracted_nodes)

    # ── Seed storage: single writer, zero contention ──────────────
    # Entity extraction tasks return extracted_nodes with resolved
    # fact_ids (UUIDs). We load the referenced facts once, remap to
    # fact_indices, and call store_seeds_from_extracted_nodes in a
    # single transaction — eliminating the N-writer lock storm.
    if extracted_nodes:
        all_referenced_fact_ids: set[str] = set()
        for node in extracted_nodes:
            all_referenced_fact_ids.update(node.get("fact_ids", []))

        write_session = (await state.resolve_sessions(input.graph_id))[1]()
        try:
            from kt_db.repositories.write_facts import WriteFactRepository
            from kt_db.repositories.write_seeds import WriteSeedRepository
            from kt_facts.processing.seed_extraction import store_seeds_from_extracted_nodes
            from kt_models.embeddings import EmbeddingService
            from kt_qdrant.repositories.seeds import QdrantSeedRepository

            embedding_service = cast(EmbeddingService, state.embedding_service)

            # Load all referenced facts (single SELECT query)
            write_fact_repo = WriteFactRepository(write_session)
            fact_uuids = [uuid.UUID(fid) for fid in all_referenced_fact_ids]
            write_facts = await write_fact_repo.get_by_ids(fact_uuids)

            # Build unified facts list and remap fact_ids → fact_indices
            # (store_seeds_from_extracted_nodes expects 1-indexed positions)
            fact_id_to_pos = {str(f.id): i + 1 for i, f in enumerate(write_facts)}
            for node in extracted_nodes:
                node["fact_indices"] = [
                    fact_id_to_pos[fid] for fid in node.get("fact_ids", []) if fid in fact_id_to_pos
                ]

            write_seed_repo = WriteSeedRepository(write_session)

            if state.qdrant_client is None:
                raise RuntimeError("Qdrant client is required for seed extraction but was not available on WorkerState")
            qdrant_seed_repo = QdrantSeedRepository(state.qdrant_client)

            _link_count, seed_keys = await store_seeds_from_extracted_nodes(
                extracted_nodes,
                write_facts,
                write_seed_repo,
                embedding_service=embedding_service,
                qdrant_seed_repo=qdrant_seed_repo,
            )

            # Create author seeds (lightweight, no fact linking).
            # Previously done inside entity_extraction_task; now batched here
            # alongside entity seeds to keep all seed writes in one place.
            if source_authors:
                from kt_core_engine_api.extractor import is_valid_entity_name
                from kt_db.keys import make_seed_key

                author_seeds_data: list[dict[str, Any]] = []
                for author_info in source_authors:
                    person = author_info.get("author_person") or ""
                    org = author_info.get("author_org") or ""
                    for name in person.split(","):
                        name = name.strip()
                        if name and is_valid_entity_name(name):
                            author_seeds_data.append(
                                {
                                    "key": make_seed_key(name),
                                    "name": name,
                                    "node_type": "entity",
                                    "entity_subtype": "person",
                                }
                            )
                    for name in org.split(","):
                        name = name.strip()
                        if name and is_valid_entity_name(name):
                            author_seeds_data.append(
                                {
                                    "key": make_seed_key(name),
                                    "name": name,
                                    "node_type": "entity",
                                    "entity_subtype": "organization",
                                }
                            )
                if author_seeds_data:
                    await write_seed_repo.upsert_seeds_batch(author_seeds_data)

            await write_session.commit()
        except Exception:
            logger.exception("Seed storage failed in decompose_sources")
            try:
                await write_session.rollback()
            except Exception:
                pass
            seed_keys = []
        finally:
            await write_session.close()

    logger.info(
        "decompose_sources completed: sources=%d facts=%d entities=%d seeds=%d",
        len(text_source_ids),
        total_fact_count,
        len(extracted_nodes),
        len(seed_keys),
    )

    return DecomposeSourcesOutput(
        total_fact_count=total_fact_count,
        fact_ids=all_fact_ids,
        extracted_nodes=extracted_nodes,
        seed_keys=seed_keys,
    ).model_dump()


# ---------------------------------------------------------------------------
# reingest_source_wf: re-fetch URL + re-decompose (forced, bypass hash check)
# ---------------------------------------------------------------------------

from kt_hatchet.models import ReingestSourceInput, ReingestSourceOutput

reingest_source_wf = hatchet.workflow(
    name="reingest_source",
    input_validator=ReingestSourceInput,
)


@tracked_task(
    reingest_source_wf,
    task_type="reingest_source",
    execution_timeout=timedelta(minutes=30),
    schedule_timeout=_schedule_timeout,
)
async def reingest_source_task(input: ReingestSourceInput, ctx: Context) -> dict:
    """Re-fetch a source URL and re-decompose into facts.

    This is a forced re-ingestion: content hash checks are bypassed so
    that decomposition runs even if the content hasn't changed (e.g. a
    previous decomposition failed).

    Steps:
    1. Re-fetch the URL via ContentFetcher.
    2. Force-update source content in write-db (bypass hash dedup).
    3. Dispatch decompose_source_task for fact extraction.
    """
    state = cast(WorkerState, ctx.lifespan)

    from kt_db.repositories.write_sources import WriteSourceRepository
    from kt_providers.fetch import build_fetch_registry

    write_session = (await state.resolve_sessions(input.graph_id))[1]()
    content_updated = False
    fact_count = 0
    fact_ids: list[str] = []
    message = ""

    try:
        write_source_repo = WriteSourceRepository(write_session)
        source = await write_source_repo.get_by_id(uuid.UUID(input.raw_source_id))

        if source is None:
            message = "Source not found in write-db"
            logger.warning("reingest: source %s not found", input.raw_source_id)
            return ReingestSourceOutput(message=message).model_dump()

        # Step 1: Re-fetch URL content via the full provider chain
        ctx.log(f"Re-fetching URL: {source.uri}")
        registry = build_fetch_registry(state.settings)
        try:
            result = await registry.fetch(source.uri)
        finally:
            await registry.close()

        fetcher_attempts = [a.to_dict() for a in result.attempts]
        if not result.success:
            message = f"Failed to fetch source content: {result.error}"
            ctx.log(message)
            await write_source_repo.mark_fetch_attempted(
                source.id,
                error=result.error,
                fetcher_attempts=fetcher_attempts,
            )
            await write_session.commit()
            return ReingestSourceOutput(message=message).model_dump()

        # Step 2: Force-update content (bypass hash dedup)
        new_content = result.content or ""
        new_hash = WriteSourceRepository.compute_hash(new_content)

        from sqlalchemy import update as sa_update

        from kt_db.write_models import WriteRawSource

        values: dict[str, object] = {
            "raw_content": new_content,
            "content_hash": new_hash,
            "is_full_text": True,
        }
        if result.content_type is not None:
            values["content_type"] = result.content_type

        await write_session.execute(sa_update(WriteRawSource).where(WriteRawSource.id == source.id).values(**values))
        await write_source_repo.mark_fetch_attempted(
            source.id,
            error=None,
            fetcher_winner=result.provider_id,
            fetcher_attempts=fetcher_attempts,
        )
        await write_session.commit()
        content_updated = True
        ctx.log("Source content updated, starting decomposition")

        # Step 3: Dispatch decompose_source_task (reuses existing logic)
        # force=True bypasses the super-source safety guard so manual
        # reingest works for large sources that were deferred during research.
        # Use source.id (write-db) not input.raw_source_id (graph-db) —
        # they can differ when create_or_get deduplicates by URI.
        decompose_result = await decompose_source_task.aio_run(
            DecomposeSourceInput(
                raw_source_id=str(source.id),
                concept=input.concept,
                query_context=input.query_context,
                force=True,
                graph_id=input.graph_id,
            ),
        )

        output = DecomposeSourceOutput.model_validate(decompose_result)
        fact_count = output.fact_count
        fact_ids = output.fact_ids
        message = f"Extracted {fact_count} fact(s) from source."
        if fact_count == 0:
            message = "Content re-fetched but no new facts could be extracted."

    except Exception:
        logger.exception("reingest: failed for %s", input.raw_source_id)
        message = "Reingest failed due to an internal error."
        try:
            await write_session.rollback()
        except Exception:
            pass
    finally:
        await write_session.close()

    ctx.log(f"Reingest complete: {fact_count} facts")

    return ReingestSourceOutput(
        fact_count=fact_count,
        fact_ids=fact_ids,
        content_updated=content_updated,
        message=message,
    ).model_dump()
