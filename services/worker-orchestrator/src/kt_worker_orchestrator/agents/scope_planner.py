"""Scope Planner Agent -- LangGraph-based planning + fact-gathering phase.

A LangGraph agent (extending BaseAgent) that runs as Phase 1 of explore_scope.
It scouts the scope (external search + existing graph), gathers facts for new
concepts into the shared fact pool, then incrementally builds a plan via
repeated ``add_to_plan`` calls.

Key separation of responsibilities
-----------------------------------
- **ScopePlannerAgent** (this module):
    - scouts external sources and the graph
    - calls ``gather_facts`` to decompose and store facts for NEW concepts
    - incrementally calls ``add_to_plan`` to add nodes/perspectives to the plan
    - calls ``done`` to finalise (or is forced when budget exhausted)

- **HatchetPipeline.create** (kt_hatchet/pipeline.py):
    - reads facts from the pool (NO external search -- ``explore_budget=0``)
    - creates / enriches / refreshes the node

Usage::

    async with state.session_factory() as session:
        agent_ctx = _build_agent_context(state, session)
        plan = await run_scope_planner(
            agent_ctx,
            scope_description="...",
            focus_concepts=["..."],
            nav_slice=10,
            explore_slice=3,
        )
        await session.commit()  # commit pool facts gathered during planning

    # Then fan out node_pipeline_wf for plan.node_plans
    # Then resolve + build plan.perspective_plans
"""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Annotated, Any

if TYPE_CHECKING:
    from langchain_core.messages import ToolMessage

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from langchain_core.tools import BaseTool, tool
from langgraph.graph.message import add_messages
from pydantic import BaseModel, Field

from kt_agents_core.base import BaseAgent
from kt_agents_core.state import AgentContext
from kt_worker_orchestrator.prompts.scope_planner import SCOPE_PLANNER_SYSTEM_PROMPT

logger = logging.getLogger(__name__)


# -- Minimal state proxy for GatherFactsPipeline ---------------------------


@dataclass
class _GatherState:
    """Minimal state compatible with GatherFactsPipeline.gather()."""

    query: str
    explore_budget: int
    explore_used: int = 0
    gathered_fact_count: int = 0
    # nav fields required by _emit_budget in pipeline.py -- not used by the planner
    nav_budget: int = 0
    nav_used: int = 0

    @property
    def explore_remaining(self) -> int:
        return max(0, self.explore_budget - self.explore_used)


# -- Output types ---------------------------------------------------------


@dataclass
class ScopePlan:
    """Complete build plan produced by ScopePlannerAgent."""

    node_plans: list[dict] = field(default_factory=list)
    """[{name: str, node_type: str}] -- facts already gathered into pool."""

    perspective_plans: list[dict] = field(default_factory=list)
    """[{claim: str, antithesis: str, source_concept_id: str}]
    source_concept_id is a real UUID for existing nodes or a concept name for
    newly planned nodes (resolved to UUID post-build in explore_scope).
    """

    explore_used: int = 0
    """Number of ``gather_facts`` calls charged against explore_slice."""

    gathered_fact_count: int = 0
    """Total facts successfully decomposed and stored during planning."""


# -- Tool input schemas ----------------------------------------------------


class NodePlanEntry(BaseModel):
    name: str
    node_type: str = "concept"
    entity_subtype: str | None = None


class PerspectivePlanEntry(BaseModel):
    claim: str
    antithesis: str
    source_concept_id: str


# -- LangGraph state -------------------------------------------------------


class ScopePlannerState(BaseModel):
    """LangGraph state for the scope planner agent."""

    messages: Annotated[Sequence[BaseMessage], add_messages] = Field(default_factory=list)

    # Scope info
    scope_description: str = ""
    focus_concepts: list[str] = Field(default_factory=list)

    # Budget
    nav_slice: int = 0
    explore_slice: int = 0
    explore_used: int = 0
    gathered_fact_count: int = 0

    # Accumulated plan (grows with each add_to_plan call)
    planned_nodes: list[dict] = Field(default_factory=list)
    planned_perspectives: list[dict] = Field(default_factory=list)

    # Control
    phase: str = "planning"  # planning | done
    iteration_count: int = 0
    nudge_count: int = 0

    model_config = {"arbitrary_types_allowed": True}

    @property
    def explore_remaining(self) -> int:
        return max(0, self.explore_slice - self.explore_used)

    @property
    def nav_used(self) -> int:
        return len(self.planned_nodes) + len(self.planned_perspectives)

    @property
    def nav_remaining(self) -> int:
        return max(0, self.nav_slice - self.nav_used)


# -- Agent -----------------------------------------------------------------


class ScopePlannerAgent(BaseAgent[ScopePlannerState]):
    """Planning agent: scout scope, gather facts, produce build plan.

    Uses the BaseAgent LangGraph loop. Tools execute sequentially with
    commit-based session management (same as SubExplorerAgent).
    """

    max_trim_tokens = 100_000
    terminal_phase = "done"
    emit_tool_label = "scope_planner"
    route_nudges_to_agent = True

    def get_model_id(self) -> str:
        return self.ctx.model_gateway.scope_model

    def get_state_type(self) -> type[ScopePlannerState]:
        return ScopePlannerState

    def get_reasoning_effort(self) -> str | None:
        return self.ctx.model_gateway.orchestrator_thinking_level or None

    def create_tools(self) -> list[BaseTool]:
        ctx = self.ctx

        @tool
        async def scout(queries: list[str]) -> str:
            """Search external sources and the existing graph for context.

            Returns external web snippets plus existing graph nodes with their
            UUIDs, richness scores, and staleness info. FREE -- no budget cost.
            Always call this first to understand the landscape.
            """
            from kt_worker_orchestrator.agents.tools.scout import scout_impl

            try:
                result = await scout_impl(queries, ctx)
                compact: dict[str, Any] = {}
                for q, data in result.items():
                    compact[q] = {
                        "external": data.get("external", [])[:4],
                        "graph_matches": [
                            {
                                "node_id": m["node_id"],
                                "concept": m["concept"],
                                "node_type": m["node_type"],
                                "richness": m["richness"],
                                "is_stale": m.get("is_stale", False),
                            }
                            for m in data.get("graph_matches", [])[:8]
                        ],
                    }
                return json.dumps(compact)
            except Exception as exc:
                logger.warning("scout error: %s", exc, exc_info=True)
                return json.dumps({"error": str(exc)})

        @tool
        async def search_graph(query: str) -> str:
            """Search only the knowledge graph for existing nodes matching a query.

            Uses text and semantic similarity. Use to verify whether a concept
            already exists before spending explore_budget on gather_facts.
            """
            from kt_worker_query.agents.tools.query_tools import lightweight_search_nodes

            try:
                result = await lightweight_search_nodes([query], ctx, limit=10)
                matches: list[Any] = []
                for v in result.values():
                    if isinstance(v, list):
                        matches = v
                        break
                    if isinstance(v, dict):
                        matches = v.get("matches", [])
                        break
                return json.dumps({"query": query, "matches": matches[:10]}, default=str)
            except Exception as exc:
                logger.warning("search_graph error: %s", exc, exc_info=True)
                return json.dumps({"error": str(exc)})

        @tool
        async def read_node(node_id: str) -> str:
            """Read an existing graph node's structure for planning purposes. FREE.

            Returns the node's definition, dimension summaries with suggested
            concepts, and nearest neighbours. Use to understand existing nodes
            and to get UUIDs for perspective planning.
            """
            try:
                nid = uuid.UUID(node_id)
            except (ValueError, AttributeError):
                return json.dumps({"error": f"Invalid UUID: {node_id!r}"})

            try:
                node = await ctx.graph_engine.get_node(nid)
                if node is None:
                    return json.dumps({"error": f"Node not found: {node_id}"})

                dims = await ctx.graph_engine.get_dimensions(nid)
                suggested: list[str] = []
                dim_summaries: list[dict] = []
                for d in dims:
                    suggested.extend(d.suggested_concepts or [])
                    content = d.content[:300] + ("..." if len(d.content) > 300 else "")
                    dim_summaries.append(
                        {
                            "model_id": d.model_id,
                            "content": content,
                            "confidence": d.confidence,
                        }
                    )

                edges = await ctx.graph_engine.get_edges(nid, direction="both")
                connected: list[dict] = []
                for e in edges[:6]:
                    target_id = e.target_node_id if e.source_node_id == nid else e.source_node_id
                    target = await ctx.graph_engine.get_node(target_id)
                    if target:
                        connected.append(
                            {
                                "node_id": str(target.id),
                                "concept": target.concept,
                                "node_type": target.node_type,
                                "edge_type": e.relationship_type,
                                "weight": e.weight,
                            }
                        )

                facts = await ctx.graph_engine.get_node_facts(nid)
                return json.dumps(
                    {
                        "node_id": node_id,
                        "concept": node.concept,
                        "node_type": node.node_type,
                        "definition": node.definition,
                        "fact_count": len(facts),
                        "suggested_concepts": list(dict.fromkeys(suggested))[:12],
                        "dimensions": dim_summaries,
                        "connected_nodes": connected,
                    },
                    default=str,
                )

            except Exception as exc:
                logger.warning("read_node error %s: %s", node_id, exc, exc_info=True)
                return json.dumps({"error": str(exc)})

        @tool
        async def gather_facts(search_queries: list[str]) -> str:
            """Search external sources for multiple queries in parallel and store facts in the pool.

            Decomposes search results into typed facts and stores them in the
            shared knowledge pool. The node builder will use these facts when
            creating the node -- it has no external search access of its own.

            Costs 1 explore_budget per query. Pass 2-4 targeted queries in one
            call to cover different angles of a concept simultaneously.
            Do NOT call for concepts that already exist with good richness in the
            graph -- existing nodes are enriched from pool facts for free.
            """
            from kt_worker_nodes.pipelines.gathering.pipeline import GatherFactsPipeline

            state = self._get_state()

            remaining_budget = state.explore_slice - state.explore_used
            if remaining_budget <= 0:
                return json.dumps(
                    {
                        "error": "explore_budget exhausted",
                        "budget_used": state.explore_used,
                        "budget_total": state.explore_slice,
                        "hint": (
                            f"You have 0 explore budget remaining (used {state.explore_used}/{state.explore_slice}). "
                            f"Call add_to_plan with the concepts relevant to your scope, then call done. "
                            f"The node builder will assemble facts from the entire knowledge pool."
                        ),
                    }
                )

            proxy = _GatherState(
                query=state.scope_description,
                explore_budget=state.explore_slice,
                explore_used=state.explore_used,
            )

            try:
                result = await GatherFactsPipeline(ctx).gather(search_queries, proxy)  # type: ignore[arg-type]
                # Sync budget back to state
                state.explore_used = proxy.explore_used
                state.gathered_fact_count += proxy.gathered_fact_count
                return json.dumps(result, default=str)
            except Exception as exc:
                logger.warning("gather_facts error %r: %s", search_queries, exc, exc_info=True)
                return json.dumps(
                    {
                        "error": str(exc),
                        "remaining_explore_budget": state.explore_slice - state.explore_used,
                    }
                )

        @tool
        async def add_to_plan(
            node_plans: list[NodePlanEntry],
            perspective_plans: list[PerspectivePlanEntry] | None = None,
        ) -> str:
            """Add nodes and perspectives to the build plan.

            Call this to submit nodes and perspectives for building. Each call
            ADDS to the plan (does not replace previous submissions). After
            calling, you will see how many nav slots remain -- keep calling
            add_to_plan with more nodes/perspectives until the budget is used,
            then call done.

            node_plans: nodes to build. Include concepts you gathered facts for
              (new nodes) or that already exist in the graph (will be enriched).
            perspective_plans: thesis/antithesis pairs for debatable aspects.
              source_concept_id can be a UUID (existing node) or a concept name
              (node being built -- resolved to UUID after creation).
            """
            state = self._get_state()

            if not node_plans:
                return json.dumps(
                    {
                        "error": "node_plans is required and cannot be empty.",
                        "hint": "Include at least one node from the concepts you gathered facts for.",
                    }
                )

            # Deduplicate against already-planned nodes
            existing_names = {n["name"].lower() for n in state.planned_nodes}
            new_nodes = []
            for e in node_plans:
                stripped = e.name.strip()
                if not stripped or stripped.lower() in existing_names:
                    continue
                entry: dict[str, str | None] = {"name": stripped, "node_type": e.node_type}
                if e.node_type == "entity" and e.entity_subtype:
                    entry["entity_subtype"] = e.entity_subtype
                new_nodes.append(entry)

            existing_claims = {p["claim"].lower() for p in state.planned_perspectives}
            new_perspectives = [
                {
                    "claim": e.claim.strip(),
                    "antithesis": e.antithesis.strip(),
                    "source_concept_id": e.source_concept_id.strip(),
                }
                for e in (perspective_plans or [])
                if e.claim.strip()
                and e.antithesis.strip()
                and e.source_concept_id.strip()
                and e.claim.strip().lower() not in existing_claims
            ]

            # Cap to remaining nav budget
            nav_remaining = state.nav_remaining
            if len(new_nodes) + len(new_perspectives) > nav_remaining:
                # Prioritise nodes over perspectives
                node_cap = min(len(new_nodes), nav_remaining)
                persp_cap = min(len(new_perspectives), nav_remaining - node_cap)
                new_nodes = new_nodes[:node_cap]
                new_perspectives = new_perspectives[:persp_cap]

            state.planned_nodes.extend(new_nodes)
            state.planned_perspectives.extend(new_perspectives)
            added = len(new_nodes) + len(new_perspectives)

            nav_remaining_after = state.nav_remaining
            explore_remaining = state.explore_remaining

            response: dict[str, Any] = {
                "status": "added_to_plan",
                "nodes_added": len(new_nodes),
                "perspectives_added": len(new_perspectives),
                "total_planned_nodes": len(state.planned_nodes),
                "total_planned_perspectives": len(state.planned_perspectives),
                "nav_remaining": nav_remaining_after,
                "explore_remaining": explore_remaining,
            }

            if nav_remaining_after > 0 and explore_remaining > 0:
                response["hint"] = (
                    f"Good -- {added} items added to the plan. "
                    f"You still have {nav_remaining_after} nav slots and "
                    f"{explore_remaining} explore budget remaining. "
                    f"Call gather_facts for more concepts, then add_to_plan again."
                )
            elif nav_remaining_after > 0:
                response["hint"] = (
                    f"{added} items added. You have {nav_remaining_after} nav slots remaining. "
                    f"Add more nodes/perspectives from the facts you already gathered, "
                    f"then call done."
                )
            else:
                response["hint"] = f"{added} items added. Nav budget fully used. Call done to finalise."

            return json.dumps(response)

        @tool
        async def done() -> str:
            """Finalise the plan and exit the planning phase.

            Call this after you have added all nodes and perspectives via
            add_to_plan. Your plan will be submitted for building.
            """
            state = self._get_state()

            if not state.planned_nodes:
                return json.dumps(
                    {
                        "error": "Cannot finish -- no nodes have been planned yet. "
                        "You MUST call add_to_plan before done. The node builder will "
                        "assemble facts from the entire knowledge pool (including previous queries), "
                        "so add every concept you consider relevant. Example:\n\n"
                        'add_to_plan(node_plans=[{"name": "your concept here", "node_type": "concept"}])\n\n'
                        "Then call done() to finalise.",
                    }
                )

            nav_remaining = state.nav_remaining
            explore_remaining = state.explore_remaining
            if nav_remaining > 2:
                hint = f"CANNOT FINISH YET -- you still have {nav_remaining} nav slots remaining. "
                if explore_remaining > 0:
                    hint += (
                        f"You also have {explore_remaining} explore budget. "
                        f"Call gather_facts for more concepts, then add_to_plan."
                    )
                else:
                    hint += (
                        "Add more nodes to the plan with add_to_plan. The node builder "
                        "will assemble facts from the entire knowledge pool, so add every "
                        "concept you consider relevant. Use scout or read_node to find "
                        "more concepts and perspectives to add."
                    )
                return hint

            state.phase = "done"
            return json.dumps(
                {
                    "status": "plan_finalised",
                    "total_nodes": len(state.planned_nodes),
                    "total_perspectives": len(state.planned_perspectives),
                }
            )

        return [scout, search_graph, read_node, gather_facts, add_to_plan, done]  # type: ignore[list-item]

    def check_budget_exhaustion(self, state: ScopePlannerState) -> dict[str, Any] | None:
        """Hard stop when both budgets exhausted and nodes exist. Logging only otherwise."""
        iteration = state.iteration_count

        logger.info(
            "[scope_planner:%s] iter=%d | explore=%d/%d nav_remaining=%d/%d | "
            "planned=%d nodes + %d perspectives | facts=%d | phase=%s",
            state.scope_description[:40],
            iteration,
            state.explore_remaining,
            state.explore_slice,
            state.nav_remaining,
            state.nav_slice,
            len(state.planned_nodes),
            len(state.planned_perspectives),
            state.gathered_fact_count,
            state.phase,
        )

        # Hard stop: both budgets exhausted AND nodes already planned -> force done
        if state.explore_remaining <= 0 and state.nav_remaining <= 0 and state.planned_nodes and state.phase != "done":
            logger.info(
                "[scope_planner:%s] Both budgets exhausted at iter=%d, forcing done",
                state.scope_description[:40],
                iteration,
            )
            state.phase = "done"
            return {"phase": "done"}

        # All other nudging happens in post_llm_hook (after the LLM responds)
        return None

    async def execute_tool_calls(
        self,
        state: ScopePlannerState,
        tool_calls: list[dict[str, Any]],
        tools_by_name: dict[str, BaseTool],
    ) -> list["ToolMessage"]:
        """Commit-based execution with logging."""
        from langchain_core.messages import ToolMessage

        tool_messages: list[ToolMessage] = []
        iteration = state.iteration_count + 1

        for tc in tool_calls:
            name = tc["name"]
            args = tc.get("args", {})

            logger.info(
                "[scope_planner:%s] iter=%d tool=%s args=%s",
                state.scope_description[:40],
                iteration,
                name,
                _compact_args(name, args),
            )

            try:
                tool_fn = tools_by_name[name]
                result = await tool_fn.ainvoke(args)
                await self.ctx.session.commit()
                tool_messages.append(ToolMessage(content=str(result), tool_call_id=tc["id"], name=name))
                logger.info(
                    "[scope_planner:%s] iter=%d tool=%s -> %s",
                    state.scope_description[:40],
                    iteration,
                    name,
                    _compact_result(name, result),
                )
            except Exception as exc:
                logger.warning(
                    "[scope_planner:%s] iter=%d tool=%s -> ERROR: %s: %s",
                    state.scope_description[:40],
                    iteration,
                    name,
                    type(exc).__name__,
                    exc,
                )
                logger.debug("Full traceback:", exc_info=True)
                try:
                    await self.ctx.session.rollback()
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

    MAX_NUDGES = 5

    def post_llm_hook(self, state: ScopePlannerState, response: Any) -> dict[str, Any] | None:
        """Nudge if the LLM tries to finish without tool calls while work remains."""
        from langchain_core.messages import AIMessage

        if not isinstance(response, AIMessage) or response.tool_calls:
            return None  # has tool calls -- proceed normally

        if state.phase == "done":
            return None

        if state.nudge_count >= self.MAX_NUDGES:
            return None  # let it end, fallback will handle

        # No nodes planned -- must call add_to_plan
        if not state.planned_nodes:
            logger.info(
                "[scope_planner:%s] LLM ended without tool calls, no nodes planned -- nudging (%d/%d)",
                state.scope_description[:40],
                state.nudge_count + 1,
                self.MAX_NUDGES,
            )
            return {
                "messages": [
                    response,
                    HumanMessage(
                        content="ERROR: You responded without calling any tools but there are "
                        "NO nodes in the plan. You MUST call add_to_plan with the concepts "
                        "relevant to your scope. The node builder will assemble facts from "
                        "the entire knowledge pool, so add every concept you consider relevant. "
                        "Example:\n\n"
                        f'add_to_plan(node_plans=[{{"name": "{state.scope_description[:60]}", '
                        '"node_type": "concept"}])\n\n'
                        "Then call done() to finalise."
                    ),
                ],
                "nudge_count": state.nudge_count + 1,
            }

        # Nodes planned but nav budget remaining -- nudge to add more
        if state.planned_nodes and state.nav_remaining > 2:
            logger.info(
                "[scope_planner:%s] LLM ended with %d nav remaining -- nudging (%d/%d)",
                state.scope_description[:40],
                state.nav_remaining,
                state.nudge_count + 1,
                self.MAX_NUDGES,
            )
            nudge = (
                f"You still have {state.nav_remaining} nav slots remaining. "
                "Add more nodes to the plan with add_to_plan, or call done() to finalise."
            )
            if not state.planned_perspectives:
                nudge += (
                    " Consider adding perspective pairs for debatable aspects of your scope. "
                    "Use scout to find common viewpoints and controversies, or use read_node "
                    "to check suggested_concepts on existing nodes for inspiration."
                )
            return {
                "messages": [
                    response,
                    HumanMessage(content=nudge),
                ],
                "nudge_count": state.nudge_count + 1,
            }

        return None

    def propagate_state(self, state: ScopePlannerState) -> dict[str, Any]:
        return {
            "iteration_count": state.iteration_count + 1,
            "explore_used": state.explore_used,
            "gathered_fact_count": state.gathered_fact_count,
            "planned_nodes": state.planned_nodes,
            "planned_perspectives": state.planned_perspectives,
            "phase": state.phase,
            "nudge_count": state.nudge_count,
        }


# -- Public entry point ----------------------------------------------------


async def run_scope_planner(
    agent_ctx: AgentContext,
    scope_description: str,
    focus_concepts: list[str],
    nav_slice: int,
    explore_slice: int,
) -> ScopePlan:
    """Run the scope planner agent and return a ScopePlan.

    IMPORTANT: the caller must ``await session.commit()`` after this
    returns so that pool facts gathered during planning are visible
    to the node builder tasks that follow.
    """
    agent = ScopePlannerAgent(agent_ctx)
    graph, _tools = agent.build_graph()
    compiled = graph.compile()

    hints = f"\nSearch hints: {', '.join(focus_concepts)}" if focus_concepts else ""
    user_msg = (
        f'Scope: "{scope_description}"{hints}\n\n'
        f"explore_budget: {explore_slice} gather_facts calls available\n"
        f"nav_slice: {nav_slice} total items "
        f"(node_plans + perspective_plans combined)\n\n"
        f"Scout thoroughly, gather facts for new concepts, then use "
        f"add_to_plan to add nodes to the plan, and call done when finished."
    )

    initial_state = ScopePlannerState(
        messages=[
            SystemMessage(content=SCOPE_PLANNER_SYSTEM_PROMPT),
            HumanMessage(content=user_msg),
        ],
        scope_description=scope_description,
        focus_concepts=focus_concepts,
        nav_slice=nav_slice,
        explore_slice=explore_slice,
    )

    try:
        final_state = await compiled.ainvoke(
            initial_state,
            config={"recursion_limit": 80},
        )
    except Exception:
        logger.exception(
            "ScopePlannerAgent: graph execution failed (scope=%r)",
            scope_description,
        )
        final_state = initial_state

    # Extract state -- may be dict or ScopePlannerState
    if isinstance(final_state, dict):
        planned_nodes = final_state.get("planned_nodes", [])
        planned_perspectives = final_state.get("planned_perspectives", [])
        explore_used = final_state.get("explore_used", 0)
        gathered_fact_count = final_state.get("gathered_fact_count", 0)
    else:
        planned_nodes = final_state.planned_nodes
        planned_perspectives = final_state.planned_perspectives
        explore_used = final_state.explore_used
        gathered_fact_count = final_state.gathered_fact_count

    # Fallback if no nodes planned
    if not planned_nodes:
        logger.warning(
            "ScopePlannerAgent: no nodes planned (scope=%r) -- using fallback",
            scope_description,
        )
        planned_nodes = [{"name": scope_description, "node_type": "concept"}]
        for c in focus_concepts[: max(0, nav_slice - 1)]:
            planned_nodes.append({"name": c, "node_type": "concept"})

    plan = ScopePlan(
        node_plans=planned_nodes,
        perspective_plans=planned_perspectives,
        explore_used=explore_used,
        gathered_fact_count=gathered_fact_count,
    )

    logger.info(
        "ScopePlannerAgent done -- %d nodes, %d perspectives (scope=%r, explore_used=%d/%d, facts=%d)",
        len(plan.node_plans),
        len(plan.perspective_plans),
        scope_description,
        explore_used,
        explore_slice,
        gathered_fact_count,
    )
    return plan


# -- Logging helpers -------------------------------------------------------


def _compact_args(tool_name: str, args: dict) -> str:
    """One-line summary of tool call arguments for log output."""
    if tool_name in ("scout", "search_graph"):
        queries = args.get("queries") or args.get("query") or ""
        if isinstance(queries, list):
            return repr(queries[:3])
        return repr(str(queries)[:120])
    if tool_name == "gather_facts":
        qs = args.get("search_queries", [])
        return f"{len(qs)} queries: {repr(qs[:2])}"
    if tool_name == "add_to_plan":
        nodes = args.get("node_plans", [])
        persp = args.get("perspective_plans") or []
        return f"{len(nodes)} nodes, {len(persp)} perspectives"
    if tool_name == "read_node":
        return repr(args.get("node_id", ""))
    if tool_name == "done":
        return ""
    return repr(str(args)[:120])


def _compact_result(tool_name: str, result: str) -> str:
    """One-line summary of tool result for log output."""
    try:
        data = json.loads(result)
    except Exception:
        return repr(result[:120])

    if tool_name == "scout":
        total_ext = sum(len(v.get("external", [])) for v in data.values() if isinstance(v, dict))
        total_graph = sum(len(v.get("graph_matches", [])) for v in data.values() if isinstance(v, dict))
        return f"{len(data)} queries -> {total_ext} external, {total_graph} graph matches"
    if tool_name == "search_graph":
        matches = data.get("matches", [])
        names = [m.get("concept", "?") for m in matches[:4]]
        return f"{len(matches)} matches: {names}"
    if tool_name == "gather_facts":
        if "error" in data:
            return f"ERROR: {data['error']}"
        facts = data.get("facts_gathered", 0)
        remaining = data.get("explore_remaining", "?")
        executed = data.get("queries_executed", "?")
        return f"facts_gathered={facts}, queries={executed}, explore_remaining={remaining}"
    if tool_name == "add_to_plan":
        if "error" in data:
            return f"ERROR: {data['error']}"
        return (
            f"added={data.get('nodes_added', 0)}+{data.get('perspectives_added', 0)}, "
            f"total={data.get('total_planned_nodes', 0)}+{data.get('total_planned_perspectives', 0)}, "
            f"nav_remaining={data.get('nav_remaining', '?')}"
        )
    if tool_name == "done":
        return f"status={data.get('status')}"
    if tool_name == "read_node":
        if "error" in data:
            return f"ERROR: {data['error']}"
        return (
            f"concept={data.get('concept')!r}, facts={data.get('fact_count')}, dims={len(data.get('dimensions', []))}"
        )
    return repr(result[:120])


# -- Post-build perspective resolution ------------------------------------


def resolve_perspective_source_ids(
    perspective_plans: list[dict],
    built_nodes: list[dict],
) -> list[dict]:
    """Replace concept-name source_concept_ids with real node UUIDs.

    The planner produces ``source_concept_id`` values that are either:
    - A real UUID (for existing nodes found during scouting) -> kept as-is
    - A concept name (for nodes being built) -> resolved from built_nodes

    Perspectives whose source cannot be resolved are dropped with a warning.
    """
    name_to_id: dict[str, str] = {}
    for n in built_nodes:
        nid = n.get("node_id")
        concept = (n.get("concept") or "").strip()
        if nid and concept:
            name_to_id[concept.lower()] = nid

    resolved: list[dict] = []
    for plan in perspective_plans:
        source = (plan.get("source_concept_id") or "").strip()
        if not source:
            continue

        # Already a valid UUID -- keep as-is
        try:
            uuid.UUID(source)
            resolved.append(plan)
            continue
        except (ValueError, AttributeError):
            pass

        # Exact name match
        nid = name_to_id.get(source.lower())

        # Partial match fallback
        if not nid:
            for name, candidate in name_to_id.items():
                if source.lower() in name or name in source.lower():
                    nid = candidate
                    break

        if nid:
            resolved.append({**plan, "source_concept_id": nid})
        else:
            logger.debug(
                "resolve_perspective_source_ids: cannot resolve %r -- dropping",
                source,
            )

    return resolved
