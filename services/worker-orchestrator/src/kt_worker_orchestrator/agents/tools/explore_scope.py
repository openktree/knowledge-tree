"""Tool: explore_scope — Isolated sub-explorer agent for scoped exploration.

Follows the synthesize_answer.py pattern: a LangGraph sub-agent with its own
tools, system prompt, and state. Each sub-explorer gets a focused scope, its
own budget slice, and produces a briefing summary for the orchestrator.

The sub-explorer has access to the same _impl functions (gather_facts, build_concept,
etc.) but operates in isolation — preventing anchoring bias from early results
and enabling broader coverage through parallel scopes.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.messages.utils import trim_messages
from langchain_core.tools import BaseTool, tool
from langgraph.graph import END, StateGraph
from pydantic import BaseModel, Field

from kt_agents_core.base import BaseAgent, approx_tokens
from kt_agents_core.state import AgentContext
from kt_config.settings import get_settings
from kt_worker_nodes.agents.tools.build_node import build_nodes_impl
from kt_worker_nodes.agents.tools.read_node import read_node_impl
from kt_worker_nodes.pipelines.gathering import GatherFactsPipeline
from kt_worker_orchestrator.agents.orchestrator_state import OrchestratorState, SubExplorerState
from kt_worker_query.agents.tools.query_tools import DEFAULT_SEARCH_LIMIT, lightweight_search_nodes

logger = logging.getLogger(__name__)


# ── Pydantic schemas for tool inputs ─────────────────────────────


class NodeEntry(BaseModel):
    """A node to build."""

    name: str = Field(description="Node name or label, e.g. 'quantum entanglement' or 'Albert Einstein'")
    node_type: str = Field(
        default="concept",
        description=(
            "One of: 'concept' (abstract topic/idea), 'entity' (person or organization), "
            "'event' (temporal occurrence), 'location' (physical place)"
        ),
    )


class PerspectiveEntry(BaseModel):
    """A perspective to build as a thesis/antithesis pair."""

    claim: str = Field(
        description="Full propositional sentence (the thesis), e.g. 'Vaccines have a strong safety profile'"
    )
    source_concept_id: str = Field(description="UUID of the concept node this perspective is about")
    antithesis: str | None = Field(
        default=None,
        description="Opposing claim (the antithesis). When provided, BOTH thesis and antithesis nodes are created as a dialectic pair.",
    )


# ── Sub-explorer system prompt ─────────────────────────────────────

SUB_EXPLORER_SYSTEM_PROMPT = """\
You are a focused Sub-Explorer agent within an integrative knowledge system. \
You have been given a specific SCOPE to investigate. Your responsibility is to \
explore that scope thoroughly — gathering facts, building relevant nodes \
(concepts, entities, events, locations), and creating perspectives that capture \
the debatable dimensions of the topic. You will produce a concise briefing \
summary for the orchestrator when done.


## Your Tools

- **gather_facts(search_queries)** — Gather facts from external sources into \
the shared fact pool. Each query costs 1 explore_budget. A single gather call \
brings in facts about MANY topics beyond your exact query — the pool will \
contain facts about related people, places, organizations, techniques, events, \
and more. EXPLOIT THIS.
- **build_nodes(nodes)** — Batch build multiple nodes (up to 10) in one call. \
Each entry: {"name": "...", "node_type": "concept|entity|event|location"}. Costs 1 \
nav_budget per node. If the fact pool already has relevant facts for a node, \
no explore_budget is spent; if the pool has no facts, 1 explore_budget is \
spent on an external search. Edges between nodes are automatically created \
from dimension-suggested relationships.
- **build_perspectives(perspectives)** — Save perspective pairs as lightweight \
seeds for later user-driven synthesis (up to 10). Each entry: \
{"claim": "...", "source_concept_id": "...", "antithesis": "..."}. \
The claim is the THESIS and antithesis is the opposing claim. Both MUST be \
full propositional sentences — one core idea per claim. Compound sentences \
are fine, but each perspective should capture ONE debatable position, not \
multiple unrelated arguments. Prefer broad, well-supported perspectives \
over narrow niche claims. Always provide BOTH thesis AND antithesis. \
The antithesis argues FOR an alternative position, not merely against the thesis. \
FREE — no budget cost. Seeds are stored for users to browse and selectively \
synthesize into full perspective nodes.
- **search_graph(queries, limit=20)** — Search the knowledge graph by text and \
embedding similarity. Accepts a list of query strings. Returns node summaries \
with concept, type, fact_count, richness. Pass limit (up to 100) for more \
results. FREE — no budget cost. Use this to survey what already exists before \
building, so you don't waste budget on duplicates.
- **read_node(node_id)** — Read a node's full dimensions and edges. Costs 1 \
nav_budget if the node hasn't been visited this session; FREE if already \
visited. Use selectively — prefer search_graph to survey, and only read_node \
when you need the full dimension content or suggested_concepts.
- **get_budget()** — Check remaining budgets. FREE.
- **finish_scope(summary)** — End exploration and submit your briefing. \
You MUST call this when done.

## Node Types — Classify Every Node

Every node you build MUST have the correct node_type:

**concept** = abstract topic, idea, theory, phenomenon, field of study, technique, procedure.
  Examples: "pyramid construction techniques", "vaccine safety", "quantum entanglement", "gradient descent"

**entity** = a subject capable of intent — a person or organization.
  Examples: "Pharaoh Khufu", "World Health Organization", "NASA"
  NOT entities: locations, publications, objects, technologies (these are concepts)

**event** = something that happened at a specific time — historical events, incidents, \
discoveries, experiments, crises, launches, treaties, disasters, elections, breakthroughs.
  Examples: "2008 financial crisis", "Apollo 11 moon landing", "Chernobyl disaster", \
"Aspect experiment 1982", "signing of the Treaty of Versailles", "2020 Nobel Prize in Chemistry"
  IMPORTANT: Nobel prizes, experiments, battles, elections, product launches, court rulings, \
and other dated occurrences are EVENTS, not entities or concepts.

**location** = a physical place — countries, cities, regions, landmarks, geographic features.
  Examples: "Silicon Valley", "Chernobyl exclusion zone", "Great Barrier Reef", "Tokyo", \
"Suez Canal", "Mount Everest"
  Locations relate to events that happened there, entities based there, and concepts associated \
with them. NOT locations: virtual spaces, abstract regions (these are concepts).

**perspective** = a debatable claim or position on a topic, built as \
thesis/antithesis pairs using Hegelian dialectics. Created via \
build_perspectives (NOT build_nodes). Each perspective is linked to source \
nodes via source_concept_id. Perspectives are composite nodes — dedicated \
agents build their content independently after dispatch.
  Quality criteria for thesis/antithesis pairs:
  - Each claim = ONE core debatable position. Compound sentences are OK, \
but don't pack multiple distinct arguments into one perspective.
  - Focus on broad, well-supported positions that the gathered facts can back up.
  - Both sides must make an AFFIRMATIVE case (the antithesis argues FOR \
something, not just against the thesis)
  - Reference specific mechanisms, evidence, trade-offs, or causal claims
  - Steelman both sides — frame each as its proponents would
  - Each side should naturally connect to different concepts/entities in the graph
  Good: "Renewable energy can replace fossil fuels for baseload power" vs \
"Grid reliability requires dispatchable fossil fuel generation"
  Good: "Germline editing to eliminate heritable diseases prevents lifetime \
suffering for thousands of families" vs "Germline modifications propagate to \
all descendants without their consent, crossing an irreversible ethical boundary"
  Bad: "Gene editing is good" vs "Gene editing is bad" (simple negation, no mechanisms)
  Bad: "Gene editing cures diseases, reduces healthcare costs, advances science, \
and empowers patients" (multiple arguments crammed into one claim)

**Classification rules:**
- If it is a person or organization (capable of intent/agency) → entity
- If it happened at a specific time or period → **event**
- If it is a physical place, country, city, or geographic feature → **location**
- If it is a debatable claim or position → **perspective** (use build_perspectives)
- If it's a general topic, theory, phenomenon, technique, or object → concept
- When in doubt → concept

## Naming Rules

**Use full names and add context when ambiguous.** Node names are matched against \
facts via text similarity — short or ambiguous names cause false matches:
- Entities: use full names — "Pam Bondi" not "Bondi", "Elon Musk" not "Musk"
- Abbreviations: add context — "H2O water molecule" not "H2O", "WHO health organization" not "WHO"
- Common words: disambiguate — "chemical bonding" not "bonding", "Trump tariff policy" not "tariffs"
- Use descriptive names of 2-6 words, never single words.

## Budget System

You have two budgets:
- **nav_budget** — Spent when building nodes (1 per node) or reading unvisited \
nodes (1 per read). Reading nodes you already visited or created is FREE. \
Perspective seeds are FREE (no nav cost).
- **explore_budget** — Spent on external searches: gather_facts (1 per query) \
and building a node when the fact pool has no relevant facts (1 per node).

You MUST use ALL of your nav budget before finishing. Plan your spending wisely — \
you will NOT be allowed to finish with unused nav budget.

## Your Mission: Plan and Execute

When you receive your scope, think about it before acting. You should:
1. Assess the scope and plan your node building strategy
2. Execute your plan through the phases below, adjusting as you learn
3. Propose perspective seeds for any debatable sub-topics (free, no budget cost)

## Phases

### Phase 1: GATHER — Targeted queries within your scope
- Start with 1-2 gather_facts queries covering different angles of your scope.
- Each query fills the shared fact pool with facts about MANY related topics.

### Phase 2: BUILD CORE — Primary nodes for your scope
- Use search_graph first to check what already exists — don't rebuild what's there.
- Build the main concept node for your scope.
- Build the key entities, events, locations, and related concepts from the fact pool.
- Use build_nodes for batch efficiency — mix concepts, entities, events, AND locations in one call.
- Always read_node on the core nodes central to your scope — these are the nodes \
that MUST have rich dimensions and suggested_concepts for the research to be \
complete. Their suggested_concepts will guide the rest of your exploration.

### Phase 3: EXPAND — Follow the facts
- Read back a few key nodes you built (using read_node) — their dimensions \
contain suggested_concepts. Build nodes for any suggested concept or entity \
that falls within your scope.
- WATCH YOUR BUDGET: Do not spend all your remaining nav budget on read_node \
calls. Each unvisited read costs 1 nav. Use search_graph (free) to survey \
first, then read only the most important nodes.

### Phase 4: PERSPECTIVES — Hegelian Dialectics
This is a critical phase. Perspectives capture the debatable dimensions of \
your scope as thesis/antithesis pairs.
- For any debatable sub-topic, build perspectives as **thesis/antithesis pairs**.
- Always provide BOTH a thesis (claim) AND an antithesis in each entry. \
The system creates both nodes automatically and links them with a contradicts edge.
- The source_concept_id MUST be a UUID of a concept node you built or found earlier.
- The pair costs only 1 nav_budget together. Both go through the same pipeline.

Quality principles for thesis/antithesis:
1. ONE core position per claim — don't list multiple arguments in a single perspective. \
If a topic has several debatable angles, create separate pairs for each.
2. Prefer broad, general perspectives that the gathered data supports well.
3. Both sides must make an AFFIRMATIVE case — the antithesis argues FOR an \
alternative position, not merely against the thesis.
4. Reference specific mechanisms, evidence, or trade-offs — not vague value \
judgments like "X is good/bad".
5. Steelman both sides — frame each as a knowledgeable proponent would argue it.
6. Each side should connect to different graph concepts/entities, creating richer edges.

Examples:
  - claim: "Vaccines prevent far more harm than they cause" \
vs antithesis: "Passive reporting systems undercount adverse events"
  - claim: "Remote work increases productivity for knowledge workers" \
vs antithesis: "In-office collaboration drives innovation that remote work cannot replicate"

### Phase 5: FINISH — Submit briefing
Call finish_scope with a briefing that tells the orchestrator:
  - What key concepts/entities you built (with node IDs)
  - What perspectives exist and their stance balance
  - What cross-scope connections you noticed (topics relevant to other scopes)
  - What angles remain unexplored

## Rules

- Stay within your assigned scope — do not drift into unrelated topics
- Be thorough within scope — build a mix of concepts, entities, and events
- Use search_graph before building to avoid duplicating existing nodes
- Call get_budget() if unsure about remaining budget
- When budget is exhausted, call finish_scope immediately
- Do NOT build nodes outside your scope; note them in the briefing instead

## Tool Call Format — EXACT JSON Schema

CRITICAL: Every build_nodes entry MUST use exactly these keys: "name" and "node_type". \
No other keys are accepted.

build_nodes(nodes=[
  {"name": "<node label>", "node_type": "concept"},
  {"name": "<node label>", "node_type": "entity"},
  {"name": "<node label>", "node_type": "event"},
  {"name": "<node label>", "node_type": "location"}
])

Every build_perspectives entry MUST use exactly these keys: "claim", "source_concept_id", \
and "antithesis".

build_perspectives(perspectives=[
  {"claim": "<thesis sentence>", "source_concept_id": "<uuid>", "antithesis": "<opposing sentence>"}
])

## Examples — Full Flow Including Perspectives

### Climate scope: "renewable energy transition"
```
gather_facts(search_queries=["renewable energy grid transition challenges", "solar wind power growth 2024"])

build_nodes(nodes=[
  {"name": "renewable energy transition", "node_type": "concept"},
  {"name": "grid energy storage", "node_type": "concept"},
  {"name": "solar power", "node_type": "concept"},
  {"name": "International Energy Agency", "node_type": "entity"},
  {"name": "2015 Paris Agreement", "node_type": "event"}
])

# Broad perspectives grounded in the data — one core position per claim:
build_perspectives(perspectives=[
  {"claim": "Renewable energy can replace fossil fuels for baseload power generation", "source_concept_id": "<uuid of renewable energy transition>", "antithesis": "Grid reliability requires dispatchable fossil fuel generation until storage technology matures"},
  {"claim": "Falling solar costs make renewables the cheapest new electricity source globally", "source_concept_id": "<uuid of solar power>", "antithesis": "Integration costs including storage and grid upgrades erase the headline cost advantage of renewables"}
])
```

### Physics scope: "quantum entanglement experiments"
```
gather_facts(search_queries=["quantum entanglement Bell test experiments", "EPR paradox Einstein Bohr debate"])

build_nodes(nodes=[
  {"name": "quantum entanglement", "node_type": "concept"},
  {"name": "Bell's theorem", "node_type": "concept"},
  {"name": "quantum nonlocality", "node_type": "concept"},
  {"name": "Albert Einstein", "node_type": "entity"},
  {"name": "Niels Bohr", "node_type": "entity"},
  {"name": "Alain Aspect", "node_type": "entity"},
  {"name": "Aspect experiment 1982", "node_type": "event"},
  {"name": "2022 Nobel Prize in Physics", "node_type": "event"}
])

# Perspectives — affirmative cases with specific mechanisms:
build_perspectives(perspectives=[
  {"claim": "Bell test violations demonstrate nonlocal correlations that cannot be explained by any local hidden variable theory, as confirmed by loophole-free experiments", "source_concept_id": "<uuid of quantum nonlocality>", "antithesis": "Superdeterminism and retrocausal models preserve locality by explaining Bell correlations through measurement-setting dependencies, avoiding the need for nonlocal influences"},
  {"claim": "Einstein's EPR argument identified a genuine tension between quantum completeness and locality that drove decades of productive experimental work", "source_concept_id": "<uuid of quantum entanglement>", "antithesis": "Bohr's complementarity framework resolves the EPR paradox by showing that quantum properties are contextual to the measurement arrangement, not pre-existing hidden values"}
])
```

### Politics scope: "2024 US immigration policy debate"
```
gather_facts(search_queries=["US immigration policy border security 2024", "immigration reform economic impact"])

build_nodes(nodes=[
  {"name": "US immigration policy", "node_type": "concept"},
  {"name": "border security", "node_type": "concept"},
  {"name": "immigration economic impact", "node_type": "concept"},
  {"name": "asylum system", "node_type": "concept"},
  {"name": "Department of Homeland Security", "node_type": "entity"},
  {"name": "2024 border crisis", "node_type": "event"}
])

# Highly contested — affirmative cases with specific mechanisms:
build_perspectives(perspectives=[
  {"claim": "Legal immigration pathways reduce unauthorized crossings by providing viable alternatives, as demonstrated by post-1986 IRCA visa expansion data", "source_concept_id": "<uuid of border security>", "antithesis": "Border infrastructure and enforcement capacity are a precondition before pathway expansion can work, since the 2014 surge followed perceived leniency signals"},
  {"claim": "Immigrant workers fill critical labor gaps in agriculture, construction, and healthcare, contributing $2T+ annually to GDP with net positive fiscal impact at the federal level", "source_concept_id": "<uuid of immigration economic impact>", "antithesis": "High immigration rates suppress wage growth in low-skill sectors by 3-8% and concentrate fiscal costs at the state and local level where services are funded"},
  {"claim": "The asylum system fulfills binding international obligations under the 1951 Refugee Convention and protects people facing credible persecution threats", "source_concept_id": "<uuid of asylum system>", "antithesis": "Asylum claim backlogs exceeding 2 million cases create de facto indefinite admission, incentivizing economic migrants to use asylum as a bypass for immigration caps"}
])
```

### Biology scope: "CRISPR gene editing applications"
```
gather_facts(search_queries=["CRISPR Cas9 gene editing mechanism applications", "CRISPR clinical trials diseases"])

build_nodes(nodes=[
  {"name": "CRISPR-Cas9 mechanism", "node_type": "concept"},
  {"name": "gene therapy", "node_type": "concept"},
  {"name": "germline editing", "node_type": "concept"},
  {"name": "Jennifer Doudna", "node_type": "entity"},
  {"name": "Emmanuelle Charpentier", "node_type": "entity"},
  {"name": "2020 Nobel Prize in Chemistry", "node_type": "event"},
  {"name": "He Jiankui affair 2018", "node_type": "event"}
])

# Mix of contested ethics and technical debate — affirmative cases:
build_perspectives(perspectives=[
  {"claim": "Germline editing to eliminate heritable diseases like sickle cell and Huntington's prevents lifetime suffering for thousands of families and reduces long-term healthcare costs", "source_concept_id": "<uuid of germline editing>", "antithesis": "Germline modifications propagate to all descendants without their consent, crossing an irreversible ethical boundary that somatic therapies avoid while treating the same conditions"},
  {"claim": "CRISPR-based therapies like Casgevy for sickle cell disease demonstrate that precision gene editing can achieve functional cures where traditional medicine offers only management", "source_concept_id": "<uuid of gene therapy>", "antithesis": "Off-target editing rates of 1-5% in current CRISPR systems pose unquantified cancer risks, and delivery challenges limit treatment to ex vivo applications for the foreseeable future"}
])
```

### Automotive scope: "2024 Toyota RAV4 vs Honda CR-V"
```
gather_facts(search_queries=["2024 Toyota RAV4 specs price fuel economy", "2024 Honda CR-V specs price fuel economy"])

build_nodes(nodes=[
  {"name": "compact SUV segment comparison", "node_type": "concept"},
  {"name": "hybrid powertrain technology", "node_type": "concept"},
  {"name": "Toyota RAV4", "node_type": "entity"},
  {"name": "Honda CR-V", "node_type": "entity"},
  {"name": "Toyota Motor Corporation", "node_type": "entity"},
  {"name": "Honda Motor Company", "node_type": "entity"}
])

# Factual comparison — affirmative cases with specific trade-offs:
build_perspectives(perspectives=[
  {"claim": "Toyota's hybrid powertrain delivers class-leading 41 MPG combined and proven reliability with lower long-term maintenance costs across 200k+ miles", "source_concept_id": "<uuid of compact SUV segment comparison>", "antithesis": "Honda's hybrid offers a more refined driving experience with superior noise isolation, 6 cubic feet more cargo space, and a lower starting MSRP that offsets the MPG difference"}
])
```

## Efficiency

Call MULTIPLE tools in a single response when inputs are independent. \
Use build_nodes to batch everything in one call — up to 10 entries per call.
"""


# ── Tool factory ───────────────────────────────────────────────────


def create_sub_explorer_tools(
    ctx: AgentContext,
    get_state: Callable[[], SubExplorerState],
) -> list[BaseTool]:
    """Create tools for a sub-explorer agent.

    Wraps existing _impl functions with the SubExplorerState (which is
    structurally compatible with OrchestratorState).
    """

    @tool
    async def gather_facts(search_queries: list[str]) -> str:
        """Gather facts from external sources into the fact pool.
        Each query costs 1 explore_budget."""
        state = get_state()
        result = await GatherFactsPipeline(ctx).gather(search_queries, state)  # type: ignore[arg-type]
        return json.dumps(result, default=str)

    @tool
    async def build_nodes(nodes: list[NodeEntry]) -> str:
        """Batch build multiple nodes (up to 10). Mix concepts and entities.
        Each entry must have "name" (the node label) and "node_type" ("concept" or "entity")."""
        state = get_state()

        # Convert Pydantic models to dicts for the impl function
        node_dicts = [n.model_dump() for n in nodes]
        result = await build_nodes_impl(node_dicts, ctx, state, scope_name=state.scope)  # type: ignore[arg-type]
        output = json.dumps(result, default=str)

        # Remind agent to use remaining nav budget
        nav_remaining = max(0, state.nav_budget - state.nav_used)
        if nav_remaining > 0:
            output += (
                f"\n\n⚠ You still have {nav_remaining} nav budget remaining. "
                f"You MUST use your full nav budget before finishing."
            )

        return output

    @tool
    async def build_perspectives(perspectives: list[PerspectiveEntry]) -> str:
        """Propose perspective pairs as seeds for later user-driven synthesis.
        Each entry must have "claim" (full sentence) and "source_concept_id" (UUID of source concept).
        Saves lightweight seeds — no LLM cost. Users choose which to synthesize."""
        from kt_db.repositories.write_seeds import WriteSeedRepository
        from kt_facts.processing.perspective_seeds import store_perspective_seeds

        state = get_state()

        plans = [
            {
                "claim": p.claim,
                "antithesis": p.antithesis or "",
                "source_concept_id": p.source_concept_id,
                "scope_description": state.scope or "",
            }
            for p in perspectives
            if p.claim and p.antithesis
        ]

        if not plans:
            return json.dumps({"results": [], "seeds_created": 0})

        # Store as seeds via write-db
        write_session = ctx.graph_engine._write_session
        if not write_session:
            return json.dumps({"error": "No write session available", "seeds_created": 0})

        write_seed_repo = WriteSeedRepository(write_session)
        thesis_keys = await store_perspective_seeds(
            plans=plans,
            write_seed_repo=write_seed_repo,
        )

        results = [{"claim": p["claim"], "antithesis": p["antithesis"], "action": "seeded"} for p in plans]

        return json.dumps(
            {
                "results": results,
                "seeds_created": len(thesis_keys),
                "thesis_seed_keys": thesis_keys,
            },
            default=str,
        )

    @tool
    async def read_node(node_id: str) -> str:
        """Read a node's dimensions and edges. Costs 1 nav_budget if unvisited, free if already visited."""
        result = await read_node_impl(node_id, ctx, get_state())  # type: ignore[arg-type]
        return json.dumps(result, default=str)

    @tool
    async def search_graph(queries: list[str], limit: int = DEFAULT_SEARCH_LIMIT) -> str:
        """Search the existing knowledge graph by text and embedding similarity.
        Graph-only — no external API calls. Returns node summaries with concept,
        type, fact_count, richness. FREE (no budget cost).
        Default 20 results per query; pass limit (up to 100) for more."""
        result = await lightweight_search_nodes(queries, ctx, limit=limit)
        return json.dumps(result, default=str)

    @tool
    async def get_budget() -> str:
        """Check remaining budgets. FREE."""
        state = get_state()
        nav_remaining = max(0, state.nav_budget - state.nav_used)
        return json.dumps(
            {
                "nav_budget": state.nav_budget,
                "nav_used": state.nav_used,
                "nav_remaining": nav_remaining,
                "explore_budget": state.explore_budget,
                "explore_used": state.explore_used,
                "explore_remaining": state.explore_remaining,
                "nodes_created": len(state.created_nodes),
                "nodes_visited": len(state.visited_nodes),
                "facts_gathered": state.gathered_fact_count,
                "scope": state.scope,
            }
        )

    @tool
    async def finish_scope(summary: str) -> str:
        """End exploration and submit a briefing summary for the orchestrator.
        Call this when you have finished exploring your scope."""
        state = get_state()

        # Block early finish if nav budget is not fully used
        nav_remaining = max(0, state.nav_budget - state.nav_used)
        if nav_remaining > 0:
            msg = (
                f"CANNOT FINISH YET — you still have {nav_remaining} nav budget remaining. "
                f"You MUST use your full nav budget before finishing. "
                f"Use read_node to read existing nodes — check their dimensions for "
                f"suggested_concepts to guide your next exploration direction. "
                f"If you've built new nodes, read them back to discover edge opportunities."
            )
            return msg

        state.summary = summary
        state.phase = "done"
        return "Scope exploration complete. Summary submitted."

    return [  # type: ignore[list-item]
        gather_facts,
        build_nodes,
        build_perspectives,
        search_graph,
        read_node,
        get_budget,
        finish_scope,
    ]


# ── Logging helpers ───────────────────────────────────────────────


def _get_node_name(n: Any) -> str:
    """Extract node name from a dict or Pydantic model, trying common keys."""
    if isinstance(n, dict):
        return n.get("name") or n.get("concept") or n.get("label") or n.get("title") or "?"
    return getattr(n, "name", None) or getattr(n, "concept", None) or "?"


def _get_node_type(n: Any) -> str:
    """Extract node type from a dict or Pydantic model."""
    if isinstance(n, dict):
        return n.get("node_type") or n.get("type") or "concept"
    return getattr(n, "node_type", None) or getattr(n, "type", None) or "concept"


async def _emit_tool_items(ctx: AgentContext, tool_name: str, args: dict[str, Any], scope: str) -> None:
    """Emit one activity_log event per item in a batch tool call."""
    prefix = f"[{scope}]"
    if tool_name == "build_nodes":
        nodes = args.get("nodes", [])
        for n in nodes:
            name = _get_node_name(n)
            ntype = _get_node_type(n)
            await ctx.emit("activity_log", action=f"{prefix} building {ntype}: {name}", tool="explore_scope")
        if not nodes:
            await ctx.emit("activity_log", action=f"{prefix} build_nodes (empty list)", tool="explore_scope")
    elif tool_name == "gather_facts":
        queries = args.get("search_queries", [])
        for q in queries:
            await ctx.emit("activity_log", action=f"{prefix} gathering: {q!r}", tool="explore_scope")
    elif tool_name == "build_perspectives":
        perspectives = args.get("perspectives", [])
        for p in perspectives:
            claim = p.get("claim", "?") if isinstance(p, dict) else getattr(p, "claim", "?")
            await ctx.emit("activity_log", action=f"{prefix} perspective: {claim[:80]}", tool="explore_scope")
    elif tool_name == "search_graph":
        queries = args.get("queries", [])
        await ctx.emit("activity_log", action=f"{prefix} searching graph: {queries!r}", tool="explore_scope")
    elif tool_name == "read_node":
        await ctx.emit(
            "activity_log", action=f"{prefix} reading node: {args.get('node_id', '?')}", tool="explore_scope"
        )
    elif tool_name == "finish_scope":
        summary = args.get("summary", "")[:80]
        await ctx.emit("activity_log", action=f"{prefix} finishing: {summary}...", tool="explore_scope")
    elif tool_name == "get_budget":
        await ctx.emit("activity_log", action=f"{prefix} checking budget", tool="explore_scope")
    else:
        await ctx.emit("activity_log", action=f"{prefix} {tool_name}", tool="explore_scope")


async def _emit_tool_results(ctx: AgentContext, tool_name: str, result: str, scope: str) -> None:
    """Emit per-item result events for batch tools."""
    prefix = f"[{scope}]"
    if tool_name == "build_nodes":
        try:
            data = json.loads(result)
            for r in data.get("results", []):
                action = r.get("action", "unknown")
                name = r.get("concept") or r.get("name") or "?"
                ntype = r.get("node_type", "")
                if action == "created":
                    await ctx.emit("activity_log", action=f"{prefix} ✓ created {ntype}: {name}", tool="explore_scope")
                elif action == "enriched":
                    new = r.get("new_facts_linked", 0)
                    await ctx.emit(
                        "activity_log", action=f"{prefix} ✓ enriched: {name} (+{new} facts)", tool="explore_scope"
                    )
                elif action == "skipped":
                    reason = r.get("reason", "")
                    await ctx.emit(
                        "activity_log", action=f"{prefix} ✗ skipped: {name} ({reason})", tool="explore_scope"
                    )
                elif action == "error":
                    reason = r.get("reason", "")
                    await ctx.emit("activity_log", action=f"{prefix} ✗ error: {name} ({reason})", tool="explore_scope")
                elif action == "read":
                    await ctx.emit("activity_log", action=f"{prefix} ✓ read: {name}", tool="explore_scope")
        except (json.JSONDecodeError, AttributeError):
            pass
    elif tool_name == "gather_facts":
        try:
            data = json.loads(result)
            total = data.get("facts_gathered", 0)
            queries = data.get("queries_executed", 0)
            await ctx.emit(
                "activity_log", action=f"{prefix} gathered {total} facts from {queries} queries", tool="explore_scope"
            )
        except (json.JSONDecodeError, AttributeError):
            pass
    elif tool_name == "build_perspectives":
        try:
            data = json.loads(result)
            for r in data.get("results", []):
                action = r.get("action", "unknown")
                claim = r.get("claim", "?")[:60]
                if action == "created":
                    await ctx.emit("activity_log", action=f"{prefix} ✓ perspective: {claim}", tool="explore_scope")
                elif action == "skipped":
                    reason = r.get("reason", "")
                    await ctx.emit(
                        "activity_log",
                        action=f"{prefix} ✗ perspective skipped: {claim} ({reason})",
                        tool="explore_scope",
                    )
        except (json.JSONDecodeError, AttributeError):
            pass


def _summarize_args(tool_name: str, args: dict[str, Any]) -> str:
    """Compact arg summary for backend logs."""
    if tool_name == "build_nodes":
        nodes = args.get("nodes", [])
        sample = repr(nodes[0]) if nodes else "[]"
        names = [_get_node_name(n) for n in nodes[:5]]
        return f"nodes=[{', '.join(repr(n) for n in names)}]({len(nodes)} total) sample={sample}"
    if tool_name == "gather_facts":
        queries = args.get("search_queries", [])
        return f"queries={queries}"
    if tool_name == "build_perspectives":
        perspectives = args.get("perspectives", [])
        return f"perspectives=({len(perspectives)} items)"
    if tool_name == "search_graph":
        return f"queries={args.get('queries', [])!r}"
    if tool_name == "read_node":
        return f"node_id={args.get('node_id', '?')}"
    if tool_name == "finish_scope":
        return f"summary={args.get('summary', '')[:100]!r}"
    return str(args)[:200]


# ── Sub-explorer subgraph ─────────────────────────────────────────


def _approx_tokens(messages: list[BaseMessage]) -> int:
    """Approximate token count for message trimming (chars / 4).

    .. deprecated:: Use ``agents.base.approx_tokens`` instead.
    """
    return approx_tokens(messages)


# ── SubExplorerAgent class ───────────────────────────────────────


class SubExplorerAgent(BaseAgent[SubExplorerState]):
    """Sub-explorer agent — focused scope investigation.

    Commit-based tool execution with granular per-item event emission.
    """

    max_trim_tokens = 100_000
    terminal_phase = "done"
    emit_tool_label = "explore_scope"

    def create_tools(self) -> list[BaseTool]:
        return create_sub_explorer_tools(self.ctx, self._get_state)

    def get_model_id(self) -> str:
        return self.ctx.model_gateway.scope_model

    def get_state_type(self) -> type[SubExplorerState]:
        return SubExplorerState

    def get_reasoning_effort(self) -> str | None:
        return self.ctx.model_gateway.scope_thinking_level or None

    def check_budget_exhaustion(self, state: SubExplorerState) -> dict[str, Any] | None:
        nav_remaining = max(0, state.nav_budget - state.nav_used)
        iteration = state.iteration_count

        logger.info(
            "[%s] iter=%d | explore=%d/%d nav=%d/%d | nodes=%d facts=%d | phase=%s",
            state.scope,
            iteration,
            state.explore_remaining,
            state.explore_budget,
            nav_remaining,
            state.nav_budget,
            len(state.created_nodes),
            state.gathered_fact_count,
            state.phase,
        )

        # Hard stop: both budgets exhausted
        if state.explore_remaining <= 0 and nav_remaining <= 0 and state.phase != "done":
            logger.info("[%s] Both budgets exhausted at iter=%d, forcing finish", state.scope, iteration)
            return {
                "messages": [
                    HumanMessage(content="Both budgets exhausted. Call finish_scope now with your briefing summary.")
                ]
            }

        return None

    async def execute_tool_calls(
        self,
        state: SubExplorerState,
        tool_calls: list[dict[str, Any]],
        tools_by_name: dict[str, BaseTool],
    ) -> list[ToolMessage]:
        """Commit-based execution with per-item event emission."""
        tool_messages: list[ToolMessage] = []
        iteration = state.iteration_count + 1

        for tc in tool_calls:
            name = tc["name"]
            args = tc.get("args", {})

            await _emit_tool_items(self.ctx, name, args, state.scope)
            logger.info("[%s] iter=%d tool=%s args=%s", state.scope, iteration, name, _summarize_args(name, args))
            if name == "build_nodes":
                logger.info("[%s] iter=%d build_nodes RAW tc[args]=%r", state.scope, iteration, args)

            try:
                tool_fn = tools_by_name[name]
                result = await tool_fn.ainvoke(args)
                await self.ctx.session.commit()
                tool_messages.append(ToolMessage(content=str(result), tool_call_id=tc["id"], name=name))

                await _emit_tool_results(self.ctx, name, result, state.scope)
                logger.info("[%s] iter=%d tool=%s → OK", state.scope, iteration, name)
                if self.ctx.pipeline_tracker:
                    await self.ctx.pipeline_tracker.log_tool_call(
                        scope_id=state.scope,
                        phase="exploring",
                        tool_name=name,
                        params=args,
                    )
            except Exception as exc:
                logger.warning(
                    "[%s] iter=%d tool=%s → ERROR: %s: %s",
                    state.scope,
                    iteration,
                    name,
                    type(exc).__name__,
                    exc,
                )
                logger.debug("Full traceback for tool error:", exc_info=True)
                try:
                    await self.ctx.session.rollback()
                except Exception:
                    logger.debug("Rollback failed after tool error", exc_info=True)
                tool_messages.append(
                    ToolMessage(
                        content=f"Error: {type(exc).__name__}: {exc}",
                        tool_call_id=tc["id"],
                        name=name,
                    )
                )
                if self.ctx.pipeline_tracker:
                    await self.ctx.pipeline_tracker.log_tool_call(
                        scope_id=state.scope,
                        phase="exploring",
                        tool_name=name,
                        params={"error": f"{type(exc).__name__}: {exc}", **(args or {})},
                    )

        return tool_messages

    def propagate_state(self, state: SubExplorerState) -> dict[str, Any]:
        return {
            "iteration_count": state.iteration_count + 1,
            "explore_used": state.explore_used,
            "nav_used": state.nav_used,
            "visited_nodes": state.visited_nodes,
            "created_nodes": state.created_nodes,
            "created_edges": state.created_edges,
            "exploration_path": state.exploration_path,
            "gathered_fact_count": state.gathered_fact_count,
            "summary": state.summary,
            "phase": state.phase,
        }

    def emit_budget_data(self, state: SubExplorerState) -> dict[str, Any]:
        # Sub-explorer doesn't emit budget_update (orchestrator handles it)
        return {}


# ── Build the graph (backwards-compatible entry point) ───────────


def build_sub_explorer_graph(
    ctx: AgentContext,
    state_snapshot: list[SubExplorerState | None] | None = None,
    tool_factory: Callable[[AgentContext, Callable[[], SubExplorerState]], list[BaseTool]] | None = None,
) -> StateGraph:
    """Build a LangGraph StateGraph for the sub-explorer agent.

    Same agent -> tools -> agent pattern as the synthesis graph.

    Args:
        ctx: Agent context.
        state_snapshot: Optional mutable list to receive the last-known state
            snapshot from tool_node. Pass a ``[None]`` list and read
            ``state_snapshot[0]`` after ainvoke to recover partial results
            on exception/timeout.
        tool_factory: Optional factory function that creates tools for the
            sub-explorer. When provided, replaces the default in-process tools.
            Signature: ``(ctx, get_state) -> list[BaseTool]``.
    """
    _current_state: list[SubExplorerState | None] = state_snapshot if state_snapshot is not None else [None]

    get_state: Callable[[], SubExplorerState] = lambda: _current_state[0]  # type: ignore[return-value]
    if tool_factory is not None:
        tools = tool_factory(ctx, get_state)
    else:
        tools = create_sub_explorer_tools(ctx, get_state)
    tools_by_name = {t.name: t for t in tools}
    chat_model = ctx.model_gateway.get_chat_model(
        model_id=ctx.model_gateway.scope_model,
        reasoning_effort=ctx.model_gateway.scope_thinking_level or None,
    )
    llm_with_tools = chat_model.bind_tools(tools)

    async def agent_node(state: SubExplorerState) -> dict[str, Any]:
        """LLM decides next actions."""
        nav_remaining = max(0, state.nav_budget - state.nav_used)
        iteration = state.iteration_count

        logger.info(
            "[%s] iter=%d | explore=%d/%d nav=%d/%d | nodes=%d facts=%d | phase=%s",
            state.scope,
            iteration,
            state.explore_remaining,
            state.explore_budget,
            nav_remaining,
            state.nav_budget,
            len(state.created_nodes),
            state.gathered_fact_count,
            state.phase,
        )

        # Hard stop: both budgets exhausted
        if state.explore_remaining <= 0 and nav_remaining <= 0 and state.phase != "done":
            logger.info("[%s] Both budgets exhausted at iter=%d, forcing finish", state.scope, iteration)
            return {
                "messages": [
                    HumanMessage(content="Both budgets exhausted. Call finish_scope now with your briefing summary.")
                ]
            }

        trimmed = trim_messages(
            state.messages,
            max_tokens=100_000,
            token_counter=_approx_tokens,
            strategy="last",
            include_system=True,
        )

        try:
            response = await llm_with_tools.ainvoke(trimmed)
        except Exception:
            logger.exception("Error in sub-explorer agent LLM call for scope '%s'", state.scope)
            return {"phase": "done"}

        # Prevent early finish: if agent tries to end without tool calls
        # but nav budget remains, nudge it to keep exploring (max 3 nudges)
        max_nudges = 3
        if (
            isinstance(response, AIMessage)
            and not response.tool_calls
            and nav_remaining > 0
            and state.phase != "done"
            and state.nudge_count < max_nudges
        ):
            logger.info(
                "[%s] Agent tried to finish early with %d nav budget remaining, nudging (%d/%d)",
                state.scope,
                nav_remaining,
                state.nudge_count + 1,
                max_nudges,
            )
            nudge = (
                f"You still have {nav_remaining} nav budget remaining and MUST use it before finishing. "
                f"Use read_node to read existing nodes — check their dimensions for "
                f"suggested_concepts to discover what to explore next. "
                f"If you've built new nodes, read them back to find edge opportunities."
            )
            nudge += (
                " Remember: build_perspectives saves lightweight seeds (FREE, no budget). "
                "Propose thesis/antithesis pairs for any debatable aspects of your scope."
            )
            return {
                "messages": [
                    response,
                    HumanMessage(content=nudge),
                ],
                "nudge_count": state.nudge_count + 1,
            }

        return {"messages": [response]}

    async def tool_node(state: SubExplorerState) -> dict[str, Any]:
        """Execute tool calls from the last AIMessage."""
        _current_state[0] = state
        ai_msg = state.messages[-1]

        if not isinstance(ai_msg, AIMessage) or not ai_msg.tool_calls:
            return {}

        tool_messages: list[ToolMessage] = []
        iteration = state.iteration_count + 1

        for tc in ai_msg.tool_calls:
            name = tc["name"]
            args = tc.get("args", {})

            # Emit per-item events for batch tools, single event for others
            await _emit_tool_items(ctx, name, args, state.scope)
            logger.info("[%s] iter=%d tool=%s args=%s", state.scope, iteration, name, _summarize_args(name, args))
            if name == "build_nodes":
                logger.info("[%s] iter=%d build_nodes RAW tc[args]=%r", state.scope, iteration, args)

            try:
                tool_fn = tools_by_name[name]
                result = await tool_fn.ainvoke(args)
                await ctx.session.commit()
                tool_messages.append(ToolMessage(content=str(result), tool_call_id=tc["id"], name=name))

                # Emit per-item result events for batch tools
                await _emit_tool_results(ctx, name, result, state.scope)
                logger.info("[%s] iter=%d tool=%s → OK", state.scope, iteration, name)
                if ctx.pipeline_tracker:
                    await ctx.pipeline_tracker.log_tool_call(
                        scope_id=state.scope,
                        phase="exploring",
                        tool_name=name,
                        params=args,
                    )
            except Exception as exc:
                logger.warning(
                    "[%s] iter=%d tool=%s → ERROR: %s: %s",
                    state.scope,
                    iteration,
                    name,
                    type(exc).__name__,
                    exc,
                )
                logger.debug("Full traceback for tool error:", exc_info=True)
                try:
                    await ctx.session.rollback()
                except Exception:
                    logger.debug("Rollback failed after tool error", exc_info=True)
                tool_messages.append(
                    ToolMessage(
                        content=f"Error: {type(exc).__name__}: {exc}",
                        tool_call_id=tc["id"],
                        name=name,
                    )
                )
                if ctx.pipeline_tracker:
                    await ctx.pipeline_tracker.log_tool_call(
                        scope_id=state.scope,
                        phase="exploring",
                        tool_name=name,
                        params={"error": f"{type(exc).__name__}: {exc}", **(args or {})},
                    )

        return {
            "messages": tool_messages,
            "iteration_count": iteration,
            "explore_used": state.explore_used,
            "nav_used": state.nav_used,
            "visited_nodes": state.visited_nodes,
            "created_nodes": state.created_nodes,
            "created_edges": state.created_edges,
            "exploration_path": state.exploration_path,
            "gathered_fact_count": state.gathered_fact_count,
            "summary": state.summary,
            "phase": state.phase,
        }

    def should_continue(state: SubExplorerState) -> str:
        """Route after agent_node."""
        if state.phase == "done":
            return END
        last_msg = state.messages[-1] if state.messages else None
        if isinstance(last_msg, AIMessage) and last_msg.tool_calls:
            return "tools"
        # If agent_node injected a HumanMessage nudge, loop back so LLM sees it
        if isinstance(last_msg, HumanMessage):
            return "agent"
        return END

    def after_tools(state: SubExplorerState) -> str:
        """Route after tool_node."""
        if state.phase == "done":
            return END
        return "agent"

    graph = StateGraph(SubExplorerState)
    graph.add_node("agent", agent_node)
    graph.add_node("tools", tool_node)
    graph.set_entry_point("agent")
    graph.add_conditional_edges("agent", should_continue, {"tools": "tools", "agent": "agent", END: END})
    graph.add_conditional_edges("tools", after_tools, {"agent": "agent", END: END})

    return graph


# ── Public entry point ─────────────────────────────────────────────


async def explore_scope_impl(
    scope: str,
    explore_budget: int,
    nav_budget: int,
    ctx: AgentContext,
    orchestrator_state: OrchestratorState,
    tool_factory: Callable[[AgentContext, Callable[[], SubExplorerState]], list[BaseTool]] | None = None,
) -> dict[str, Any]:
    """Launch a sub-explorer agent for a focused scope.

    Validates and caps budgets against the orchestrator's remaining budget,
    deducts explore budget upfront, runs the sub-explorer, refunds unused
    budget, and propagates results back to the orchestrator state.

    Args:
        scope: Focused exploration scope (e.g. "construction techniques of giza pyramids").
        explore_budget: Requested explore budget for this scope.
        nav_budget: Requested nav budget for this scope.
        ctx: Agent context.
        orchestrator_state: Orchestrator state (mutated in-place).
        tool_factory: Optional factory for event-emitting tools. When provided,
            the sub-explorer emits events instead of calling pipelines in-process.

    Returns:
        Dict with scope, summary, nodes/edges created, budget usage.
    """
    await ctx.emit(
        "activity_log",
        action=f"Launching sub-explorer for scope: '{scope}'",
        tool="explore_scope",
    )

    # Validate budgets — reject over-allocation instead of silently capping
    orch_explore_remaining = orchestrator_state.explore_remaining
    orch_nav_remaining = max(0, orchestrator_state.nav_budget - orchestrator_state.nav_used)

    if orch_explore_remaining <= 0 and explore_budget > 0:
        return {
            "error": "Orchestrator explore budget exhausted — cannot launch sub-explorer.",
            "scope": scope,
        }

    if explore_budget > orch_explore_remaining:
        return {
            "error": (
                f"Requested explore_budget={explore_budget} exceeds remaining "
                f"orchestrator explore budget ({orch_explore_remaining}). "
                f"Reduce explore_budget to at most {orch_explore_remaining} and retry."
            ),
            "scope": scope,
        }

    capped_nav = min(nav_budget, orch_nav_remaining)

    if explore_budget > 5:
        logger.info(
            "Sub-explorer for scope '%s' requested explore_budget=%d "
            "(recommended max: 5). Consider using smaller, more focused scopes.",
            scope,
            explore_budget,
        )

    if explore_budget <= 0 and capped_nav <= 0:
        return {
            "error": "No budget available for sub-explorer.",
            "scope": scope,
        }

    # Deduct explore budget upfront to prevent overspend
    orchestrator_state.explore_used += explore_budget

    await ctx.emit(
        "budget_update",
        data={
            "nav_remaining": max(0, orchestrator_state.nav_budget - orchestrator_state.nav_used),
            "nav_total": orchestrator_state.nav_budget,
            "explore_remaining": orchestrator_state.explore_remaining,
            "explore_total": orchestrator_state.explore_budget,
        },
    )

    await ctx.emit(
        "scope_start",
        data={
            "scope": scope,
            "explore_allocated": explore_budget,
            "nav_allocated": capped_nav,
        },
    )

    # Build sub-explorer state
    system_content = (
        SUB_EXPLORER_SYSTEM_PROMPT
        + "\n\n# YOUR SCOPE\n\n"
        + f'Scope: "{scope}"\n'
        + f'Parent query: "{orchestrator_state.query}"\n\n'
        + f"Explore budget: {explore_budget}\n"
        + f"Nav budget: {capped_nav}\n\n"
        + "Investigate this scope thoroughly. Build concepts, entities, and "
        + "perspectives. Then call finish_scope with your briefing."
    )

    # Pool-building override: when explore_budget=0, the sub-explorer should
    # build from existing pool facts instead of gathering new ones.
    if explore_budget == 0:
        system_content += (
            "\n\n## POOL-BUILDING MODE (explore_budget=0)\n\n"
            "Do NOT call gather_facts — the fact pool is already populated from "
            "prior explorations. External search is unavailable.\n\n"
            "**Your workflow:**\n"
            "1. Use search_graph to survey what already exists.\n"
            "2. Build nodes with names relevant to your scope using build_nodes. "
            "The system will match pool facts to each node by name. Each node "
            "costs 1 nav_budget but no explore_budget.\n"
            "3. Build perspectives from pool facts using build_perspectives.\n"
            "4. Read back key nodes (read_node) to check suggested_concepts "
            "from their dimensions — build those too.\n"
            "5. Call finish_scope when done.\n\n"
            "The pool contains facts about many topics from prior queries. "
            "Use your nav budget to build nodes and perspectives that cover "
            "your scope thoroughly."
        )

    messages: list[BaseMessage] = [
        SystemMessage(content=system_content),
        HumanMessage(content=f'Explore this scope: "{scope}"'),
    ]

    sub_state = SubExplorerState(
        scope=scope,
        parent_query=orchestrator_state.query,
        query=scope,  # _impl compat
        nav_budget=capped_nav,
        explore_budget=explore_budget,
        messages=messages,
    )

    # Create a child context with its own session if possible, otherwise
    # fall back to the parent context (e.g. in tests without a session_factory).
    child_ctx: AgentContext | None = None
    try:
        child_ctx = ctx.create_child_context()
        run_ctx = child_ctx
    except RuntimeError:
        run_ctx = ctx

    # Run sub-explorer — track last-known state for error recovery
    _last_state: list[SubExplorerState | None] = [None]
    try:
        graph = build_sub_explorer_graph(run_ctx, state_snapshot=_last_state, tool_factory=tool_factory)
        compiled = graph.compile()

        # Each explore unit triggers ~10 agent→tool cycles (gather, build,
        # read, perspectives); each nav unit ~1 cycle.  Each cycle = 2
        # LangGraph steps.  Use a generous multiplier so the agent can
        # exhaust the fact pool without hitting the safety limit.
        max_steps = min(explore_budget, 20) * 40 + min(capped_nav, 20) * 6 + 30
        config = {"recursion_limit": max(max_steps, 120)}

        scope_timeout = get_settings().scope_timeout_seconds
        try:
            final = await asyncio.wait_for(
                compiled.ainvoke(sub_state, config=config),
                timeout=scope_timeout,
            )
        except asyncio.TimeoutError:
            logger.warning(
                "Sub-explorer for scope '%s' timed out after %ds",
                scope,
                scope_timeout,
            )
            raise TimeoutError(f"Sub-explorer for '{scope}' timed out after {scope_timeout}s") from None

        if isinstance(final, dict):
            summary = final.get("summary", "")
            created_nodes = final.get("created_nodes", [])
            created_edges = final.get("created_edges", [])
            visited_nodes = final.get("visited_nodes", [])
            explore_used = final.get("explore_used", 0)
            nav_used = final.get("nav_used", 0)
            gathered_fact_count = final.get("gathered_fact_count", 0)
        else:
            summary = final.summary
            created_nodes = final.created_nodes
            created_edges = final.created_edges
            visited_nodes = final.visited_nodes
            explore_used = final.explore_used
            nav_used = final.nav_used
            gathered_fact_count = final.gathered_fact_count

        # Commit the child session so the parent can see the data
        if child_ctx is not None:
            await child_ctx.session.commit()

    except Exception:
        logger.exception("Error in sub-explorer for scope '%s'", scope)
        summary = f"Sub-explorer for '{scope}' encountered an error. Partial results may be available."
        # Use the last-known state snapshot from the tool_node closure.
        # sub_state is the initial state passed to ainvoke and is NEVER mutated
        # by LangGraph (it works on copies), so it always has empty lists.
        fallback = _last_state[0] if _last_state[0] is not None else sub_state
        created_nodes = fallback.created_nodes
        created_edges = fallback.created_edges
        visited_nodes = fallback.visited_nodes
        explore_used = fallback.explore_used
        nav_used = fallback.nav_used
        gathered_fact_count = fallback.gathered_fact_count
        if _last_state[0] is not None:
            logger.info(
                "Recovered partial results from last state snapshot: %d created, %d visited, %d facts",
                len(created_nodes),
                len(visited_nodes),
                gathered_fact_count,
            )

        # Try to commit partial work from the child session
        if child_ctx is not None:
            try:
                await child_ctx.session.commit()
            except Exception:
                logger.debug("Child session commit failed after error, rolling back")
                try:
                    await child_ctx.session.rollback()
                except Exception:
                    logger.debug("Child session rollback also failed", exc_info=True)
    finally:
        if child_ctx is not None:
            await child_ctx.session.close()

    # Fallback summary if agent ended without calling finish_scope
    if not summary:
        summary = (
            f"Explored scope '{scope}'. "
            f"Created {len(created_nodes)} nodes, {len(created_edges)} edges. "
            f"Gathered {gathered_fact_count} facts."
        )

    # Refund unused explore budget
    actual_explore_used = min(explore_used, explore_budget)
    unused_explore = explore_budget - actual_explore_used
    if unused_explore > 0:
        orchestrator_state.explore_used -= unused_explore

    await ctx.emit(
        "scope_end",
        data={
            "scope": scope,
            "explore_used": actual_explore_used,
            "explore_allocated": explore_budget,
            "explore_refunded": unused_explore,
        },
    )

    # Propagate results back to orchestrator state
    for nid in created_nodes:
        if nid not in orchestrator_state.created_nodes:
            orchestrator_state.created_nodes.append(nid)
    for eid in created_edges:
        if eid not in orchestrator_state.created_edges:
            orchestrator_state.created_edges.append(eid)
    for nid in visited_nodes:
        if nid not in orchestrator_state.visited_nodes:
            orchestrator_state.visited_nodes.append(nid)

    orchestrator_state.nav_used += nav_used
    orchestrator_state.gathered_fact_count += gathered_fact_count

    # Append briefing
    briefing = {
        "scope": scope,
        "summary": summary,
        "nodes_created": len(created_nodes),
        "edges_created": len(created_edges),
        "explore_used": actual_explore_used,
        "explore_allocated": explore_budget,
    }
    orchestrator_state.sub_explorer_summaries.append(briefing)

    await ctx.emit(
        "budget_update",
        data={
            "nav_remaining": max(0, orchestrator_state.nav_budget - orchestrator_state.nav_used),
            "nav_total": orchestrator_state.nav_budget,
            "explore_remaining": orchestrator_state.explore_remaining,
            "explore_total": orchestrator_state.explore_budget,
        },
    )

    return {
        "scope": scope,
        "summary": summary,
        "nodes_created": list(created_nodes),
        "edges_created": list(created_edges),
        "nodes_visited": list(visited_nodes),
        "explore_used": actual_explore_used,
        "nav_used": nav_used,
        "explore_allocated": explore_budget,
        "explore_refunded": unused_explore,
        "facts_gathered": gathered_fact_count,
    }
