"""Navigation tools for the Synthesizer Agent.

Mirrors the 8 MCP tools from the reference synthesizer agent:
search_graph, search_facts, get_node, get_edges, get_facts,
get_dimensions, get_fact_sources, get_node_paths.
"""

from __future__ import annotations

import logging
import uuid
from collections import defaultdict
from typing import Any

from langchain_core.tools import BaseTool, tool

from kt_agents_core.state import AgentContext
from kt_config.types import COMPOUND_FACT_TYPES
from kt_db.models import Fact

logger = logging.getLogger(__name__)


def _fact_label(content: str, max_words: int = 8) -> str:
    """Extract a short label from fact content for citation tags."""
    words = content.split()
    label = " ".join(words[:max_words])
    if len(words) > max_words:
        label += "…"
    return label.replace("{", "").replace("}", "").replace("|", "-")


def _format_fact(f: Fact, stance: str | None = None) -> str:
    """Format a single fact with type, stance, attribution, content, and ID."""
    attr_parts: list[str] = []
    for fs in getattr(f, "sources", []):
        source_parts: list[str] = []
        org = getattr(fs, "author_org", None)
        person = getattr(fs, "author_person", None)
        if org:
            source_parts.append(org)
        if person:
            source_parts.append(person)
        if source_parts:
            attr_parts.append("; ".join(source_parts))
        elif fs.attribution:
            attr_parts.append(fs.attribution)
        elif fs.raw_source and fs.raw_source.title:
            attr_parts.append(f"source: {fs.raw_source.title}")
    attr_suffix = f" ({'; '.join(attr_parts)})" if attr_parts else ""

    stance_label = f" [{stance.upper()}]" if stance else ""
    label = _fact_label(f.content)
    fact_id_tag = f" {{fact:{f.id}|{label}}}"
    if f.fact_type in COMPOUND_FACT_TYPES:
        return f"- [{f.fact_type}]{stance_label}{attr_suffix}{fact_id_tag}\n    {f.content}"
    return f"- [{f.fact_type}]{stance_label} {f.content}{attr_suffix}{fact_id_tag}"


def build_navigation_tools(ctx: AgentContext, state_ref: list[Any]) -> list[BaseTool]:
    """Build the 8 navigation tools for the synthesizer agent."""

    @tool
    async def search_graph(query: str, limit: int = 20, node_type: str | None = None) -> str:
        """Search for nodes matching a text query. Returns node ID, concept, type, and fact count. Use 4-6 different search terms for broad coverage."""
        nodes = await ctx.graph_engine.search_nodes(query, limit=limit)
        if node_type:
            nodes = [n for n in nodes if n.node_type == node_type]
        if not nodes:
            return f"No nodes found for query: {query}"
        lines = [f"Found {len(nodes)} nodes:"]
        for n in nodes:
            facts = await ctx.graph_engine.get_node_facts(n.id)
            edges = await ctx.graph_engine.get_edges(n.id, direction="both")
            lines.append(f"- {n.id} — {n.concept} [{n.node_type}] — {len(facts)} facts, {len(edges)} edges")
        return "\n".join(lines)

    @tool
    async def search_facts(query: str, limit: int = 20) -> str:
        """Search across ALL facts in the entire knowledge graph by text content. Returns fact content, sources, and ALL linked nodes. Key for finding cross-cutting patterns."""
        if not ctx.embedding_service:
            return "Embedding service not available for fact search."
        try:
            embeddings = await ctx.embedding_service.embed_batch([query])
            embedding = embeddings[0]
            from kt_qdrant.repositories.facts import QdrantFactRepository

            fact_repo = QdrantFactRepository(ctx.qdrant_client)
            results = await fact_repo.search_similar(embedding, limit=limit)
            if not results:
                return f"No facts found for query: {query}"

            # Look up fact content from DB for richer output
            from sqlalchemy import select

            from kt_db.models import Fact, NodeFact

            fact_ids = [r.fact_id for r in results]

            facts_result = await ctx.session.execute(select(Fact).where(Fact.id.in_(fact_ids)))
            facts_by_id = {str(f.id): f for f in facts_result.scalars().all()}

            # Get node links for each fact
            nf_result = await ctx.session.execute(
                select(NodeFact.fact_id, NodeFact.node_id).where(NodeFact.fact_id.in_(fact_ids))
            )
            node_links: dict[str, list[str]] = {}
            for fid, nid in nf_result.all():
                node_links.setdefault(str(fid), []).append(str(nid))

            lines = [f"Found {len(results)} facts:"]
            for r in results:
                fid = str(r.fact_id)
                fact = facts_by_id.get(fid)
                content = fact.content[:200] if fact else "?"
                nids = node_links.get(fid, [])
                lines.append(f"- [{r.fact_type or '?'}] (score={r.score:.3f}) {content}\n  nodes: {nids}")
            return "\n".join(lines)
        except Exception as exc:
            logger.warning("search_facts failed: %s", exc)
            return f"Fact search failed: {exc}"

    @tool
    async def get_node(node_id: str) -> str:
        """Get a node's definition, type, parent, and basic stats. Use this to understand what a node is about."""
        try:
            nid = uuid.UUID(node_id)
        except (ValueError, AttributeError):
            return f"Invalid node_id: '{node_id}'"
        node = await ctx.graph_engine.get_node(nid)
        if node is None:
            return f"Node not found: {node_id}"

        # Track visit
        state = state_ref[0]
        if state and node_id not in state.nodes_visited:
            state.nodes_visited.append(node_id)
            state.nodes_visited_count += 1

        lines = [f"# {node.concept} [{node.node_type}]"]
        if node.definition:
            lines.append(f"\n## Definition\n{node.definition}")
        else:
            lines.append("\n_No definition available._")
        facts = await ctx.graph_engine.get_node_facts(nid)
        edges = await ctx.graph_engine.get_edges(nid, direction="both")
        lines.append(f"\nStats: {len(facts)} facts, {len(edges)} edges")
        if node.parent_id:
            lines.append(f"Parent: {node.parent_id}")
        return "\n".join(lines)

    @tool
    async def get_edges(node_id: str, limit: int = 50) -> str:
        """Get connected nodes with relationship type, weight, justification, and fact count. Sorted by fact count."""
        try:
            nid = uuid.UUID(node_id)
        except (ValueError, AttributeError):
            return f"Invalid node_id: '{node_id}'"
        edges = await ctx.graph_engine.get_edges(nid, direction="both")
        if not edges:
            return f"No edges for node {node_id}"
        lines = [f"Edges ({len(edges)}):"]
        for edge in edges[:limit]:
            target_id = edge.target_node_id if edge.source_node_id == nid else edge.source_node_id
            target_node = await ctx.graph_engine.get_node(target_id)
            target_concept = target_node.concept if target_node else "unknown"
            target_type = getattr(target_node, "node_type", "?") if target_node else "?"
            weight_str = f"{edge.weight:.1f}" if edge.weight is not None else "n/a"
            justification = (edge.justification or "")[:150]
            lines.append(
                f"- **{target_concept}** [{target_type}] "
                f"({edge.relationship_type}, weight={weight_str}, id={target_id})\n"
                f"  {justification}"
            )
        return "\n".join(lines)

    @tool
    async def get_facts(node_id: str, limit: int = 100) -> str:
        """Get facts for a node GROUPED BY SOURCE. Each source shows URI, title, author, and nested facts with type and content."""
        try:
            nid = uuid.UUID(node_id)
        except (ValueError, AttributeError):
            return f"Invalid node_id: '{node_id}'"

        # Track visit
        state = state_ref[0]
        if state and node_id not in state.nodes_visited:
            state.nodes_visited.append(node_id)
            state.nodes_visited_count += 1

        facts_with_stance = await ctx.graph_engine.get_node_facts_with_stance(nid)
        if not facts_with_stance:
            return f"No facts for node {node_id}"

        facts_with_sources = await ctx.graph_engine.get_node_facts_with_sources(nid)
        source_map = {f.id: f for f in facts_with_sources}

        # Group by source
        by_source: dict[str, dict[str, Any]] = defaultdict(lambda: {"facts": [], "meta": {}})
        for fact, stance in facts_with_stance[:limit]:
            rich_fact = source_map.get(fact.id, fact)
            formatted = _format_fact(rich_fact, stance=stance)
            # Get source info
            source_key = "unknown"
            for fs in getattr(rich_fact, "sources", []):
                if fs.raw_source:
                    source_key = fs.raw_source.title or fs.raw_source.uri or "unknown"
                    by_source[source_key]["meta"] = {
                        "uri": fs.raw_source.uri,
                        "title": fs.raw_source.title,
                        "author_org": getattr(fs, "author_org", None),
                        "author_person": getattr(fs, "author_person", None),
                    }
                    break
            by_source[source_key]["facts"].append(formatted)

        # Track facts retrieved
        if state:
            state.facts_retrieved[node_id] = [
                f
                for _, stance in facts_with_stance[:limit]
                for f in [_format_fact(source_map.get(_.id, _), stance=stance)]
            ]

        lines = [f"Facts for node {node_id} ({len(facts_with_stance)} total, grouped by source):"]
        for source_name, data in by_source.items():
            meta = data["meta"]
            author = meta.get("author_org") or meta.get("author_person") or ""
            lines.append(f"\n### Source: {source_name} ({len(data['facts'])} facts)")
            if author:
                lines.append(f"Author: {author}")
            if meta.get("uri"):
                lines.append(f"URI: {meta['uri']}")
            for f_line in data["facts"]:
                lines.append(f_line)

        return "\n".join(lines)

    @tool
    async def get_dimensions(node_id: str) -> str:
        """Get multi-model dimension analyses for a node. Use for spotting model convergence or divergence."""
        try:
            nid = uuid.UUID(node_id)
        except (ValueError, AttributeError):
            return f"Invalid node_id: '{node_id}'"

        # Track visit
        state = state_ref[0]
        if state and node_id not in state.nodes_visited:
            state.nodes_visited.append(node_id)
            state.nodes_visited_count += 1

        dimensions = await ctx.graph_engine.get_dimensions(nid)
        if not dimensions:
            return f"No dimensions for node {node_id}"
        node = await ctx.graph_engine.get_node(nid)
        concept = node.concept if node else "unknown"
        lines = [f"Dimensions for {concept} ({len(dimensions)}):"]
        for dim in dimensions:
            tag = " [DEFINITIVE]" if dim.is_definitive else ""
            lines.append(f"\n## {dim.model_id}{tag} (confidence={dim.confidence:.2f})")
            lines.append(dim.content)
        return "\n".join(lines)

    @tool
    async def get_fact_sources(node_id: str) -> str:
        """Get deduplicated raw sources backing a node's facts. Returns URI, title, provider, date."""
        try:
            nid = uuid.UUID(node_id)
        except (ValueError, AttributeError):
            return f"Invalid node_id: '{node_id}'"
        facts = await ctx.graph_engine.get_node_facts_with_sources(nid)
        seen: set[str] = set()
        lines = ["Sources:"]
        for fact in facts:
            for fs in getattr(fact, "sources", []):
                if fs.raw_source and str(fs.raw_source.id) not in seen:
                    seen.add(str(fs.raw_source.id))
                    lines.append(
                        f"- {fs.raw_source.title or 'untitled'} ({fs.raw_source.uri})"
                        f" — provider: {fs.raw_source.provider_id}"
                    )
        return "\n".join(lines) if len(lines) > 1 else f"No sources found for node {node_id}"

    @tool
    async def get_node_paths(source_node_id: str, target_node_id: str, max_depth: int = 4) -> str:
        """Find shortest paths between two nodes via BFS over edges. Returns step-by-step paths showing intermediate nodes and connecting edges. Key for finding bridge concepts."""
        try:
            src = uuid.UUID(source_node_id)
            tgt = uuid.UUID(target_node_id)
        except (ValueError, AttributeError):
            return "Invalid node IDs"

        # Simple BFS
        visited: set[uuid.UUID] = set()
        queue: list[list[dict[str, Any]]] = [[{"node_id": src, "concept": "start"}]]
        found_paths: list[list[dict[str, Any]]] = []

        for _ in range(max_depth):
            next_queue: list[list[dict[str, Any]]] = []
            for path in queue:
                current = path[-1]["node_id"]
                if current in visited:
                    continue
                visited.add(current)
                edges = await ctx.graph_engine.get_edges(current, direction="both")
                for edge in edges:
                    neighbor = edge.target_node_id if edge.source_node_id == current else edge.source_node_id
                    if neighbor in visited:
                        continue
                    neighbor_node = await ctx.graph_engine.get_node(neighbor)
                    step = {
                        "node_id": neighbor,
                        "concept": neighbor_node.concept if neighbor_node else "unknown",
                        "edge_type": edge.relationship_type,
                        "edge_weight": edge.weight,
                    }
                    new_path = path + [step]
                    if neighbor == tgt:
                        found_paths.append(new_path)
                    else:
                        next_queue.append(new_path)
            queue = next_queue
            if found_paths:
                break

        if not found_paths:
            return f"No path found between {source_node_id} and {target_node_id} within {max_depth} hops"

        lines = [f"Found {len(found_paths)} path(s):"]
        for i, path in enumerate(found_paths[:3]):
            steps = []
            for step in path[1:]:  # skip the start placeholder
                steps.append(f"{step['concept']} (via {step['edge_type']}, weight={step.get('edge_weight', '?')})")
            lines.append(f"  Path {i + 1}: {' → '.join(steps)}")
        return "\n".join(lines)

    return [
        search_graph,
        search_facts,
        get_node,
        get_edges,
        get_facts,
        get_dimensions,
        get_fact_sources,
        get_node_paths,
    ]
