"""Import service — deduplicate and import facts, nodes, edges, and links."""

from __future__ import annotations

import hashlib
import logging
import re
import uuid
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from qdrant_client import AsyncQdrantClient
from collections.abc import Awaitable, Callable

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from kt_api.schemas import (
    EdgeResponse,
    FactResponse,
    FactSourceInfo,
    ImportResultItem,
    NodeFactLinkItem,
    NodeResponse,
    RejectedFactInfo,
)
from kt_db.models import Node
from kt_db.repositories.edges import EdgeRepository
from kt_db.repositories.facts import FactRepository
from kt_db.repositories.nodes import NodeRepository
from kt_db.repositories.sources import SourceRepository
from kt_facts.processing.cleanup import cleanup_facts
from kt_facts.processing.dedup import deduplicate_facts
from kt_models.embeddings import EmbeddingService
from kt_models.gateway import ModelGateway

logger = logging.getLogger(__name__)

# Callback signature: (phase, processed, total) -> None
ProgressCallback = Callable[[str, int, int], Awaitable[None]]


async def import_facts(
    facts: list[FactResponse],
    session: AsyncSession,
    embedding_service: EmbeddingService | None = None,
    on_progress: ProgressCallback | None = None,
    *,
    do_cleanup: bool = False,
    cleanup_min_words: int = 12,
    cleanup_gateway: ModelGateway | None = None,
    cleanup_batch_size: int = 20,
    qdrant_client: AsyncQdrantClient | None = None,
    write_session: AsyncSession | None = None,
) -> tuple[list[ImportResultItem], dict[str, str], list[RejectedFactInfo]]:
    """Import facts with optional cleanup and deduplication.

    Returns (results, old_id->new_id map, rejected_facts).
    """
    rejected: list[RejectedFactInfo] = []

    # Optional cleanup pass: validate short facts via LLM before dedup
    if do_cleanup and cleanup_gateway is not None and facts:
        result = await cleanup_facts(
            facts,
            min_words=cleanup_min_words,
            gateway=cleanup_gateway,
            content_accessor=lambda f: f.content,
            batch_size=cleanup_batch_size,
            on_progress=on_progress,
        )
        rejected = [RejectedFactInfo(content=r.content, reason=r.reason) for r in result.rejected]
        facts = result.kept  # type: ignore[assignment]

    fact_repo = FactRepository(session)
    results: list[ImportResultItem] = []
    id_map: dict[str, str] = {}
    total = len(facts)

    # Build write-db fact repository for deduplication
    write_fact_repo = None
    if write_session is not None:
        from kt_db.repositories.write_facts import WriteFactRepository

        write_fact_repo = WriteFactRepository(write_session)

    # Build pre-computed embeddings if available from export
    pre_embeddings: list[list[float] | None] | None = None
    if any(f.embedding is not None for f in facts):
        pre_embeddings = [f.embedding for f in facts]

    # Batch dedup: one embed_batch() call for all facts (or use pre-computed)
    items = [(f.content, f.fact_type) for f in facts]
    dedup_results = await deduplicate_facts(
        items,
        fact_repo,
        embedding_service,
        qdrant_client=qdrant_client,
        write_fact_repo=write_fact_repo,
        pre_embeddings=pre_embeddings,
    )

    for i, (fact_data, (new_fact_id, is_new)) in enumerate(zip(facts, dedup_results)):
        try:
            id_map[fact_data.id] = str(new_fact_id)
            results.append(
                ImportResultItem(
                    old_id=fact_data.id,
                    new_id=str(new_fact_id),
                    is_new=is_new,
                )
            )

            # Also create in graph-db for immediate API visibility
            # (sync worker will skip if already present via ON CONFLICT)
            if is_new:
                from sqlalchemy.dialects.postgresql import insert as pg_insert

                from kt_db.models import Fact as FactModel

                stmt = (
                    pg_insert(FactModel)
                    .values(
                        id=new_fact_id,
                        content=fact_data.content,
                        fact_type=fact_data.fact_type,
                    )
                    .on_conflict_do_nothing(index_elements=["id"])
                )
                await session.execute(stmt)

            # Import sources for this fact
            for source_info in fact_data.sources:
                source_id = await _create_or_get_source(
                    source_info,
                    session,
                    write_session,
                )

                # Link fact->source (use savepoint to handle duplicates)
                try:
                    async with session.begin_nested():
                        await fact_repo.create_fact_source(
                            fact_id=new_fact_id,
                            raw_source_id=source_id,
                            context_snippet=source_info.context_snippet,
                            attribution=source_info.attribution,
                            author_person=source_info.author_person,
                            author_org=source_info.author_org,
                        )
                except IntegrityError:
                    logger.debug("Fact-source link already exists: fact=%s source=%s", new_fact_id, source_id)

        except Exception as e:
            logger.warning("Failed to import fact %s: %s", fact_data.id, e)

        if on_progress is not None:
            await on_progress("facts", i + 1, total)

    return results, id_map, rejected


async def import_nodes(
    nodes: list[NodeResponse],
    session: AsyncSession,
    embedding_service: EmbeddingService | None = None,
    on_progress: ProgressCallback | None = None,
    qdrant_client: AsyncQdrantClient | None = None,
) -> tuple[list[ImportResultItem], dict[str, str]]:
    """Import nodes with deduplication. Returns (results, old_id->new_id map)."""
    node_repo = NodeRepository(session)
    results: list[ImportResultItem] = []
    id_map: dict[str, str] = {}
    total = len(nodes)

    # Collect new nodes for batch Qdrant upsert
    qdrant_node_batch: list[tuple[uuid.UUID, list[float], str | None, str | None]] = []

    for i, node_data in enumerate(nodes):
        try:
            # Use pre-computed embedding for dedup search if available
            embedding_for_search = node_data.embedding
            matched_node = await _find_existing_node(
                node_repo,
                node_data.concept,
                embedding_service,
                qdrant_client=qdrant_client,
                pre_embedding=embedding_for_search,
            )

            if matched_node:
                new_id = str(matched_node.id)
                is_new = False
            else:
                new_node = await node_repo.create(
                    concept=node_data.concept,
                    node_type=node_data.node_type,
                    attractor=node_data.attractor,
                    filter_id=node_data.filter_id,
                    max_content_tokens=node_data.max_content_tokens,
                )
                new_id = str(new_node.id)
                is_new = True

                # Queue Qdrant upsert for new nodes with embeddings
                if node_data.embedding is not None:
                    qdrant_node_batch.append(
                        (
                            new_node.id,
                            node_data.embedding,
                            node_data.node_type,
                            node_data.concept,
                        )
                    )
                elif embedding_service is not None:
                    # Generate embedding for new node if not provided
                    try:
                        emb = await embedding_service.embed_text(node_data.concept)
                        qdrant_node_batch.append(
                            (
                                new_node.id,
                                emb,
                                node_data.node_type,
                                node_data.concept,
                            )
                        )
                    except Exception:
                        logger.warning("Failed to embed new node %s", node_data.concept)

            id_map[node_data.id] = new_id
            results.append(
                ImportResultItem(
                    old_id=node_data.id,
                    new_id=new_id,
                    is_new=is_new,
                )
            )

        except Exception as e:
            logger.warning("Failed to import node %s: %s", node_data.id, e)

        if on_progress is not None:
            await on_progress("nodes", i + 1, total)

    # Batch upsert new node embeddings to Qdrant
    if qdrant_client is not None and qdrant_node_batch:
        try:
            from kt_qdrant.repositories.nodes import QdrantNodeRepository

            qdrant_repo = QdrantNodeRepository(qdrant_client)
            await qdrant_repo.upsert_batch(qdrant_node_batch)
        except Exception:
            logger.warning("Failed to batch upsert %d nodes to Qdrant", len(qdrant_node_batch), exc_info=True)

    # Second pass: remap parent_id
    for node_data in nodes:
        if node_data.parent_id and node_data.id in id_map:
            remapped_parent = id_map.get(node_data.parent_id)
            if remapped_parent:
                try:
                    await node_repo.update_fields(
                        uuid.UUID(id_map[node_data.id]),
                        parent_id=uuid.UUID(remapped_parent),
                    )
                except Exception as e:
                    logger.warning("Failed to remap parent for node %s: %s", node_data.id, e)

    return results, id_map


_FACT_TOKEN_RE = re.compile(r"\{fact:([0-9a-f-]{36})\}", re.IGNORECASE)


def _remap_justification_tokens(
    justification: str,
    fact_id_map: dict[str, str],
) -> str:
    """Replace {fact:<old-uuid>} tokens with {fact:<new-uuid>} using the ID map."""

    def _replace(m: re.Match[str]) -> str:
        old_id = m.group(1)
        new_id = fact_id_map.get(old_id)
        if new_id is not None:
            return f"{{fact:{new_id}}}"
        return m.group(0)

    return _FACT_TOKEN_RE.sub(_replace, justification)


async def import_edges(
    edges: list[EdgeResponse],
    node_id_map: dict[str, str],
    session: AsyncSession,
    on_progress: ProgressCallback | None = None,
    fact_id_map: dict[str, str] | None = None,
) -> int:
    """Import edges, remapping node IDs and fact references. Returns count of imported edges."""
    edge_repo = EdgeRepository(session)
    count = 0
    total = len(edges)

    for i, edge_data in enumerate(edges):
        new_source = node_id_map.get(edge_data.source_node_id)
        new_target = node_id_map.get(edge_data.target_node_id)
        if not new_source or not new_target:
            logger.debug(
                "Skipping edge %s: missing node mapping (source=%s, target=%s)",
                edge_data.id,
                edge_data.source_node_id,
                edge_data.target_node_id,
            )
            if on_progress is not None:
                await on_progress("edges", i + 1, total)
            continue

        # Remap {fact:UUID} tokens in justification text
        justification = edge_data.justification
        if justification and fact_id_map:
            justification = _remap_justification_tokens(justification, fact_id_map)

        try:
            edge = await edge_repo.create(
                source_node_id=uuid.UUID(new_source),
                target_node_id=uuid.UUID(new_target),
                relationship_type=edge_data.relationship_type,
                weight=edge_data.weight,
                justification=justification,
            )
            count += 1

            # Recreate edge-fact links from supporting_fact_ids
            if fact_id_map and edge_data.supporting_fact_ids:
                for old_fact_id in edge_data.supporting_fact_ids:
                    new_fact_id = fact_id_map.get(old_fact_id)
                    if new_fact_id:
                        try:
                            await edge_repo.link_fact_to_edge(
                                edge.id,
                                uuid.UUID(new_fact_id),
                            )
                        except Exception:
                            pass  # Duplicate or missing fact — skip
        except Exception as e:
            logger.warning("Failed to import edge %s: %s", edge_data.id, e)

        if on_progress is not None:
            await on_progress("edges", i + 1, total)

    return count


async def link_facts_to_nodes(
    links: list[NodeFactLinkItem],
    node_id_map: dict[str, str],
    fact_id_map: dict[str, str],
    session: AsyncSession,
    on_progress: ProgressCallback | None = None,
) -> int:
    """Link facts to nodes using remapped IDs. Returns count of created links."""
    fact_repo = FactRepository(session)
    count = 0
    total = len(links)

    for i, link in enumerate(links):
        new_node_id = node_id_map.get(link.node_id)
        new_fact_id = fact_id_map.get(link.fact_id)
        if not new_node_id or not new_fact_id:
            if on_progress is not None:
                await on_progress("links", i + 1, total)
            continue

        try:
            result = await fact_repo.link_to_node(
                node_id=uuid.UUID(new_node_id),
                fact_id=uuid.UUID(new_fact_id),
                relevance_score=link.relevance_score,
                stance=link.stance,
            )
            if result is not None:
                count += 1
        except Exception as e:
            logger.warning("Failed to link fact %s to node %s: %s", link.fact_id, link.node_id, e)

        if on_progress is not None:
            await on_progress("links", i + 1, total)

    return count


async def create_seeds_from_import(
    nodes: list[NodeResponse],
    node_fact_links: list[NodeFactLinkItem],
    node_id_map: dict[str, str],
    fact_id_map: dict[str, str],
    write_session: AsyncSession,
) -> int:
    """Create WriteSeed + WriteSeedFact records for imported nodes.

    Each imported node becomes a promoted seed so that the seed layer
    matches what the normal pipeline would have produced.

    Returns count of seeds created.
    """
    from kt_db.keys import make_seed_key
    from kt_db.repositories.write_seeds import WriteSeedRepository

    seed_repo = WriteSeedRepository(write_session)

    # Build seed dicts and collect per-seed fact links
    seed_dicts: list[dict] = []
    seed_fact_links: list[dict] = []
    seed_keys_created: set[str] = set()

    # Build a lookup: old_node_id -> list of fact links
    node_links: dict[str, list[NodeFactLinkItem]] = {}
    for link in node_fact_links:
        node_links.setdefault(link.node_id, []).append(link)

    for node_data in nodes:
        if node_data.id not in node_id_map:
            continue
        seed_key = make_seed_key(node_data.node_type, node_data.concept)

        if seed_key in seed_keys_created:
            continue
        seed_keys_created.add(seed_key)

        # Count facts for this node
        links_for_node = node_links.get(node_data.id, [])
        fact_count = len(links_for_node)

        seed_dicts.append(
            {
                "key": seed_key,
                "name": node_data.concept,
                "node_type": node_data.node_type,
                "entity_subtype": node_data.entity_subtype,
                "fact_count": fact_count,
            }
        )

        # Build seed-fact links
        for link in links_for_node:
            new_fact_id = fact_id_map.get(link.fact_id)
            if new_fact_id:
                seed_fact_links.append(
                    {
                        "seed_key": seed_key,
                        "fact_id": new_fact_id,
                        "confidence": link.relevance_score,
                        "extraction_context": None,
                    }
                )

    if not seed_dicts:
        return 0

    # Batch upsert seeds
    await seed_repo.upsert_seeds_batch(seed_dicts)

    # Link facts to seeds
    if seed_fact_links:
        await seed_repo.link_facts_batch(seed_fact_links)

    # Promote each seed (mark as promoted with the node key)
    from kt_db.keys import make_node_key

    for node_data in nodes:
        if node_data.id not in node_id_map:
            continue
        seed_key = make_seed_key(node_data.node_type, node_data.concept)
        node_key = make_node_key(node_data.node_type, node_data.concept)
        await seed_repo.promote_seed(seed_key, node_key)

    return len(seed_dicts)


# ── Helpers ──────────────────────────────────────────────────────────────


async def _find_existing_node(
    node_repo: NodeRepository,
    concept: str,
    embedding_service: EmbeddingService | None,
    qdrant_client: AsyncQdrantClient | None = None,
    pre_embedding: list[float] | None = None,
) -> Node | None:
    """Try to find an existing node by text search, then embedding fallback."""
    # Text search first
    text_matches = await node_repo.search_by_concept(concept, limit=5)
    for match in text_matches:
        if match.concept.lower() == concept.lower():
            return match

    # Embedding fallback — use pre-computed if available
    embedding = pre_embedding
    if embedding is None and embedding_service is not None:
        try:
            embedding = await embedding_service.embed_text(concept)
        except Exception as e:
            logger.warning("Embedding generation failed for concept %r: %s", concept, e)

    if embedding is not None and qdrant_client is not None:
        try:
            from kt_qdrant.repositories.nodes import QdrantNodeRepository

            qdrant_repo = QdrantNodeRepository(qdrant_client)
            results = await qdrant_repo.search_similar(
                embedding,
                limit=1,
                score_threshold=0.75,
            )
            if results:
                node = await node_repo.get_by_id(results[0].node_id)
                if node is not None:
                    return node
        except Exception as e:
            logger.warning("Embedding search failed for concept %r: %s", concept, e)

    return None


async def _create_or_get_source(
    source_info: FactSourceInfo,
    session: AsyncSession,
    write_session: AsyncSession | None = None,
) -> uuid.UUID:
    """Create or find a RawSource, dual-writing to graph-db and write-db.

    Synchronous import flow: graph-db must hold the row immediately so the
    FactSource junction insert in the same transaction can satisfy its FK.
    The deterministic id (from URI) means both writes land on the same pk,
    so worker-sync's later id-keyed upsert is a no-op.
    """
    from kt_db.keys import uri_to_source_id
    from kt_db.models import RawSource

    source_repo = SourceRepository(session)

    # Use real content_hash from export when available; otherwise synthetic
    content_hash = source_info.content_hash
    if not content_hash:
        content_hash = hashlib.sha256(f"import:{source_info.uri}".encode()).hexdigest()

    # Check if already exists by id (deterministic from URI)
    deterministic_id = uri_to_source_id(source_info.uri)
    existing = await source_repo.get_by_id(deterministic_id)
    if existing is not None:
        source_id = existing.id
    else:
        source = RawSource(
            id=deterministic_id,
            uri=source_info.uri,
            title=source_info.title,
            raw_content=source_info.raw_content or "",
            content_hash=content_hash,
            provider_id=source_info.provider_id,
            is_full_text=source_info.is_full_text,
            content_type=source_info.content_type,
            provider_metadata=source_info.provider_metadata,
        )
        try:
            async with session.begin_nested():
                session.add(source)
                await session.flush()
        except IntegrityError:
            # Concurrent insert won the race; re-fetch by id.
            existing = await source_repo.get_by_id(deterministic_id)
            if existing is None:
                raise
            source_id = existing.id
        else:
            source_id = source.id

    # Mirror into write-db so worker-sync's watermark advances and the
    # row is observable through the canonical store.
    if write_session is not None:
        from kt_db.repositories.write_sources import WriteSourceRepository

        write_source_repo = WriteSourceRepository(write_session)
        await write_source_repo.create_or_get(
            uri=source_info.uri,
            title=source_info.title,
            raw_content=source_info.raw_content,
            content_hash=content_hash,
            provider_id=source_info.provider_id,
            provider_metadata=source_info.provider_metadata,
        )

    return source_id
