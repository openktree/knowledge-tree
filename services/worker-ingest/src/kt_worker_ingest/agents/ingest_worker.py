"""IngestWorker — workflow initiation for the ingest agent."""

from __future__ import annotations

import logging
import uuid
from typing import Any

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage

from kt_agents_core.results import AgentResult, build_ingest_subgraph, extract_final_state
from kt_agents_core.worker_base import BaseWorker
from kt_worker_ingest.agents.ingest_agent import (
    INGEST_SYSTEM_PROMPT,
    MAX_ITERATIONS,
    IngestAgentImpl,
    _build_fact_summary,
    _describe_prior_nodes,
)
from kt_worker_ingest.agents.ingest_state import IngestState
from kt_worker_ingest.ingest.pipeline import DecompositionSummary, ProcessedSource

logger = logging.getLogger(__name__)


class IngestWorker(BaseWorker):
    """Encapsulates workflow initiation for the ingest agent."""

    async def run(
        self,
        conversation_id: str,
        processed_sources: list[ProcessedSource],
        nav_budget: int,
        decomp_summary: DecompositionSummary,
        *,
        content_index: Any | None = None,
        partition_index_range: tuple[int, int] | None = None,
        prior_created_nodes: list[str] | None = None,
        prior_visited_nodes: list[str] | None = None,
    ) -> AgentResult:
        """Run the ingest agent on pre-decomposed sources.

        The fact pool is already filled. The agent builds nodes constrained
        by nav_budget.

        For expansion runs, pass prior_created_nodes and prior_visited_nodes
        so the agent knows what was already built and avoids duplicating work.
        """
        ctx = self.ctx

        # Build source summaries for the prompt
        source_summaries: list[dict[str, Any]] = []
        for ps in processed_sources:
            summary: dict[str, Any] = {
                "source_id": ps.source_id,
                "name": ps.name,
                "type": "image" if ps.is_image else "text",
                "is_image": ps.is_image,
            }
            if ps.summary:
                summary["summary"] = ps.summary
            source_summaries.append(summary)

        # Build title from source names
        names = [ps.name for ps in processed_sources[:3]]
        title = ", ".join(names)
        if len(processed_sources) > 3:
            title += f" (+{len(processed_sources) - 3} more)"

        # Build fact summary for the prompt
        fact_summary = _build_fact_summary(decomp_summary)

        # Build system prompt
        system_prompt = INGEST_SYSTEM_PROMPT.format(
            total_facts=decomp_summary.total_facts,
            total_sources=decomp_summary.total_sources,
            total_chunks=decomp_summary.total_chunks_processed,
            nav_budget=nav_budget,
            fact_summary=fact_summary,
        )

        # Expansion awareness: append context about prior nodes
        is_expansion = bool(prior_created_nodes)
        if is_expansion and prior_created_nodes:
            prior_node_descriptions = await _describe_prior_nodes(
                prior_created_nodes[:50],
                ctx,
            )
            system_prompt += (
                f"\n\n## EXPANSION MODE\n\n"
                f"This is a continuation of a previous ingest. "
                f"You already built **{len(prior_created_nodes)} nodes** in earlier passes. "
                f"Your job now is to build NEW concepts, entities, events, and methods "
                f"that were NOT covered before. Do NOT rebuild existing nodes.\n\n"
                f"**Previously built nodes:**\n{prior_node_descriptions}\n\n"
                f"Focus on:\n"
                f"- Facts in the pool that no existing node covers\n"
                f"- Entities, events, or methods mentioned but not yet built\n"
                f"- Suggested concepts from existing node dimensions\n"
                f"- Perspectives not yet represented"
            )

        # Initial messages
        if is_expansion:
            human_content = (
                f"EXPAND the knowledge graph — {len(prior_created_nodes or [])} nodes already built. "
                f"The fact pool has {decomp_summary.total_facts} facts from "
                f"{decomp_summary.total_sources} source(s). "
                f"You have a NEW budget of {nav_budget} nodes.\n\n"
                "Build NEW nodes for topics not yet covered. "
                "Read existing nodes to find suggested_concepts and build those. "
                "Build entities, events, methods that were skipped in the first pass. "
                "Use build_nodes with proper node_types for batch efficiency."
            )
        else:
            human_content = (
                f"Build a rich knowledge graph from the {decomp_summary.total_facts} facts "
                f"extracted from {decomp_summary.total_sources} source(s). "
                f"You have a budget of {nav_budget} nodes.\n\n"
                "Start by calling browse_index() to see what sections are available, then "
                "get_summary(idx) on the most important ones. "
                "Build the core concepts, then aggressively exhaust the fact pool — "
                "build ALL entities (people, orgs, places), events, methods, and related concepts. "
                "Use browse_facts(unlinked_only=True) to find uncovered facts. "
                "For any debatable claims, build perspectives representing BOTH sides. "
                "Use build_nodes with proper node_types for batch efficiency."
            )

        messages: list[BaseMessage] = [
            SystemMessage(content=system_prompt),
            HumanMessage(content=human_content),
        ]

        # Build content index context for the system prompt
        index_context = ""
        if content_index is not None:
            toc = content_index.toc_text()
            if partition_index_range:
                p_start, p_end = partition_index_range
                index_context = (
                    f"\n\n## Content Index ({len(content_index.entries)} total sections)\n\n"
                    f"**Your assigned sections: [{p_start}-{p_end})** — "
                    f"use browse_index() and get_summary(idx) to explore them.\n\n"
                    f"Full table of contents:\n{toc}"
                )
            else:
                index_context = (
                    f"\n\n## Content Index ({len(content_index.entries)} sections)\n\n"
                    "Use browse_index() and get_summary(idx) to explore sections.\n\n"
                    f"Table of contents:\n{toc}"
                )
            system_prompt += index_context

        # Collect raw_source_ids from processed sources for browse_facts tool
        raw_source_ids = [ps.raw_source_id for ps in processed_sources if ps.raw_source_id]

        # Build initial state — pre-populate with prior nodes for expansion
        state = IngestState(
            conversation_id=conversation_id,
            query=f"Ingest: {title}",
            source_summaries=source_summaries,
            nav_budget=nav_budget,
            total_facts=decomp_summary.total_facts,
            fact_type_counts=decomp_summary.fact_type_counts,
            key_topics=decomp_summary.key_topics,
            decomp_source_summaries=decomp_summary.source_summaries,
            gathered_fact_count=decomp_summary.total_facts,
            content_index=content_index,
            partition_index_range=partition_index_range,
            raw_source_ids=raw_source_ids,
            messages=messages,
            visited_nodes=list(prior_visited_nodes or []),
            created_nodes=list(prior_created_nodes or []),
        )

        # Emit initial events
        await self._emit_start("Starting knowledge graph construction", "ingest", nav_budget)

        # Start pipeline scope for building phase
        tracker = ctx.pipeline_tracker
        scope_id = "ingest-building"
        if tracker:
            await tracker.start_scope(scope_id, "Building Nodes")

        # Run the graph
        agent = IngestAgentImpl(ctx)

        try:
            final = await self._compile_and_invoke(agent, state, MAX_ITERATIONS * 3)
            fs = extract_final_state(
                final,
                state,
                ["answer", "nav_used", "visited_nodes", "created_nodes", "created_edges"],
            )
            answer = fs["answer"] or ""
            nav_used = fs["nav_used"] or 0
            visited = fs["visited_nodes"] or []
            created_nodes = fs["created_nodes"] or []
            created_edges = fs["created_edges"] or []

        except Exception:
            logger.exception("Ingest agent failed for conversation %s", conversation_id)
            answer = state.answer or "Ingest processing encountered an error."
            nav_used = state.nav_used
            visited = state.visited_nodes
            created_nodes = state.created_nodes
            created_edges = state.created_edges
            if tracker:
                await tracker.complete_scope(scope_id, status="failed", error="Agent error")

        else:
            if tracker:
                await tracker.complete_scope(scope_id, node_count=len(created_nodes))

        # Build subgraph for response
        subgraph = await build_ingest_subgraph(created_nodes, created_edges, ctx)

        return AgentResult(
            answer=answer,
            visited_nodes=list(visited),
            created_nodes=list(created_nodes),
            created_edges=list(created_edges),
            nav_used=nav_used,
            explore_used=0,
            subgraph=subgraph,
        )

    async def run_expansion(
        self,
        conversation_id: str,
        nav_budget: int,
    ) -> AgentResult:
        """Run ingest expansion — build more nodes from the existing fact pool.

        Skips decomposition (facts already exist). Reconstructs the decomp
        summary and processed sources from the DB, then runs the ingest agent
        with awareness of previously created nodes.
        """
        ctx = self.ctx

        from kt_db.repositories.conversations import ConversationRepository
        from kt_worker_ingest.ingest.pipeline import reconstruct_decomp_summary, reconstruct_processed_sources

        conv_uuid = uuid.UUID(conversation_id)

        await ctx.emit("phase_change", data={"phase": "building"})

        # Reconstruct fact pool summary and source list using a short-lived
        # graph-db session (these tables are graph-db only).
        assert ctx.session_factory is not None, "session_factory required for expansion"
        async with ctx.session_factory() as graph_session:
            decomp_summary = await reconstruct_decomp_summary(conv_uuid, graph_session)
            processed_sources = await reconstruct_processed_sources(conv_uuid, graph_session)

            # Gather prior created/visited nodes
            repo = ConversationRepository(graph_session)
            prior_created = await repo.get_all_created_nodes(conv_uuid)
            prior_visited = await repo.get_all_visited_nodes(conv_uuid)

        await ctx.emit(
            "activity_log",
            action=f"Expanding ingest: {len(prior_created)} existing nodes, "
            f"{decomp_summary.total_facts} facts in pool, "
            f"budget +{nav_budget} nodes",
            tool="ingest",
        )

        return await self.run(
            conversation_id=conversation_id,
            processed_sources=processed_sources,
            nav_budget=nav_budget,
            decomp_summary=decomp_summary,
            prior_created_nodes=prior_created,
            prior_visited_nodes=prior_visited,
        )
