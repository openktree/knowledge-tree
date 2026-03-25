"""Tool: scout — Lightweight reconnaissance of external sources and existing graph.

Returns titles/snippets from external search AND existing graph matches with
richness scores and staleness info. Costs 0 explore_budget.
"""

from __future__ import annotations

import logging
from typing import Any

from kt_agents_core.state import AgentContext

logger = logging.getLogger(__name__)


async def scout_impl(queries: list[str], ctx: AgentContext) -> dict[str, Any]:
    """Lightweight external search + graph scan. No decomposition, no storage.

    For each query:
    - Calls provider_registry.search_all() for external results (titles/snippets only)
    - Calls graph_engine.search_nodes() and find_similar_nodes() for internal matches

    External searches and embedding computations are batched for efficiency.

    Returns dict with external snippets AND existing graph matches with
    richness scores and staleness info.
    """
    results: dict[str, Any] = {}

    # Batch external searches — all queries in parallel
    try:
        external_by_query = await ctx.provider_registry.search_all(queries, max_results=5)
    except Exception:
        logger.exception("Scout: batch external search failed")
        external_by_query = {q: [] for q in queries}

    # Batch embedding computation — all queries at once
    embeddings_by_query: dict[str, list[float]] = {}
    if ctx.embedding_service:
        try:
            all_embeddings = await ctx.embedding_service.embed_batch(queries)
            for q, emb in zip(queries, all_embeddings):
                embeddings_by_query[q] = emb
        except Exception:
            logger.warning(
                "Scout: batch embedding failed — Tier 2 (semantic) graph search disabled for this call", exc_info=True
            )
    else:
        logger.warning("Scout: no embedding service available — Tier 2 (semantic) graph search disabled")

    for query in queries:
        entry: dict[str, Any] = {"external": [], "graph_matches": []}

        # External search — titles/snippets only (from pre-fetched results)
        raw_results = external_by_query.get(query, [])
        for r in raw_results:
            snippet = r.raw_content[:300] if r.raw_content else ""
            entry["external"].append(
                {
                    "title": r.title,
                    "snippet": snippet,
                    "url": r.uri,
                }
            )

        # Internal graph search — text + embedding (sequential, uses DB session)
        try:
            text_matches = await ctx.graph_engine.search_nodes(query, limit=5)
            seen_ids: set[str] = set()

            for node in text_matches:
                nid = str(node.id)
                if nid in seen_ids:
                    continue
                seen_ids.add(nid)

                facts = await ctx.graph_engine.get_node_facts(node.id)
                dims = await ctx.graph_engine.get_dimensions(node.id)
                richness = ctx.graph_engine.compute_richness(node, len(facts), len(dims))
                is_stale = ctx.graph_engine.is_node_stale(node)

                match_info: dict[str, Any] = {
                    "node_id": nid,
                    "concept": node.concept,
                    "node_type": node.node_type,
                    "fact_count": len(facts),
                    "richness": round(richness, 2),
                    "is_stale": is_stale,
                }
                if node.parent_id:
                    match_info["parent_id"] = str(node.parent_id)

                entry["graph_matches"].append(match_info)

            # Tier 2: embedding search (using pre-computed embedding)
            embedding = embeddings_by_query.get(query)
            if embedding:
                try:
                    similar = await ctx.graph_engine.find_similar_nodes(embedding, threshold=0.4, limit=5)
                    for node in similar:
                        nid = str(node.id)
                        if nid in seen_ids:
                            continue
                        seen_ids.add(nid)

                        facts = await ctx.graph_engine.get_node_facts(node.id)
                        dims = await ctx.graph_engine.get_dimensions(node.id)
                        richness = ctx.graph_engine.compute_richness(node, len(facts), len(dims))
                        is_stale = ctx.graph_engine.is_node_stale(node)

                        match_info = {
                            "node_id": nid,
                            "concept": node.concept,
                            "node_type": node.node_type,
                            "fact_count": len(facts),
                            "richness": round(richness, 2),
                            "is_stale": is_stale,
                        }
                        if node.parent_id:
                            match_info["parent_id"] = str(node.parent_id)

                        entry["graph_matches"].append(match_info)
                except Exception:
                    logger.exception("Scout: embedding search failed for '%s'", query)
        except Exception:
            logger.exception("Scout: graph search failed for '%s'", query)

        results[query] = entry

    return results
