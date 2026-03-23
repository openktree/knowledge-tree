"""Multi-vector semantic expansion for edge candidate discovery.

Finds edge candidates that pure node-embedding misses by searching with
MULTIPLE vectors derived from the node's facts, dimensions, and
suggested_concepts. Each term is embedded individually (embedding is cheap)
and searched separately to maximize specificity.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field

from kt_agents_core.state import AgentContext
from kt_config.settings import get_settings

logger = logging.getLogger(__name__)


@dataclass
class CandidateNode:
    """A candidate node discovered via semantic expansion."""

    node_id: uuid.UUID
    concept: str
    node_type: str
    match_count: int = 1
    max_similarity: float = 0.0
    match_sources: list[str] = field(default_factory=list)


async def expand_candidates(
    node_id: uuid.UUID,
    ctx: AgentContext,
) -> list[CandidateNode]:
    """Find edge candidates using multi-vector semantic expansion.

    For a node:
    1. Extract suggested_concepts from dimensions
    2. Extract key terms from facts (important phrases, entity names)
    3. Embed each term individually
    4. Search for nodes matching each term embedding separately
    5. Cross-match at the fact level
    6. Aggregate and rank candidates

    Args:
        node_id: The node to find candidates for.
        ctx: Agent context with graph_engine and embedding_service.

    Returns:
        List of CandidateNode sorted by match_count + similarity.
    """
    settings = get_settings()
    if not settings.enable_semantic_expansion:
        return []

    if ctx.embedding_service is None:
        return []

    node = await ctx.graph_engine.get_node(node_id)
    if node is None:
        return []

    max_terms = settings.semantic_expansion_max_terms
    fact_threshold = settings.semantic_expansion_fact_threshold

    # Collect search terms
    terms: list[str] = []
    seen_terms: set[str] = set()

    # 1. Extract suggested_concepts from dimensions
    dims = await ctx.graph_engine.get_dimensions(node_id)
    for dim in dims:
        for sc in dim.suggested_concepts or []:
            key = sc.lower().strip()
            if key and key not in seen_terms and key != node.concept.lower().strip():
                seen_terms.add(key)
                terms.append(sc)

    # 2. Extract key terms from facts (content fragments)
    facts = await ctx.graph_engine.get_node_facts(node_id)
    for fact in facts[:10]:  # Cap facts we scan
        # Extract quoted terms and capitalized phrases
        content = fact.content
        # Simple heuristic: look for quoted strings or capitalized multi-word phrases
        words = content.split()
        for i, word in enumerate(words):
            # Capitalized words that aren't at sentence start
            if i > 0 and word[0:1].isupper() and len(word) > 2:
                key = word.lower().strip(".,;:\"'()")
                if key and key not in seen_terms and len(key) > 2:
                    seen_terms.add(key)
                    terms.append(word.strip(".,;:\"'()"))

    # Cap total terms
    terms = terms[:max_terms]

    if not terms:
        return []

    logger.info(
        "semantic_expansion for '%s': %d terms to search",
        node.concept,
        len(terms),
    )

    # 3. Embed each term individually and search
    candidates: dict[uuid.UUID, CandidateNode] = {}

    for term in terms:
        try:
            term_embedding = await ctx.embedding_service.embed_text(term)
            similar = await ctx.graph_engine.find_similar_nodes(
                term_embedding,
                threshold=0.35,
                limit=5,
            )
            for s in similar:
                if s.id == node_id:
                    continue
                if s.id in candidates:
                    candidates[s.id].match_count += 1
                    candidates[s.id].match_sources.append(f"term:{term}")
                else:
                    candidates[s.id] = CandidateNode(
                        node_id=s.id,
                        concept=s.concept,
                        node_type=s.node_type,
                        match_count=1,
                        match_sources=[f"term:{term}"],
                    )
        except Exception:
            logger.debug("Embedding search failed for term '%s'", term, exc_info=True)

    # 4. Cross-node fact matching
    fact_embeddings = [f.embedding for f in facts if f.embedding is not None]
    if fact_embeddings:
        try:
            # Cap embeddings to avoid too many queries
            capped_embeddings = fact_embeddings[:10]
            fact_matches = await ctx.graph_engine.find_nodes_with_similar_facts(
                capped_embeddings,
                exclude_node_id=node_id,
                threshold=fact_threshold,
                limit=10,
            )
            for match_node_id, match_count in fact_matches:
                if match_node_id in candidates:
                    candidates[match_node_id].match_count += match_count
                    candidates[match_node_id].match_sources.append(f"fact_match:{match_count}")
                else:
                    match_node = await ctx.graph_engine.get_node(match_node_id)
                    if match_node:
                        candidates[match_node_id] = CandidateNode(
                            node_id=match_node_id,
                            concept=match_node.concept,
                            node_type=match_node.node_type,
                            match_count=match_count,
                            match_sources=[f"fact_match:{match_count}"],
                        )
        except Exception:
            logger.debug("Fact-level matching failed for node %s", node_id, exc_info=True)

    # Sort by match_count descending
    sorted_candidates = sorted(
        candidates.values(),
        key=lambda c: c.match_count,
        reverse=True,
    )

    logger.info(
        "semantic_expansion for '%s': %d candidates found from %d terms",
        node.concept,
        len(sorted_candidates),
        len(terms),
    )

    return sorted_candidates[:20]  # Cap at 20 candidates
