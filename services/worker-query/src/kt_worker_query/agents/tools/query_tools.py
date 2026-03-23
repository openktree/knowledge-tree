"""Tool factory for the Query Agent — read-only graph navigation tools.

Creates 7 lightweight tools: search_graph, read_node, read_nodes,
get_node_facts, get_node_facts_batch, get_budget, hide_nodes.

No external API calls, no node/edge creation, no enrichment.
"""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import Callable
from typing import Any

from langchain_core.tools import BaseTool, tool

from kt_worker_query.agents.query_agent_state import QueryAgentState
from kt_agents_core.state import AgentContext

logger = logging.getLogger(__name__)

MAX_FACTS_RETURNED = 20
MAX_FACT_CONTENT_LEN = 200
MAX_DIMENSION_CONTENT_LEN = 500
MAX_BATCH_SIZE = 10


# ── Lightweight implementations ──────────────────────────────────────


DEFAULT_SEARCH_LIMIT = 20
MAX_SEARCH_LIMIT = 100


async def lightweight_search_nodes(
    queries: list[str],
    ctx: AgentContext,
    limit: int = DEFAULT_SEARCH_LIMIT,
) -> dict[str, Any]:
    """Graph-only search: text + embedding similarity. No external API calls."""
    limit = max(1, min(limit, MAX_SEARCH_LIMIT))
    results: dict[str, Any] = {}

    # Batch embedding computation
    embeddings_by_query: dict[str, list[float]] = {}
    if ctx.embedding_service:
        try:
            all_embeddings = await ctx.embedding_service.embed_batch(queries)
            for q, emb in zip(queries, all_embeddings):
                embeddings_by_query[q] = emb
        except Exception:
            logger.exception("Query search: batch embedding failed")

    for query in queries:
        matches: list[dict[str, Any]] = []
        seen_ids: set[str] = set()

        try:
            text_matches = await ctx.graph_engine.search_nodes(query, limit=limit)
            for node in text_matches:
                nid = str(node.id)
                if nid in seen_ids:
                    continue
                seen_ids.add(nid)

                facts = await ctx.graph_engine.get_node_facts(node.id)
                dims = await ctx.graph_engine.get_dimensions(node.id)
                richness = ctx.graph_engine.compute_richness(node, len(facts), len(dims))

                match_info: dict[str, Any] = {
                    "node_id": nid,
                    "concept": node.concept,
                    "node_type": node.node_type,
                    "fact_count": len(facts),
                    "richness": round(richness, 2),
                }
                if node.parent_id:
                    match_info["parent_id"] = str(node.parent_id)
                matches.append(match_info)

            # Embedding search
            embedding = embeddings_by_query.get(query)
            if embedding:
                try:
                    similar = await ctx.graph_engine.find_similar_nodes(
                        embedding, threshold=0.4, limit=limit
                    )
                    for node in similar:
                        nid = str(node.id)
                        if nid in seen_ids:
                            continue
                        seen_ids.add(nid)

                        facts = await ctx.graph_engine.get_node_facts(node.id)
                        dims = await ctx.graph_engine.get_dimensions(node.id)
                        richness = ctx.graph_engine.compute_richness(
                            node, len(facts), len(dims)
                        )

                        match_info = {
                            "node_id": nid,
                            "concept": node.concept,
                            "node_type": node.node_type,
                            "fact_count": len(facts),
                            "richness": round(richness, 2),
                        }
                        if node.parent_id:
                            match_info["parent_id"] = str(node.parent_id)
                        matches.append(match_info)
                except Exception:
                    logger.exception("Query search: embedding search failed for '%s'", query)
        except Exception:
            logger.exception("Query search: graph search failed for '%s'", query)

        results[query] = {"graph_matches": matches}

    return results


async def lightweight_read_node(
    node_id: str,
    ctx: AgentContext,
    state: QueryAgentState,
) -> dict[str, Any]:
    """Pure read: fetch node, dimensions, edges, fact count.

    Read-only — no enrichment, no relation building, no access count tracking.
    Still emits graph events (node_visited, edge_created) for rendering.
    """
    try:
        nid = uuid.UUID(node_id)
    except (ValueError, AttributeError):
        return {"error": f"Invalid node_id: '{node_id}' is not a valid UUID."}

    node = await ctx.graph_engine.get_node(nid)
    if node is None:
        return {"error": f"Node not found: {node_id}"}

    # Budget check
    already_visited = state.has_visited(node_id)
    if not already_visited:
        if state.nav_remaining <= 0:
            return {
                "error": "Nav budget exhausted — cannot read unvisited node.",
                "nav_remaining": 0,
            }
        state.visited_nodes.append(node_id)
        state.nav_used += 1
        budget_cost = 1
    else:
        budget_cost = 0

    nav_remaining = state.nav_remaining

    await ctx.emit("activity_log", action=f"Reading node: '{node.concept}'", tool="query_agent")
    await ctx.emit("node_visited", data={
        "id": node_id,
        "concept": node.concept,
        "node_type": node.node_type,
    })
    await ctx.emit("budget_update", data={
        "nav_remaining": nav_remaining,
        "nav_total": state.nav_budget,
        "explore_remaining": 0,
        "explore_total": 0,
    })

    # Fetch dimensions
    dimensions = await ctx.graph_engine.get_dimensions(nid)

    # Fetch edges (both directions)
    edges = await ctx.graph_engine.get_edges(nid, direction="both")

    edge_list: list[dict[str, Any]] = []
    for edge in edges:
        if edge.source_node_id == nid:
            target_id = edge.target_node_id
            direction = "outgoing"
        else:
            target_id = edge.source_node_id
            direction = "incoming"

        target_node = await ctx.graph_engine.get_node(target_id)
        target_concept = target_node.concept if target_node else "unknown"

        # Emit connected node for frontend graph
        if target_node is not None:
            await ctx.emit("node_visited", data={
                "id": str(target_id),
                "concept": target_concept,
                "node_type": getattr(target_node, "node_type", "concept"),
            })

        # Emit edge for frontend graph
        await ctx.emit("edge_created", data={
            "id": str(edge.id),
            "source_node_id": str(edge.source_node_id),
            "target_node_id": str(edge.target_node_id),
            "relationship_type": edge.relationship_type,
            "weight": edge.weight,
            "justification": edge.justification,
        })

        edge_list.append({
            "target_node_id": str(target_id),
            "target_concept": target_concept,
            "relationship_type": edge.relationship_type,
            "weight": edge.weight,
            "direction": direction,
        })

    # Fact count
    facts = await ctx.graph_engine.get_node_facts(nid)
    fact_count = len(facts)

    # Richness
    richness = ctx.graph_engine.compute_richness(node, fact_count, len(dimensions))

    # Dimension list (truncated)
    dim_list: list[dict[str, Any]] = []
    for dim in dimensions:
        content = dim.content
        if len(content) > MAX_DIMENSION_CONTENT_LEN:
            content = content[:MAX_DIMENSION_CONTENT_LEN] + "..."
        dim_list.append({
            "model_id": dim.model_id,
            "content": content,
            "confidence": dim.confidence,
            "suggested_concepts": dim.suggested_concepts or [],
        })

    result: dict[str, Any] = {
        "node_id": node_id,
        "concept": node.concept,
        "node_type": node.node_type,
        "fact_count": fact_count,
        "richness": richness,
        "budget_cost": budget_cost,
        "nav_remaining": nav_remaining,
        "dimensions": dim_list,
        "edges": edge_list,
    }

    return result


async def lightweight_get_node_facts(
    node_id: str,
    ctx: AgentContext,
    state: QueryAgentState,
) -> dict[str, Any]:
    """Pure read: fetch facts linked to a node.

    No increment_access_count. 1 nav if unvisited.
    """
    try:
        nid = uuid.UUID(node_id)
    except (ValueError, AttributeError):
        return {"error": f"Invalid node_id: '{node_id}' is not a valid UUID."}

    already_visited = state.has_visited(node_id)
    if not already_visited:
        if state.nav_remaining <= 0:
            return {
                "error": "Nav budget exhausted — cannot access unvisited node.",
                "nav_remaining": 0,
            }
        node = await ctx.graph_engine.get_node(nid)
        state.visited_nodes.append(node_id)
        state.nav_used += 1
        if node:
            await ctx.emit("node_visited", data={
                "id": node_id,
                "concept": node.concept,
                "node_type": node.node_type,
            })
        budget_cost = 1
    else:
        budget_cost = 0

    nav_remaining = state.nav_remaining

    facts = await ctx.graph_engine.get_node_facts(nid)
    total_count = len(facts)
    truncated = facts[:MAX_FACTS_RETURNED]

    return {
        "node_id": node_id,
        "budget_cost": budget_cost,
        "nav_remaining": nav_remaining,
        "facts": [
            {
                "fact_id": str(f.id),
                "content": f.content[:MAX_FACT_CONTENT_LEN]
                + ("..." if len(f.content) > MAX_FACT_CONTENT_LEN else ""),
                "type": f.fact_type,
            }
            for f in truncated
        ],
        "fact_count": total_count,
    }


# ── Tool factory ─────────────────────────────────────────────────────


def create_query_tools(
    ctx: AgentContext,
    get_state: Callable[[], QueryAgentState],
) -> list[BaseTool]:
    """Create @tool-decorated read-only tools for the query agent."""

    @tool
    async def search_graph(queries: list[str], limit: int = DEFAULT_SEARCH_LIMIT) -> str:
        """Search the existing knowledge graph by text and embedding similarity.
        Graph-only — no external API calls. Returns node summaries with concept,
        type, fact_count, richness. FREE (no budget cost).
        Default 20 results per query; pass limit (up to 100) for more."""
        result = await lightweight_search_nodes(queries, ctx, limit=limit)
        return json.dumps(result, default=str)

    @tool
    async def read_node(node_id: str) -> str:
        """Read a node's dimensions, edges, and structure. Pure read — no
        enrichment or relation building. Costs 1 nav_budget if unvisited
        (free if already visited)."""
        state = get_state()
        result = await lightweight_read_node(node_id, ctx, state)
        return json.dumps(result, default=str)

    @tool
    async def read_nodes(node_ids: list[str]) -> str:
        """Batch read multiple nodes (up to 10). Same rules as read_node.
        Costs 1 nav_budget per unvisited node."""
        state = get_state()
        capped = node_ids[:MAX_BATCH_SIZE]
        results: list[dict[str, Any]] = []
        for nid in capped:
            result = await lightweight_read_node(nid, ctx, state)
            results.append(result)

        total_cost = sum(r.get("budget_cost", 0) for r in results)
        errors = [r for r in results if "error" in r]

        return json.dumps({
            "results": results,
            "count": len(results),
            "total_budget_cost": total_cost,
            "errors": len(errors),
            "capped": len(node_ids) > MAX_BATCH_SIZE,
        }, default=str)

    @tool
    async def get_node_facts(node_id: str) -> str:
        """Get the facts linked to a node. Costs 1 nav_budget if unvisited
        (free if already visited). Use to inspect evidence behind a node."""
        state = get_state()
        result = await lightweight_get_node_facts(node_id, ctx, state)
        return json.dumps(result, default=str)

    @tool
    async def get_node_facts_batch(node_ids: list[str]) -> str:
        """Batch get facts for multiple nodes (up to 10). Same budget rules
        as get_node_facts."""
        state = get_state()
        capped = node_ids[:MAX_BATCH_SIZE]
        results: list[dict[str, Any]] = []
        for nid in capped:
            result = await lightweight_get_node_facts(nid, ctx, state)
            results.append(result)

        total_cost = sum(r.get("budget_cost", 0) for r in results)
        errors = [r for r in results if "error" in r]

        return json.dumps({
            "results": results,
            "count": len(results),
            "total_budget_cost": total_cost,
            "errors": len(errors),
            "capped": len(node_ids) > MAX_BATCH_SIZE,
        }, default=str)

    @tool
    async def get_budget() -> str:
        """Check remaining nav budget. FREE (no cost). Call this to plan
        your navigation strategy."""
        state = get_state()
        return json.dumps({
            "nav_budget": state.nav_budget,
            "nav_used": state.nav_used,
            "nav_remaining": state.nav_remaining,
            "nodes_visited": len(state.visited_nodes),
            "nodes_hidden": len(state.hidden_nodes),
        })

    @tool
    async def hide_nodes(node_ids: list[str]) -> str:
        """Hide nodes from the graph view. Emits node_hidden events so the
        frontend removes these nodes. Use when nodes are irrelevant to the
        user's question. FREE (no budget cost)."""
        state = get_state()
        hidden_count = 0
        for nid in node_ids:
            if nid not in state.hidden_nodes:
                state.hidden_nodes.append(nid)
                await ctx.emit("node_hidden", data={"id": nid})
                hidden_count += 1

        return json.dumps({
            "hidden": hidden_count,
            "total_hidden": len(state.hidden_nodes),
        })

    return [  # type: ignore[list-item]
        search_graph,
        read_node, read_nodes,
        get_node_facts, get_node_facts_batch,
        get_budget, hide_nodes,
    ]
