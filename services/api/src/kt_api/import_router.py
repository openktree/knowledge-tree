"""Import endpoints — upload exported JSON back into the system."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncGenerator, Callable, Coroutine, Sequence
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from kt_api.dependencies import get_db_session, get_qdrant_client_cached, get_write_session_factory_cached
from kt_api.import_service import (
    create_seeds_from_import,
    import_edges,
    import_facts,
    import_nodes,
    link_facts_to_nodes,
)
from kt_api.schemas import (
    ImportFactsRequest,
    ImportNodesRequest,
    ImportResponse,
)
from kt_config.settings import get_settings
from kt_models.embeddings import EmbeddingService
from kt_models.gateway import ModelGateway

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/import", tags=["import"])


def _strip_stale_embeddings(
    facts: Sequence[object],
    nodes: Sequence[object],
    embedding_model: str | None,
) -> None:
    """Clear pre-computed embeddings if the model doesn't match the current one.

    Mutates the fact/node objects in place, setting ``embedding = None``
    so that the import service re-generates them.
    """
    if embedding_model is None:
        return  # No model info — strip all embeddings to be safe
    current_model = get_settings().embedding_model
    if embedding_model == current_model:
        return  # Model matches — keep embeddings
    logger.info(
        "Embedding model mismatch (export=%s, current=%s) — stripping pre-computed embeddings",
        embedding_model, current_model,
    )
    for item in [*facts, *nodes]:
        if hasattr(item, "embedding"):
            item.embedding = None  # type: ignore[attr-defined]


def _get_embedding_service() -> EmbeddingService | None:
    """Create an EmbeddingService if an API key is configured."""
    settings = get_settings()
    return EmbeddingService() if settings.openrouter_api_key else None


def _get_cleanup_gateway(cleanup_enabled: bool) -> ModelGateway | None:
    """Create a ModelGateway for cleanup if cleanup is enabled and an API key exists."""
    if not cleanup_enabled:
        return None
    settings = get_settings()
    return ModelGateway() if settings.openrouter_api_key else None


def _sse_event(data: dict[str, object]) -> str:
    """Format a single SSE event."""
    return f"data: {json.dumps(data)}\n\n"


# ── Standard (non-streaming) endpoints ───────────────────────────────────


@router.post("/facts", response_model=ImportResponse)
async def import_facts_endpoint(
    request: ImportFactsRequest,
    session: AsyncSession = Depends(get_db_session),
) -> ImportResponse:
    """Import facts from a previously exported JSON payload."""
    _strip_stale_embeddings(request.facts, [], request.embedding_model)
    embedding_service = _get_embedding_service()
    settings = get_settings()
    cleanup_gateway = _get_cleanup_gateway(request.cleanup)
    write_session = get_write_session_factory_cached()()
    try:
        fact_results, _, rejected = await import_facts(
            request.facts, session, embedding_service,
            do_cleanup=request.cleanup,
            cleanup_min_words=request.cleanup_min_words,
            cleanup_gateway=cleanup_gateway,
            cleanup_batch_size=settings.import_cleanup_batch_size,
            qdrant_client=get_qdrant_client_cached(),
            write_session=write_session,
        )
        await write_session.commit()
        await session.commit()
    finally:
        await write_session.close()
    return ImportResponse(
        imported_facts=fact_results,
        rejected_count=len(rejected),
        rejected_facts=rejected,
    )


@router.post("/nodes", response_model=ImportResponse)
async def import_nodes_endpoint(
    request: ImportNodesRequest,
    session: AsyncSession = Depends(get_db_session),
) -> ImportResponse:
    """Import nodes (with facts, edges, and links) from exported JSON."""
    _strip_stale_embeddings(request.facts, request.nodes, request.embedding_model)
    embedding_service = _get_embedding_service()
    settings = get_settings()
    cleanup_gateway = _get_cleanup_gateway(request.cleanup)
    errors: list[str] = []

    qdrant = get_qdrant_client_cached()
    write_session = get_write_session_factory_cached()()
    try:
        fact_results, fact_id_map, rejected = await import_facts(
            request.facts, session, embedding_service,
            do_cleanup=request.cleanup,
            cleanup_min_words=request.cleanup_min_words,
            cleanup_gateway=cleanup_gateway,
            cleanup_batch_size=settings.import_cleanup_batch_size,
            qdrant_client=qdrant,
            write_session=write_session,
        )
        node_results, node_id_map = await import_nodes(
            request.nodes, session, embedding_service,
            qdrant_client=qdrant,
        )
        await link_facts_to_nodes(
            request.node_fact_links, node_id_map, fact_id_map, session,
        )
        edge_count = await import_edges(
            request.edges, node_id_map, session, fact_id_map=fact_id_map,
        )
        seed_count = await create_seeds_from_import(
            request.nodes, request.node_fact_links,
            node_id_map, fact_id_map, write_session,
        )

        await write_session.commit()
        await session.commit()
    finally:
        await write_session.close()
    return ImportResponse(
        imported_facts=fact_results,
        imported_nodes=node_results,
        imported_edges=edge_count,
        imported_seeds=seed_count,
        rejected_count=len(rejected),
        rejected_facts=rejected,
        errors=errors,
    )


# ── Streaming (SSE) endpoints ────────────────────────────────────────────


@router.post("/facts/stream")
async def import_facts_stream(
    raw_request: Request,
    session: AsyncSession = Depends(get_db_session),
) -> StreamingResponse:
    """Import facts with SSE progress streaming."""
    body = await raw_request.json()
    request = ImportFactsRequest(**body)
    _strip_stale_embeddings(request.facts, [], request.embedding_model)
    queue: asyncio.Queue[str | None] = asyncio.Queue()

    async def on_progress(phase: str, processed: int, total: int) -> None:
        await queue.put(_sse_event({
            "type": "progress", "phase": phase,
            "processed": processed, "total": total,
        }))

    async def run_import() -> ImportResponse:
        embedding_service = _get_embedding_service()
        settings = get_settings()
        cleanup_gateway = _get_cleanup_gateway(request.cleanup)
        write_session = get_write_session_factory_cached()()
        try:
            fact_results, _, rejected = await import_facts(
                request.facts, session, embedding_service, on_progress=on_progress,
                do_cleanup=request.cleanup,
                cleanup_min_words=request.cleanup_min_words,
                cleanup_gateway=cleanup_gateway,
                cleanup_batch_size=settings.import_cleanup_batch_size,
                qdrant_client=get_qdrant_client_cached(),
                write_session=write_session,
            )
            await write_session.commit()
            await session.commit()
        finally:
            await write_session.close()
        return ImportResponse(
            imported_facts=fact_results,
            rejected_count=len(rejected),
            rejected_facts=rejected,
        )

    return StreamingResponse(
        _stream_with_progress(queue, run_import, {
            "type": "start", "phase": "facts", "total": len(request.facts),
        }),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/nodes/stream")
async def import_nodes_stream(
    raw_request: Request,
    session: AsyncSession = Depends(get_db_session),
) -> StreamingResponse:
    """Import nodes (with facts, edges, links) with SSE progress streaming."""
    body = await raw_request.json()
    request = ImportNodesRequest(**body)
    _strip_stale_embeddings(request.facts, request.nodes, request.embedding_model)
    queue: asyncio.Queue[str | None] = asyncio.Queue()

    async def on_progress(phase: str, processed: int, total: int) -> None:
        await queue.put(_sse_event({
            "type": "progress", "phase": phase,
            "processed": processed, "total": total,
        }))

    async def run_import() -> ImportResponse:
        embedding_service = _get_embedding_service()
        settings = get_settings()
        cleanup_gateway = _get_cleanup_gateway(request.cleanup)

        qdrant = get_qdrant_client_cached()
        write_session = get_write_session_factory_cached()()
        try:
            fact_results, fact_id_map, rejected = await import_facts(
                request.facts, session, embedding_service, on_progress=on_progress,
                do_cleanup=request.cleanup,
                cleanup_min_words=request.cleanup_min_words,
                cleanup_gateway=cleanup_gateway,
                cleanup_batch_size=settings.import_cleanup_batch_size,
                qdrant_client=qdrant,
                write_session=write_session,
            )
            node_results, node_id_map = await import_nodes(
                request.nodes, session, embedding_service, on_progress=on_progress,
                qdrant_client=qdrant,
            )
            await link_facts_to_nodes(
                request.node_fact_links, node_id_map, fact_id_map, session,
                on_progress=on_progress,
            )
            edge_count = await import_edges(
                request.edges, node_id_map, session, on_progress=on_progress,
                fact_id_map=fact_id_map,
            )
            seed_count = await create_seeds_from_import(
                request.nodes, request.node_fact_links,
                node_id_map, fact_id_map, write_session,
            )

            await write_session.commit()
            await session.commit()
        finally:
            await write_session.close()
        return ImportResponse(
            imported_facts=fact_results,
            imported_nodes=node_results,
            imported_edges=edge_count,
            imported_seeds=seed_count,
            rejected_count=len(rejected),
            rejected_facts=rejected,
        )

    return StreamingResponse(
        _stream_with_progress(queue, run_import, {
            "type": "start",
            "facts": len(request.facts),
            "nodes": len(request.nodes),
            "links": len(request.node_fact_links),
            "edges": len(request.edges),
        }),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


async def _stream_with_progress(
    queue: asyncio.Queue[str | None],
    run_import: Callable[[], Coroutine[Any, Any, ImportResponse]],
    start_event: dict[str, object],
) -> AsyncGenerator[str, None]:
    """Generic SSE generator that runs an import task and yields progress events.

    The import task pushes progress SSE strings to the queue via on_progress.
    This generator reads from the queue and yields them to the client.
    When the task completes, it yields the final 'complete' event.
    """
    yield _sse_event(start_event)

    # Use a sentinel to signal completion
    import_result: list[ImportResponse] = []
    import_error: list[Exception] = []

    async def _wrapper() -> None:
        try:
            result = await run_import()
            import_result.append(result)
        except Exception as e:
            import_error.append(e)
        finally:
            await queue.put(None)  # Sentinel

    task = asyncio.create_task(_wrapper())

    while True:
        event = await queue.get()
        if event is None:
            break
        yield event

    # Ensure the task is fully done
    await task

    if import_error:
        yield _sse_event({"type": "error", "message": str(import_error[0])})
    elif import_result:
        yield _sse_event({"type": "complete", "result": import_result[0].model_dump()})
