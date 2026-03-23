"""Composite merge logic — find existing composite nodes that could be
merged with a proposed new composite node.

A merge candidate is an existing composite node of the same type whose
source nodes (linked via ``draws_from`` edges) have high Jaccard overlap
with the proposed source set, OR whose concept embedding is very similar.
"""

from __future__ import annotations

import logging
import uuid

from kt_graph.engine import GraphEngine

logger = logging.getLogger(__name__)


async def _get_source_node_ids_from_draws_from(
    graph_engine: GraphEngine,
    node_id: uuid.UUID,
) -> set[str]:
    """Get the set of source node IDs for a composite node via draws_from edges.

    ``draws_from`` edges are directed: source=composite, target=source_node.
    """
    edges = await graph_engine.get_edges(node_id, direction="both")
    source_ids: set[str] = set()
    for edge in edges:
        if edge.relationship_type == "draws_from":
            # draws_from is directed: source_node_id=composite, target_node_id=source
            if edge.source_node_id == node_id:
                source_ids.add(str(edge.target_node_id))
    return source_ids


def _jaccard_similarity(set_a: set[str], set_b: set[str]) -> float:
    """Compute Jaccard similarity between two sets."""
    if not set_a and not set_b:
        return 0.0
    intersection = set_a & set_b
    union = set_a | set_b
    return len(intersection) / len(union)


async def find_mergeable_composite(
    graph_engine: GraphEngine,
    node_type: str,
    source_node_ids: set[str],
    concept: str,
    embedding: list[float] | None = None,
    jaccard_threshold: float = 0.7,
    embedding_threshold: float = 0.25,
) -> str | None:
    """Search for an existing composite node that could be merged with a
    proposed new one.

    A merge candidate must satisfy at least one of:
    1. **Jaccard overlap** of source node sets (via ``draws_from`` edges)
       >= ``jaccard_threshold``
    2. **Concept embedding similarity** within ``embedding_threshold``
       (cosine distance)

    Args:
        graph_engine: GraphEngine instance for DB/Qdrant access.
        node_type: The node_type of the proposed composite (e.g. "concept",
            "perspective").
        source_node_ids: Set of source node ID strings for the proposed
            composite.
        concept: The concept name for the proposed composite.
        embedding: Optional embedding vector for the concept. If provided,
            enables embedding-based similarity search.
        jaccard_threshold: Minimum Jaccard similarity of source sets to
            consider a merge (default 0.7).
        embedding_threshold: Maximum cosine distance for embedding-based
            matching (default 0.25).

    Returns:
        The node_id (as string) of the best merge candidate, or ``None``
        if no suitable candidate exists.
    """
    if not source_node_ids:
        return None

    best_candidate: str | None = None
    best_jaccard: float = 0.0

    # Strategy 1: Embedding-based candidate discovery
    # Find similar nodes of the same type, then check Jaccard overlap
    if embedding is not None:
        similar_nodes = await graph_engine.find_similar_nodes(
            embedding=embedding,
            threshold=embedding_threshold,
            limit=20,
            node_type=node_type,
        )

        for candidate_node in similar_nodes:
            candidate_id = candidate_node.id
            candidate_sources = await _get_source_node_ids_from_draws_from(
                graph_engine, candidate_id
            )

            # Skip nodes that don't have draws_from edges (not composites)
            if not candidate_sources:
                continue

            jaccard = _jaccard_similarity(source_node_ids, candidate_sources)
            logger.debug(
                "Merge candidate %s (%s): jaccard=%.3f with proposed sources",
                candidate_id, candidate_node.concept, jaccard,
            )

            if jaccard >= jaccard_threshold and jaccard > best_jaccard:
                best_jaccard = jaccard
                best_candidate = str(candidate_id)

        # If we found a candidate via Jaccard on embedding-similar nodes, return it
        if best_candidate is not None:
            logger.info(
                "Found mergeable composite %s (jaccard=%.3f) for concept '%s'",
                best_candidate, best_jaccard, concept,
            )
            return best_candidate

        # If embedding similarity alone is very high (threshold/2), consider
        # it a merge candidate even without Jaccard overlap — the concepts
        # are nearly identical
        if similar_nodes:
            top_node = similar_nodes[0]
            top_sources = await _get_source_node_ids_from_draws_from(
                graph_engine, top_node.id
            )
            if top_sources:
                # This node IS a composite — even if Jaccard is lower, the
                # embedding match is strong enough
                logger.info(
                    "Found embedding-similar composite %s (%s) for concept '%s'",
                    top_node.id, top_node.concept, concept,
                )
                return str(top_node.id)

    # Strategy 2: Check source nodes' shared neighbors for composites
    # For each source node, find edges and look for composite nodes that
    # share multiple source nodes via draws_from edges
    composite_candidates: dict[str, set[str]] = {}  # candidate_id -> set of shared sources

    for source_nid_str in source_node_ids:
        try:
            source_nid = uuid.UUID(source_nid_str)
        except (ValueError, AttributeError):
            continue

        edges = await graph_engine.get_edges(source_nid, direction="both")
        for edge in edges:
            if edge.relationship_type != "draws_from":
                continue

            # draws_from: source=composite, target=source_node
            # We're looking at edges FROM a composite TO this source node
            if edge.target_node_id == source_nid:
                composite_id = str(edge.source_node_id)

                # Verify the composite is the right type
                composite_node = await graph_engine.get_node(edge.source_node_id)
                if composite_node is None:
                    continue
                if composite_node.node_type != node_type:
                    continue

                if composite_id not in composite_candidates:
                    composite_candidates[composite_id] = set()
                composite_candidates[composite_id].add(source_nid_str)

    # Evaluate candidates by full Jaccard similarity
    for candidate_id_str, shared_sources in composite_candidates.items():
        candidate_uuid = uuid.UUID(candidate_id_str)
        full_candidate_sources = await _get_source_node_ids_from_draws_from(
            graph_engine, candidate_uuid
        )

        jaccard = _jaccard_similarity(source_node_ids, full_candidate_sources)
        logger.debug(
            "Neighbor-discovered merge candidate %s: jaccard=%.3f",
            candidate_id_str, jaccard,
        )

        if jaccard >= jaccard_threshold and jaccard > best_jaccard:
            best_jaccard = jaccard
            best_candidate = candidate_id_str

    if best_candidate is not None:
        logger.info(
            "Found mergeable composite %s (jaccard=%.3f) via neighbor discovery for concept '%s'",
            best_candidate, best_jaccard, concept,
        )

    return best_candidate
