"""Post-synthesis document processing pipeline.

Splits synthesis text into sentences, embeds them, links nodes by text match,
links facts by embedding similarity, and returns a JSON-serializable document
structure to be stored in the node's metadata field.

This follows the project's dual-database architecture: the synthesis workflow
writes to write-db (via GraphEngine), and the sync worker propagates to
graph-db. No separate tables are needed — the document lives in metadata_.
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

from kt_models.embeddings import EmbeddingService

logger = logging.getLogger(__name__)


# ── Sentence splitting ─────────────────────────────────────────────


def split_into_sentences(text: str) -> list[str]:
    """Split markdown text into sentences.

    Handles:
    - Regular sentence boundaries (. ! ? followed by space + capital)
    - Markdown headings (# ## ### etc)
    - List items (1. 2. - * at start of line)
    - Horizontal rules (---)

    Each list item becomes its own sentence for better embedding quality.
    """
    lines = text.split("\n")
    blocks: list[str] = []
    current: list[str] = []

    def flush_current() -> None:
        if current:
            merged = " ".join(current).strip()
            if merged:
                blocks.append(merged)
            current.clear()

    for line in lines:
        stripped = line.strip()

        # Skip empty lines — they separate blocks
        if not stripped:
            flush_current()
            continue

        # Horizontal rules
        if re.match(r"^-{3,}$", stripped):
            flush_current()
            continue

        # Headings — become their own sentence
        heading_match = re.match(r"^#{1,6}\s+(.*)", stripped)
        if heading_match:
            flush_current()
            heading_text = heading_match.group(1).strip()
            if heading_text and len(heading_text) > 3:
                blocks.append(heading_text)
            continue

        # List items — each becomes its own sentence
        list_match = re.match(r"^(?:\d+\.\s+|[-*]\s+)(.*)", stripped)
        if list_match:
            flush_current()
            item_text = list_match.group(1).strip()
            if item_text and len(item_text) > 5:
                blocks.append(item_text)
            continue

        # Regular text — accumulate
        current.append(stripped)

    flush_current()

    # Now split accumulated blocks on sentence boundaries
    sentences: list[str] = []
    for block in blocks:
        # Split on sentence-ending punctuation followed by space + capital letter
        parts = re.split(r"(?<=[.!?])\s+(?=[A-Z\[\"\'])", block)
        for part in parts:
            part = part.strip()
            if part and len(part) > 5:
                sentences.append(part)

    return sentences


# ── Node linking by text match ─────────────────────────────────────


def link_nodes_by_text(
    sentences: list[str],
    node_names_and_aliases: dict[str, list[str]],
) -> dict[int, list[tuple[str, str]]]:
    """Match node names/aliases against each sentence.

    Returns {sentence_index: [(node_id, link_type), ...]}
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
                    break

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
    qdrant_client: object,
    referenced_node_ids: list[str] | None = None,
    top_k: int = 5,
) -> dict[int, list[tuple[str, float]]]:
    """For each sentence, find the closest facts by embedding similarity.

    Uses Qdrant's MatchAny filter on the node_ids payload field to only
    search facts linked to the visited nodes. This is fast and avoids
    showing irrelevant facts the synthesis agent never saw.

    Returns {sentence_index: [(fact_id, distance), ...]}
    """
    from kt_qdrant.repositories.facts import QdrantFactRepository

    fact_repo = QdrantFactRepository(qdrant_client)
    semaphore = asyncio.Semaphore(10)

    async def _search_one(i: int, embedding: list[float]) -> tuple[int, list[tuple[str, float]] | None]:
        async with semaphore:
            try:
                results = await fact_repo.search_similar(
                    embedding,
                    limit=top_k,
                    score_threshold=0.6,
                    node_ids=referenced_node_ids,
                )
                if results:
                    return i, [(str(r.fact_id), r.score) for r in results]
                return i, None
            except Exception:
                logger.warning("Fact embedding search failed for sentence %d", i, exc_info=True)
                return i, None

    results = await asyncio.gather(*[_search_one(i, emb) for i, emb in enumerate(sentence_embeddings)])
    return {i: r for i, r in results if r is not None}


# ── Full pipeline ──────────────────────────────────────────────────


async def process_synthesis_document(
    synthesis_text: str,
    embedding_service: EmbeddingService | None,
    qdrant_client: object | None,
    node_names_and_aliases: dict[str, list[str]] | None = None,
) -> dict[str, Any]:
    """Run the full document processing pipeline.

    Returns a JSON-serializable dict to be stored in the node's metadata_
    under the key "synthesis_document".

    Structure:
    {
        "sentences": [
            {"text": "...", "position": 0, "fact_links": [...], "node_ids": [...]}
        ],
        "referenced_nodes": [{"node_id": "...", "concept": "...", "node_type": "..."}],
        "stats": {"sentences_count": N, "facts_linked": N, "nodes_referenced": N}
    }
    """
    # 1. Split
    sentences = split_into_sentences(synthesis_text)
    if not sentences:
        logger.warning("No sentences extracted from synthesis text")
        return {
            "sentences": [],
            "referenced_nodes": [],
            "stats": {
                "sentences_count": 0,
                "facts_linked": 0,
                "nodes_referenced": 0,
            },
        }

    # 2. Embed sentences
    sentence_embeddings: list[list[float]] = []
    if embedding_service:
        try:
            sentence_embeddings = await embedding_service.embed_batch(sentences)
        except Exception:
            logger.warning("Failed to embed sentences", exc_info=True)

    # 3. Link nodes by text
    node_links_map: dict[int, list[tuple[str, str]]] = {}
    if node_names_and_aliases:
        node_links_map = link_nodes_by_text(sentences, node_names_and_aliases)

    all_referenced_node_ids: set[str] = set()
    for node_links in node_links_map.values():
        for node_id, _ in node_links:
            all_referenced_node_ids.add(node_id)

    # 4. Link facts by embedding
    fact_links_map: dict[int, list[tuple[str, float]]] = {}
    total_fact_links = 0
    if sentence_embeddings and qdrant_client:
        fact_links_map = await link_facts_by_embedding(
            sentence_embeddings,
            qdrant_client,
            referenced_node_ids=list(all_referenced_node_ids) if all_referenced_node_ids else None,
        )
        total_fact_links = sum(len(fl) for fl in fact_links_map.values())

    # 5. Build JSON document
    sentence_records = []
    for i, text in enumerate(sentences):
        node_ids = [nid for nid, _ in node_links_map.get(i, [])]
        fact_links = [{"fact_id": fid, "distance": round(dist, 4)} for fid, dist in fact_links_map.get(i, [])]
        sentence_records.append(
            {
                "text": text,
                "position": i,
                "fact_links": fact_links,
                "node_ids": node_ids,
            }
        )

    # Build referenced nodes list
    referenced_nodes = []
    if node_names_and_aliases:
        for node_id in all_referenced_node_ids:
            names = node_names_and_aliases.get(node_id, [])
            referenced_nodes.append(
                {
                    "node_id": node_id,
                    "concept": names[0] if names else "unknown",
                }
            )

    return {
        "sentences": sentence_records,
        "referenced_nodes": referenced_nodes,
        "stats": {
            "sentences_count": len(sentences),
            "facts_linked": total_fact_links,
            "nodes_referenced": len(all_referenced_node_ids),
        },
    }
