"""Post-synthesis document processing pipeline.

Splits synthesis text into sentences, embeds them, links nodes by text match,
links facts by embedding similarity, and stores everything in the database.
"""

from __future__ import annotations

import logging
import re
import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from kt_db.repositories.synthesis_documents import SynthesisDocumentRepository
from kt_models.embeddings import EmbeddingService

logger = logging.getLogger(__name__)


# ── Sentence splitting ─────────────────────────────────────────────


def split_into_sentences(text: str) -> list[str]:
    """Split markdown text into sentences.

    Uses a regex-based approach that handles common abbreviations and
    markdown formatting. Returns non-empty sentences.
    """
    # Remove markdown headings but keep their text
    text = re.sub(r"^#{1,6}\s+", "", text, flags=re.MULTILINE)

    # Split on sentence-ending punctuation followed by whitespace or newline
    # Handles: ., !, ? followed by space/newline, but not abbreviations like "Dr." "U.S."
    raw = re.split(r"(?<=[.!?])\s+(?=[A-Z\[\"\'])", text)

    sentences = []
    for s in raw:
        s = s.strip()
        if s and len(s) > 5:  # skip very short fragments
            sentences.append(s)

    return sentences


# ── Node linking by text match ─────────────────────────────────────


def link_nodes_by_text(
    sentences: list[str],
    node_names_and_aliases: dict[str, list[str]],
) -> dict[int, list[tuple[str, str]]]:
    """Match node names/aliases against each sentence.

    Args:
        sentences: List of sentence texts.
        node_names_and_aliases: {node_id: [name, alias1, alias2, ...]}

    Returns:
        {sentence_index: [(node_id, link_type), ...]}
    """
    links: dict[int, list[tuple[str, str]]] = {}

    for i, sentence in enumerate(sentences):
        lower = sentence.lower()
        for node_id, names in node_names_and_aliases.items():
            for name in names:
                if name.lower() in lower:
                    if i not in links:
                        links[i] = []
                    link_type = "name_match" if name == names[0] else "alias_match"
                    links[i].append((node_id, link_type))
                    break  # one match per node per sentence is enough

    # Also parse existing markdown node links: [text](/nodes/<uuid>)
    node_link_pattern = re.compile(r"\[([^\]]+)\]\(/nodes/([a-f0-9-]+)\)")
    for i, sentence in enumerate(sentences):
        for match in node_link_pattern.finditer(sentence):
            node_id = match.group(2)
            if i not in links:
                links[i] = []
            if not any(nid == node_id for nid, _ in links[i]):
                links[i].append((node_id, "name_match"))

    return links


# ── Fact linking by embedding similarity ───────────────────────────


async def link_facts_by_embedding(
    sentence_embeddings: list[list[float]],
    referenced_node_ids: list[str],
    qdrant_client: object,
    top_k: int = 5,
) -> dict[int, list[tuple[str, float]]]:
    """For each sentence, find the closest facts from referenced nodes.

    Args:
        sentence_embeddings: Embeddings for each sentence.
        referenced_node_ids: Node IDs whose facts to search against.
        qdrant_client: Qdrant client instance.
        top_k: Number of closest facts per sentence.

    Returns:
        {sentence_index: [(fact_id, distance), ...]}
    """
    from kt_qdrant.repositories.facts import QdrantFactRepository

    fact_repo = QdrantFactRepository(qdrant_client)
    links: dict[int, list[tuple[str, float]]] = {}

    for i, embedding in enumerate(sentence_embeddings):
        try:
            results = await fact_repo.search_similar(
                embedding,
                limit=top_k,
                node_ids=referenced_node_ids if referenced_node_ids else None,
            )
            if results:
                links[i] = [(str(r.id), r.score) for r in results]
        except Exception:
            logger.warning("Fact embedding search failed for sentence %d", i, exc_info=True)

    return links


# ── Full pipeline ──────────────────────────────────────────────────


async def process_synthesis_document(
    synthesis_node_id: uuid.UUID,
    synthesis_text: str,
    session: AsyncSession,
    embedding_service: EmbeddingService,
    qdrant_client: object | None,
    node_names_and_aliases: dict[str, list[str]] | None = None,
) -> dict[str, Any]:
    """Run the full document processing pipeline.

    1. Split text into sentences
    2. Embed sentences
    3. Link nodes by text match
    4. Link facts by embedding similarity
    5. Store everything in the database

    Returns stats dict.
    """
    repo = SynthesisDocumentRepository(session)

    # 1. Split
    sentences = split_into_sentences(synthesis_text)
    if not sentences:
        logger.warning("No sentences extracted from synthesis text")
        return {"sentences_count": 0, "facts_linked": 0, "nodes_referenced": 0}

    # 2. Create sentence records
    sentence_records = await repo.bulk_create_sentences(synthesis_node_id, sentences)

    # 3. Embed sentences
    sentence_embeddings: list[list[float]] = []
    if embedding_service:
        try:
            sentence_embeddings = await embedding_service.embed_batch(sentences)
        except Exception:
            logger.warning("Failed to embed sentences", exc_info=True)

    # 4. Link nodes by text
    node_links_map: dict[int, list[tuple[str, str]]] = {}
    if node_names_and_aliases:
        node_links_map = link_nodes_by_text(sentences, node_names_and_aliases)

    # Store node links
    node_link_tuples: list[tuple[uuid.UUID, uuid.UUID, str]] = []
    all_referenced_node_ids: set[str] = set()
    for sent_idx, node_links in node_links_map.items():
        if sent_idx < len(sentence_records):
            for node_id, link_type in node_links:
                try:
                    node_uuid = uuid.UUID(node_id)
                    node_link_tuples.append((sentence_records[sent_idx].id, node_uuid, link_type))
                    all_referenced_node_ids.add(node_id)
                except ValueError:
                    pass
    if node_link_tuples:
        await repo.bulk_link_sentence_nodes(node_link_tuples)

    # 5. Link facts by embedding
    fact_link_count = 0
    if sentence_embeddings and qdrant_client:
        fact_links_map = await link_facts_by_embedding(
            sentence_embeddings,
            list(all_referenced_node_ids),
            qdrant_client,
        )
        fact_link_tuples: list[tuple[uuid.UUID, uuid.UUID, float]] = []
        for sent_idx, fact_links in fact_links_map.items():
            if sent_idx < len(sentence_records):
                for fact_id, distance in fact_links:
                    try:
                        fact_link_tuples.append((
                            sentence_records[sent_idx].id,
                            uuid.UUID(fact_id),
                            distance,
                        ))
                    except ValueError:
                        pass
        if fact_link_tuples:
            await repo.bulk_link_sentence_facts(fact_link_tuples)
            fact_link_count = len(fact_link_tuples)

    await session.flush()

    return {
        "sentences_count": len(sentences),
        "facts_linked": fact_link_count,
        "nodes_referenced": len(all_referenced_node_ids),
    }
