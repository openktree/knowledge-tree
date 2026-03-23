from __future__ import annotations

import logging
import uuid
from typing import TYPE_CHECKING

from kt_config.types import COMPOUND_FACT_TYPES
from kt_db.repositories.facts import FactRepository
from kt_models.embeddings import EmbeddingService

if TYPE_CHECKING:
    from kt_db.repositories.write_facts import WriteFactRepository

logger = logging.getLogger(__name__)

_ATOMIC_THRESHOLD = 0.92
_COMPOUND_THRESHOLD = 0.85


def _threshold_for_type(fact_type: str) -> float:
    """Return cosine-similarity threshold based on fact type.

    Compound types (quote, procedure, reference, code, account) use a
    lower threshold (0.85) because longer content has more natural variance.
    Atomic types use 0.92.
    """
    return _COMPOUND_THRESHOLD if fact_type in COMPOUND_FACT_TYPES else _ATOMIC_THRESHOLD


async def deduplicate_facts(
    items: list[tuple[str, str]],
    repo: FactRepository,
    embedding_service: EmbeddingService | None = None,
    qdrant_client: object | None = None,
    write_fact_repo: WriteFactRepository | None = None,
    pre_embeddings: list[list[float] | None] | None = None,
) -> list[tuple[uuid.UUID, bool]]:
    """Batch-deduplicate facts. Returns list of (fact_id, is_new) in input order.

    Phase 1: Call ``embed_batch()`` once with all contents (or fill with
    ``None`` when no embedding service is available). If ``pre_embeddings``
    is provided, uses those directly and only generates embeddings for items
    where the pre-computed value is ``None``.

    Phase 2: Sequential DB loop — ``find_similar()`` + ``create()`` per fact.
    Must stay sequential because the DB session is not safe for concurrent
    writes.

    New facts are always written to the write-db via ``write_fact_repo``.
    Qdrant dedup remains unchanged.

    Args:
        items: List of ``(content, fact_type)`` pairs.
        repo: The FactRepository instance (graph-db, used for reads only).
        embedding_service: The EmbeddingService for generating embeddings (optional).
        qdrant_client: Optional Qdrant client for vector search (AsyncQdrantClient).
        write_fact_repo: WriteFactRepository for write-db fact creation (required
            for worker pipelines; may be None only in tests).
        pre_embeddings: Optional pre-computed embeddings (same length as items).
            Entries that are ``None`` will be generated via ``embedding_service``.

    Returns:
        List of ``(fact_id, is_new)`` tuples, one per input item.

    Raises:
        RuntimeError: If ``write_fact_repo`` is None when a new fact needs to
            be created. All worker pipelines must provide a write-db session.
    """
    if not items:
        return []

    # Resolve Qdrant repo (required for embedding-based deduplication)
    qdrant_fact_repo = None
    if qdrant_client is not None:
        from kt_qdrant.repositories.facts import QdrantFactRepository

        qdrant_fact_repo = QdrantFactRepository(qdrant_client)
    else:
        logger.error(
            "deduplicate_facts: Qdrant client not provided — deduplication will be skipped, all facts treated as new"
        )

    # Phase 1 — resolve embeddings (pre-computed or batch embed)
    embeddings: list[list[float] | None]
    if pre_embeddings is not None:
        # Use pre-computed; fill gaps via embedding_service
        embeddings = list(pre_embeddings)
        if embedding_service is not None:
            gaps = [(i, items[i][0]) for i, e in enumerate(embeddings) if e is None]
            if gaps:
                gap_texts = [text for _, text in gaps]
                gap_embeddings = await embedding_service.embed_batch(gap_texts)
                for (idx, _), emb in zip(gaps, gap_embeddings):
                    embeddings[idx] = emb
    elif embedding_service is not None:
        contents = [content for content, _ in items]
        raw_embeddings = await embedding_service.embed_batch(contents)
        embeddings = list(raw_embeddings)
    else:
        embeddings = [None] * len(items)

    # Phase 2 — sequential dedup + create
    results: list[tuple[uuid.UUID, bool]] = []
    qdrant_batch: list[tuple[uuid.UUID, list[float], str | None]] = []

    for (content, fact_type), embedding in zip(items, embeddings):
        if embedding is not None:
            threshold = _threshold_for_type(fact_type)

            # Qdrant dedup — required for embedding-based deduplication
            if qdrant_fact_repo is not None:
                qdrant_results = await qdrant_fact_repo.find_most_similar(
                    embedding,
                    score_threshold=threshold,
                )
                if qdrant_results is not None:
                    results.append((qdrant_results.fact_id, False))
                    continue

        # Create new fact — always write to write-db
        if write_fact_repo is None:
            raise RuntimeError(
                "deduplicate_facts: write_fact_repo is required but was None. "
                "All worker pipelines must pass a write-db session to GraphEngine."
            )
        new_id = uuid.uuid4()
        await write_fact_repo.upsert(
            fact_id=new_id,
            content=content,
            fact_type=fact_type,
        )
        results.append((new_id, True))

        # Queue Qdrant upsert for new facts with embeddings
        if embedding is not None:
            qdrant_batch.append((new_id, embedding, fact_type))

    # Batch upsert new fact embeddings to Qdrant
    if qdrant_fact_repo is not None and qdrant_batch:
        try:
            await qdrant_fact_repo.upsert_batch(qdrant_batch)
        except Exception:
            logger.warning("Failed to batch upsert %d facts to Qdrant", len(qdrant_batch), exc_info=True)

    return results
