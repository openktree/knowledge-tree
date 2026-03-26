"""Post-synthesis document processing pipeline.

Splits synthesis text into sentences, embeds them, links nodes by text match,
links facts by embedding similarity, and returns a JSON-serializable document
structure to be stored in the node's metadata field.

This follows the project's dual-database architecture: the synthesis workflow
writes to write-db (via GraphEngine), and the sync worker propagates to
graph-db. No separate tables are needed — the document lives in metadata_.
"""

from __future__ import annotations

import logging
import re
from typing import Any

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
    raw = re.split(r"(?<=[.!?])\s+(?=[A-Z\[\"\'])", text)

    sentences = []
    for s in raw:
        s = s.strip()
        if s and len(s) > 5:
            sentences.append(s)

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
    referenced_node_ids: list[str],
    qdrant_client: object,
    top_k: int = 5,
) -> dict[int, list[tuple[str, float]]]:
    """For each sentence, find the closest facts from referenced nodes.

    Returns {sentence_index: [(fact_id, distance), ...]}
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
            "stats": {"sentences_count": 0, "facts_linked": 0, "nodes_referenced": 0},
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
            list(all_referenced_node_ids),
            qdrant_client,
        )
        total_fact_links = sum(len(fl) for fl in fact_links_map.values())

    # 5. Build JSON document
    sentence_records = []
    for i, text in enumerate(sentences):
        node_ids = [nid for nid, _ in node_links_map.get(i, [])]
        fact_links = [
            {"fact_id": fid, "distance": round(dist, 4)}
            for fid, dist in fact_links_map.get(i, [])
        ]
        sentence_records.append({
            "text": text,
            "position": i,
            "fact_links": fact_links,
            "node_ids": node_ids,
        })

    # Build referenced nodes list
    referenced_nodes = []
    if node_names_and_aliases:
        for node_id in all_referenced_node_ids:
            names = node_names_and_aliases.get(node_id, [])
            referenced_nodes.append({
                "node_id": node_id,
                "concept": names[0] if names else "unknown",
            })

    return {
        "sentences": sentence_records,
        "referenced_nodes": referenced_nodes,
        "stats": {
            "sentences_count": len(sentences),
            "facts_linked": total_fact_links,
            "nodes_referenced": len(all_referenced_node_ids),
        },
    }
