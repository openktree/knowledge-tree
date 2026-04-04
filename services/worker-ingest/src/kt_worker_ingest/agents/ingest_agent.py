"""Ingest Agent — LangGraph agent that builds nodes from a pre-filled fact pool.

The fact pool is filled by decompose_all_sources() BEFORE this agent runs.
The agent's job is to strategically pick which concepts to build, constrained
by a node budget (nav_budget).
"""

from __future__ import annotations

import logging
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import BaseTool

from kt_agents_core.base import BaseAgent
from kt_agents_core.state import AgentContext
from kt_worker_ingest.agents.ingest_state import IngestState
from kt_worker_ingest.agents.tools.ingest_tools import create_ingest_tools
from kt_worker_ingest.ingest.pipeline import DecompositionSummary

logger = logging.getLogger(__name__)

MAX_ITERATIONS = 40


# ── System prompt ───────────────────────────────────────────────

INGEST_SYSTEM_PROMPT = """\
You are a focused knowledge-building agent within an integrative knowledge system. \
Your job is to take a pre-analyzed fact pool extracted from uploaded documents, \
images, and links, and build the RICHEST possible set of nodes — concepts, \
entities, events, methods, perspectives — exhausting every scrap of knowledge \
in the pool.

## How This Works

The fact pool has been pre-filled with **{total_facts} facts** from \
**{total_sources} sources** ({total_chunks} chunks processed). \
All decomposition is already done — your job is to BUILD nodes from this pool.

## Your Tools

### Browsing (FREE — use these first to understand the content)
- **browse_index(start, count)** — Browse section titles with index numbers. \
Shows what content is available. Start here to understand the document structure.
- **get_summary(idx)** — Read the full summary for a section. Use to understand \
what a section covers before deciding what nodes to build.
- **browse_facts(query, fact_type, unlinked_only, limit)** — Search the fact pool. \
Use with query="" to browse all facts, or with a topic query for semantic search. \
Set unlinked_only=True to find facts no node covers yet.

### Building
- **build_nodes(nodes)** — Batch build multiple nodes (up to 10) in one call. \
Each entry: {{"name": "...", "node_type": "concept|entity|event"}}. \
Costs 1 nav_budget per node created. Edges between nodes are automatically \
created from dimension-suggested relationships. USE THIS — it is the most \
efficient way to build.
- **build_perspectives(claims)** — Batch build perspective nodes with stance \
classification. Each entry: {{"claim": "...", "source_concept_id": "..."}}. \
The claim MUST be a full propositional sentence scoped to its parent. FREE.
- **read_node(node_id)** — Read an existing node's dimensions and edges. \
Costs 1 nav_budget. Use to check suggested_concepts and verify coverage.
- **get_budget()** — Check remaining budget. FREE.
- **finish_ingest(summary)** — End construction and submit a summary. \
You MUST call this when done.

## Budget

- **Node budget**: {nav_budget} (build_nodes costs 1 per node, read_node costs 1 per read)
- **build_perspectives** is FREE

## Node Types — Classify Every Node

Every node you build MUST have the correct node_type. This is critical.

**concept** = abstract topic, idea, theory, phenomenon, field of study.
  Examples: "pyramid construction techniques", "vaccine safety", "quantum entanglement"

**entity** = a subject capable of intent — a person or organization.
  Examples: "Pharaoh Khufu", "World Health Organization", "NASA"
  NOT entities: locations, publications, objects, technologies (these are concepts)

**event** = something that happened in time (historical, scientific, ongoing).
  Examples: "2008 financial crisis", "Apollo 11 moon landing", "Chernobyl disaster"

**Classification rules:**
- If it is a person or organization (capable of intent/agency) → entity
- If it happened at a specific time or period → event
- If it's a general topic, theory, phenomenon, technique, place, or object → concept
- When in doubt → concept

**Build ALL types aggressively.** The fact pool contains facts about many \
entities, events, methods, and concepts. Build ALL of them.

## Fact Pool Summary

{fact_summary}

## Strategy

### Step 1: EXPLORE THE INDEX — Understand what content you have
Call browse_index() to see all available sections. Call get_summary(idx) on \
the most interesting sections to understand what topics, entities, and events \
are covered. This is FREE and gives you a roadmap for building.

### Step 2: BUILD CORE — The primary concepts from the sources
Build the main concept nodes that represent the central topics of the ingested \
content. These anchor everything else.

### Step 3: EXHAUST THE POOL — Build ALL related nodes
The fact pool contains facts about many topics beyond the central theme. \
Building from the pool costs budget — but the system grows richer with every \
node. DO THIS AGGRESSIVELY:
- Build ALL related concepts the pool has facts for
- Build ALL entities mentioned in the facts (people, orgs, places, publications)
- Build events that the facts reference
- Build methods or techniques described in the facts
- Use build_nodes for batch efficiency — mix concepts and entities in one call
- If ingesting a document about "CRISPR gene editing", you should build nodes \
for the main concept PLUS entities like Jennifer Doudna, Broad Institute, \
plus related concepts like off-target effects, gene therapy, plus events like \
Nobel Prize in Chemistry 2020, etc.

### Step 4: CHECK SUGGESTED CONCEPTS
Read back the nodes you built (using read_node) — their dimensions contain \
suggested_concepts. Build nodes for any suggested concept or entity that is \
grounded in the fact pool. This is how the graph becomes richly interconnected.

### Step 5: PERSPECTIVES — Debatable aspects in the content
For any debatable sub-topic in the content, build perspectives representing \
BOTH sides. This system's core principle is gathering facts from ALL viewpoints \
and building a comprehensive integrated picture — NOT a one-sided argument.
- Do NOT just represent one position — search the facts for evidence on \
EACH side and build perspectives representing each.
- Example: if the content covers "vaccine safety", build a perspective for \
"Vaccines have a strong safety profile supported by clinical evidence" AND \
"Vaccine adverse events require more comprehensive monitoring" — each backed \
by facts from the pool.

### Step 6: FIND GAPS — Use browse_facts to check coverage
Call browse_facts(unlinked_only=True) to find facts not yet linked to any node. \
If significant uncovered facts remain, build more nodes for them.

### Step 7: ASSESS — Before finishing, ask yourself:
- Have you browsed all sections in the index?
- Have you built nodes for ALL entities mentioned in the facts?
- Have you followed up on suggested_concepts from dimensions?
- Would a curious person looking at your nodes ask "what about X?" — if yes, \
and the fact pool has facts for X, build X.
- Is the fact pool exhausted — are there facts that no node uses?
- Have you built perspectives for debatable claims with BOTH sides represented?

### Step 8: FINISH — Submit summary
Call finish_ingest with a summary that includes:
- What key concepts/entities you built (with node IDs)
- What perspectives exist and their stance balance
- What connections between nodes emerged
- What angles might be worth exploring further

## Core Principle: BE CURIOUS — BUILD RICHLY

Your job is to leave the knowledge graph richer than you found it. Every node \
you build adds understanding. A well-built ingest should produce a densely \
interconnected subgraph where concepts connect to entities connect to events \
connect to methods. If you only built 2-3 nodes from a pool of 50+ facts, \
you are leaving value on the table.

**Finish ONLY when ALL of these are true:**
1. You have called get_budget() and confirmed budget is low
2. You have built nodes for the core concepts AND related entities/events/methods
3. You have checked suggested_concepts and built relevant ones
4. Perspectives exist for debatable aspects, with multiple sides represented
5. A curious person would not immediately ask "but what about X?" for something \
the fact pool covers

## Tool Call Format — EXACT JSON Schema

CRITICAL: Every build_nodes entry MUST use exactly these keys: "name" and "node_type".

```
build_nodes(nodes=[
  {{"name": "<node label>", "node_type": "concept"}},
  {{"name": "<node label>", "node_type": "entity"}},
  {{"name": "<node label>", "node_type": "event"}},
  {{"name": "<node label>", "node_type": "event"}}
])
```

Every build_perspectives entry MUST use exactly these keys: "claim" and \
"source_concept_id".

```
build_perspectives(claims=[
  {{"claim": "<full propositional sentence>", "source_concept_id": "<uuid>"}}
])
```

## Important Rules

- All knowledge comes from the pre-filled fact pool — no external knowledge.
- Build ALL node types aggressively — concepts, entities, events.
- Look for perspective-worthy claims (debatable positions with evidence for BOTH sides).
- Focus on creating a well-connected subgraph, not just isolated nodes.
- Edges between nodes are created automatically during node building.

## Efficiency

Call MULTIPLE tools in a single response when inputs are independent. \
Use build_nodes to batch everything in one call — up to 10 entries per call.
"""


def _build_fact_summary(decomp_summary: DecompositionSummary) -> str:
    """Build the fact pool summary section for the system prompt."""
    parts: list[str] = []

    # Type counts
    if decomp_summary.fact_type_counts:
        type_lines = [
            f"  - {ft}: {count}" for ft, count in sorted(decomp_summary.fact_type_counts.items(), key=lambda x: -x[1])
        ]
        parts.append("**Fact types:**\n" + "\n".join(type_lines))

    # Per-source summaries
    if decomp_summary.source_summaries:
        source_lines = []
        for ss in decomp_summary.source_summaries:
            name = ss.get("name", "unknown")
            fact_count = ss.get("fact_count", 0)
            if ss.get("error"):
                source_lines.append(f"  - {name}: {ss['error']}")
            else:
                source_lines.append(f"  - {name}: {fact_count} facts")
        parts.append("**Per-source breakdown:**\n" + "\n".join(source_lines))

    # Key topics
    if decomp_summary.key_topics:
        topics = ", ".join(decomp_summary.key_topics[:15])
        parts.append(f"**Key topics detected:** {topics}")

    return "\n\n".join(parts) if parts else "No detailed summary available."


# ── IngestAgentImpl class ────────────────────────────────────────


class IngestAgentImpl(BaseAgent[IngestState]):
    """Ingest agent — builds nodes from a pre-filled fact pool.

    Uses commit-based tool execution (not savepoints) and has a
    MAX_ITERATIONS hard limit.
    """

    max_trim_tokens = 100_000
    terminal_phase = "done"
    emit_tool_label = "ingest"
    route_nudges_to_agent = True
    MAX_NUDGES = 3

    def create_tools(self) -> list[BaseTool]:
        return create_ingest_tools(self.ctx, self._get_state)

    def get_model_id(self) -> str:
        return self.ctx.model_gateway.orchestrator_model

    def get_state_type(self) -> type[IngestState]:
        return IngestState

    def get_reasoning_effort(self) -> str | None:
        return self.ctx.model_gateway.orchestrator_thinking_level or None

    def pre_agent_hook(self, state: IngestState) -> None:
        """Set state_ref in agent_node (ingest sets it in both agent + tool)."""
        self._state_ref[0] = state

    async def execute_tool_calls(
        self,
        state: IngestState,
        tool_calls: list[dict[str, Any]],
        tools_by_name: dict[str, BaseTool],
    ) -> list[ToolMessage]:
        """Commit-based execution (not savepoints)."""
        tool_messages: list[ToolMessage] = []

        for tc in tool_calls:
            name = tc["name"]
            args = tc.get("args", {})
            await self.ctx.emit("activity_log", action=f"Executing: {name}", tool="ingest")

            try:
                if self.ctx.pipeline_tracker:
                    await self.ctx.pipeline_tracker.log_tool_call(
                        scope_id=state.scope,
                        phase="building",
                        tool_name=name,
                        params=args,
                    )
                tool_fn = tools_by_name[name]
                result = await tool_fn.ainvoke(args)
                await self.ctx.graph_engine.commit()
                tool_messages.append(ToolMessage(content=str(result), tool_call_id=tc["id"], name=name))
            except Exception as exc:
                logger.warning("Ingest tool %s error: %s: %s", name, type(exc).__name__, exc)
                try:
                    await self.ctx.graph_engine.rollback()
                except Exception:
                    logger.debug("Rollback failed", exc_info=True)
                tool_messages.append(
                    ToolMessage(
                        content=f"Error: {type(exc).__name__}: {exc}",
                        tool_call_id=tc["id"],
                        name=name,
                    )
                )

        return tool_messages

    def propagate_state(self, state: IngestState) -> dict[str, Any]:
        return {
            "nav_used": state.nav_used,
            "visited_nodes": state.visited_nodes,
            "created_nodes": state.created_nodes,
            "created_edges": state.created_edges,
            "exploration_path": state.exploration_path,
            "gathered_fact_count": state.gathered_fact_count,
            "answer": state.answer,
            "phase": state.phase,
            "nudge_count": state.nudge_count,
        }

    def emit_budget_data(self, state: IngestState) -> dict[str, Any]:
        return {
            "nav_remaining": max(0, state.nav_budget - state.nav_used),
            "nav_total": state.nav_budget,
            "explore_remaining": 0,
            "explore_total": 0,
        }

    def should_stop_after_tools(self, state: IngestState) -> bool:
        """Stop if we've hit MAX_ITERATIONS."""
        iteration = sum(1 for m in state.messages if isinstance(m, AIMessage) and m.tool_calls)
        return iteration >= MAX_ITERATIONS

    def post_llm_hook(self, state: IngestState, response: AIMessage) -> dict[str, Any] | None:
        """Nudge the agent to keep building if it tries to finish with budget remaining."""
        if state.phase == "done":
            return None
        if state.nudge_count >= self.MAX_NUDGES:
            return None

        if not response.tool_calls and state.nav_remaining > 2:
            logger.info(
                "Ingest agent tried to finish with %d nav remaining, nudging (%d/%d)",
                state.nav_remaining,
                state.nudge_count + 1,
                self.MAX_NUDGES,
            )
            nudge = (
                f"You still have {state.nav_remaining} nav budget remaining. "
                "Use browse_facts(unlinked_only=True) to find facts not yet "
                "covered by any node. Use browse_index() to check sections you "
                "haven't built nodes for yet. "
                "Build more nodes before calling finish_ingest."
            )
            if not state.created_nodes:
                nudge += (
                    " CRITICAL: You haven't built ANY nodes yet. Start with browse_index() to see available sections."
                )
            return {
                "messages": [response, HumanMessage(content=nudge)],
                "nudge_count": state.nudge_count + 1,
            }

        return None

    def on_llm_error(self, state: IngestState) -> dict[str, Any]:
        return {"phase": "done"}


# ── Utilities used by IngestWorker ──────────────────────────────


async def _describe_prior_nodes(
    node_ids: list[str],
    ctx: AgentContext,
) -> str:
    """Build a concise text list of previously built nodes for the expansion prompt."""
    import uuid as _uuid

    lines: list[str] = []
    for nid in node_ids:
        try:
            node = await ctx.graph_engine.get_node(_uuid.UUID(nid))
            if node:
                lines.append(f"- [{node.node_type}] {node.concept} ({nid[:8]}...)")
        except Exception:
            lines.append(f"- [unknown] {nid[:8]}...")
    return "\n".join(lines) if lines else "(none)"
