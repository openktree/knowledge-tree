"""Ingest-specific tools — finish_ingest, get_budget, and create_ingest_tools factory."""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from typing import Any

from langchain_core.tools import BaseTool, tool

from kt_agents_core.state import AgentContext, NodeEntry, PerspectiveEntry
from kt_worker_ingest.agents.ingest_state import IngestState

logger = logging.getLogger(__name__)


async def finish_ingest_impl(
    summary: str,
    ctx: AgentContext,
    state: IngestState,
) -> dict[str, Any]:
    """Complete the ingest with a summary. FREE."""
    state.answer = summary
    state.phase = "done"

    await ctx.emit(
        "activity_log",
        action="Ingest complete",
        tool="ingest",
    )

    return {
        "status": "complete",
        "nodes_created": len(state.created_nodes),
        "edges_created": len(state.created_edges),
        "facts_in_pool": state.total_facts,
    }


async def get_budget_impl(
    ctx: AgentContext,
    state: IngestState,
) -> dict[str, Any]:
    """Return current budget status. FREE."""
    return {
        "nav_budget": state.nav_budget,
        "nav_used": state.nav_used,
        "nav_remaining": state.nav_remaining,
        "nodes_created": len(state.created_nodes),
        "edges_created": len(state.created_edges),
        "facts_in_pool": state.total_facts,
    }


def create_ingest_tools(
    ctx: AgentContext,
    get_state: Callable[[], IngestState],
) -> list[BaseTool]:
    """Create tools for the ingest agent."""

    @tool
    async def build_nodes(nodes: list[NodeEntry]) -> str:
        """Batch build multiple nodes from the fact pool. Costs 1 nav budget per node created.
        Each entry must have "name" (the node label) and "node_type" (concept, entity, or event)."""
        from kt_worker_nodes.agents.tools.build_node import build_nodes_impl

        state = get_state()

        # Cap to remaining budget
        remaining = state.nav_remaining
        if remaining <= 0:
            return json.dumps({"error": "Node budget exhausted", "nav_remaining": 0})

        node_dicts = [n.model_dump() for n in nodes[: min(10, remaining)]]

        if not node_dicts:
            return json.dumps({"error": "No valid node entries provided"})

        result = await build_nodes_impl(node_dicts, ctx, state, scope_name=state.scope)
        await ctx.graph_engine._write_session.commit()

        result["nav_remaining"] = state.nav_remaining
        return json.dumps(result, default=str)

    @tool
    async def build_perspectives(perspectives: list[PerspectiveEntry]) -> str:
        """Batch build perspective nodes with stance classification. FREE.
        Each entry must have "claim" (full sentence) and "source_concept_id" (UUID of source concept)."""
        from kt_hatchet.models import BuildCompositeInput
        from kt_worker_nodes.workflows.composite import build_composite_task

        results_list = []
        for p in perspectives:
            if p.claim and p.source_concept_id:
                composite_input = BuildCompositeInput(
                    node_type="perspective",
                    concept=p.claim,
                    source_node_ids=[p.source_concept_id],
                    query_context=p.claim,
                )
                try:
                    result = await build_composite_task.aio_run(composite_input)
                    results_list.append({"claim": p.claim, "action": "created", "result": result})
                except Exception as exc:
                    logger.warning("Perspective composite dispatch failed: %s", exc)
                    results_list.append({"claim": p.claim, "action": "error", "error": str(exc)})
        return json.dumps({"results": results_list, "count": len(results_list)}, default=str)

    @tool
    async def read_node(node_id: str) -> str:
        """Read an existing node's dimensions and edges. Costs 1 nav budget.

        Args:
            node_id: UUID of the node to read.
        """
        from kt_worker_nodes.agents.tools.read_node import read_node_impl

        state = get_state()

        if state.nav_remaining <= 0:
            return json.dumps({"error": "Node budget exhausted", "nav_remaining": 0})

        result = await read_node_impl(node_id, ctx, state)
        return json.dumps(result, default=str)

    @tool
    async def get_budget() -> str:
        """Check current budget status. FREE."""
        state = get_state()
        result = await get_budget_impl(ctx, state)
        return json.dumps(result, default=str)

    @tool
    async def finish_ingest(summary: str) -> str:
        """Complete the ingest with a summary of what was extracted. FREE.

        Args:
            summary: A markdown summary of the knowledge extracted from the sources.
                    Include: key concepts found, entities identified, perspectives created,
                    and notable relationships discovered.
        """
        state = get_state()
        result = await finish_ingest_impl(summary, ctx, state)
        return json.dumps(result, default=str)

    @tool
    async def browse_index(start: int = 0, count: int = 20) -> str:
        """Browse the content index — returns section titles with index numbers. FREE.

        Args:
            start: Starting index (default 0).
            count: How many entries to return (default 20, max 50).
        """
        state = get_state()
        ci = state.content_index
        if ci is None:
            return json.dumps({"error": "No content index available"})

        count = min(count, 50)
        entries = ci.entries

        # If partitioned, only show assigned range
        if state.partition_index_range is not None:
            p_start, p_end = state.partition_index_range
            entries = [e for e in entries if p_start <= e.idx < p_end]

        # Apply pagination
        page = entries[start : start + count]

        items = []
        for e in page:
            item: dict[str, Any] = {
                "idx": e.idx,
                "title": e.title,
                "fact_count": e.fact_count,
                "source": e.source_name,
            }
            if e.is_image:
                item["type"] = "image"
            else:
                item["char_count"] = e.char_count
            items.append(item)

        return json.dumps(
            {
                "entries": items,
                "total_entries": len(entries),
                "showing": f"{start}-{start + len(page)}",
            },
            default=str,
        )

    @tool
    async def get_summary(idx: int) -> str:
        """Read the full summary for a content index entry. FREE.

        Args:
            idx: Index number from browse_index.
        """
        state = get_state()
        ci = state.content_index
        if ci is None:
            return json.dumps({"error": "No content index available"})

        # Check partition bounds
        if state.partition_index_range is not None:
            p_start, p_end = state.partition_index_range
            if idx < p_start or idx >= p_end:
                return json.dumps({"error": f"Index {idx} is outside your assigned range [{p_start}, {p_end})"})

        entry = next((e for e in ci.entries if e.idx == idx), None)
        if entry is None:
            return json.dumps({"error": f"No entry at index {idx}"})

        return json.dumps(
            {
                "idx": entry.idx,
                "title": entry.title,
                "summary": entry.summary,
                "fact_count": entry.fact_count,
                "source": entry.source_name,
                "is_image": entry.is_image,
                "char_count": entry.char_count,
            },
            default=str,
        )

    @tool
    async def browse_facts(
        query: str = "",
        fact_type: str = "",
        unlinked_only: bool = False,
        limit: int = 15,
    ) -> str:
        """Browse the fact pool from ingested sources. FREE.

        Args:
            query: Semantic search query (empty = browse all facts).
            fact_type: Filter by type (claim, definition, statistic, etc). Empty = all.
            unlinked_only: If true, only show facts not yet linked to any node.
            limit: Max facts to return (default 15, max 30).
        """
        import uuid as _uuid

        from sqlalchemy import select

        from kt_db.write_models import WriteFact, WriteFactSource, WriteNode

        state = get_state()
        write_session = ctx.graph_engine._write_session

        # Use raw_source_ids from state (populated from ProcessedSource)
        raw_source_ids = [_uuid.UUID(rid) for rid in state.raw_source_ids]
        if not raw_source_ids:
            return json.dumps({"facts": [], "note": "No sources found"})

        # Get raw source URIs from WriteRawSource to match against WriteFactSource
        from kt_db.write_models import WriteRawSource

        uri_result = await write_session.execute(
            select(WriteRawSource.uri).where(WriteRawSource.id.in_(raw_source_ids))
        )
        source_uris = [row[0] for row in uri_result.all()]
        if not source_uris:
            return json.dumps({"facts": [], "note": "No sources found in write-db"})

        # Find fact_ids linked to these sources via WriteFactSource
        fact_id_stmt = select(WriteFactSource.fact_id).where(WriteFactSource.raw_source_uri.in_(source_uris)).distinct()

        # Build fact query
        stmt = select(WriteFact).where(WriteFact.id.in_(fact_id_stmt))

        if fact_type:
            stmt = stmt.where(WriteFact.fact_type == fact_type)

        if unlinked_only:
            # Exclude facts that appear in any WriteNode.fact_ids array
            linked_subq = select(WriteNode.fact_ids).where(WriteNode.fact_ids.isnot(None))
            linked_result = await write_session.execute(linked_subq)
            linked_fact_ids: set[str] = set()
            for (fact_ids_arr,) in linked_result.all():
                if fact_ids_arr:
                    linked_fact_ids.update(fact_ids_arr)
            if linked_fact_ids:
                linked_uuids = [_uuid.UUID(fid) for fid in linked_fact_ids]
                stmt = stmt.where(WriteFact.id.notin_(linked_uuids))

        stmt = stmt.order_by(WriteFact.created_at.desc()).limit(min(limit, 30))

        result = await write_session.execute(stmt)
        facts = list(result.scalars().all())

        return json.dumps(
            {
                "facts": [{"id": str(f.id), "type": f.fact_type, "content": f.content} for f in facts],
                "total": len(facts),
                "unlinked_only": unlinked_only,
            },
            default=str,
        )

    return [  # type: ignore[list-item]
        build_nodes,
        build_perspectives,
        read_node,
        get_budget,
        finish_ingest,
        browse_index,
        get_summary,
        browse_facts,
    ]
