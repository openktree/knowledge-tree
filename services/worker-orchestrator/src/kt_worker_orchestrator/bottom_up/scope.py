"""Bottom-up scope pipeline — gather, plan perspectives.

LLM call pipeline:
1. Gather facts with extraction (GatherFactsPipeline) — seeds created during decompose
2. Plan perspectives after nodes are built (single LLM call in workflow)
3. Prioritize seeds for user selection (batched LLM calls)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from kt_agents_core.state import AgentContext
from kt_worker_orchestrator.bottom_up.prompt import (
    PERSPECTIVE_SYSTEM,
    PERSPECTIVE_USER,
    PRIORITIZE_SYSTEM,
    PRIORITIZE_USER,
)
from kt_worker_orchestrator.bottom_up.state import BottomUpScopePlan

logger = logging.getLogger(__name__)


# -- Minimal state proxy for GatherFactsPipeline ---------------------------


@dataclass
class _GatherState:
    """Minimal state compatible with GatherFactsPipeline.gather()."""

    query: str
    explore_budget: int
    explore_used: int = 0
    gathered_fact_count: int = 0
    nav_budget: int = 0
    nav_used: int = 0
    scope_description: str = ""
    message_id: str = ""
    conversation_id: str = ""

    @property
    def explore_remaining(self) -> int:
        return max(0, self.explore_budget - self.explore_used)


# -- Public entry points ---------------------------------------------------


async def run_bottom_up_scope_pipeline(
    ctx: AgentContext,
    scope_description: str,
    explore_slice: int,
    *,
    message_id: str = "",
    conversation_id: str = "",
) -> BottomUpScopePlan:
    """Run the bottom-up scope pipeline: scout → gather → filter → return node plans.

    Each scope runs its own scout to discover what's relevant to its
    specific theme, then builds search queries from the scout results.

    IMPORTANT: the caller must ``await session.commit()`` after this
    returns so that pool facts gathered are visible to the node builder.
    """
    from kt_worker_nodes.pipelines.gathering import GatherFactsPipeline

    # Step 1: Scout for this scope, then build search queries
    queries = await _scout_and_build_queries(scope_description, explore_slice, ctx)

    # Step 2: Gather facts with extraction
    proxy = _GatherState(
        query=scope_description,
        scope_description=scope_description,
        explore_budget=explore_slice,
        message_id=message_id,
        conversation_id=conversation_id,
    )
    result = await GatherFactsPipeline(ctx).gather(
        queries,
        proxy,  # type: ignore[arg-type]
        enable_summary=True,
        enable_extraction=True,
    )

    extracted_nodes = result.get("extracted_nodes", [])
    content_summary = result.get("content_summary", "")
    source_urls = result.get("source_urls", [])
    super_sources = result.get("super_sources", [])
    if not isinstance(extracted_nodes, list):
        extracted_nodes = []
    if not isinstance(source_urls, list):
        source_urls = []

    logger.info(
        "Bottom-up scope %r: gathered %d facts, extracted %d nodes, %d sources",
        scope_description[:40],
        proxy.gathered_fact_count,
        len(extracted_nodes),
        len(source_urls),
    )

    # Convert all extracted nodes to node_plans — no LLM filtering.
    # Seeds (created during decompose) handle dedup; the prioritizer
    # assigns scores so the user can choose which to build.
    node_plans = [
        {
            "name": n["name"],
            "node_type": n.get("node_type", "concept"),
            "entity_subtype": n.get("entity_subtype"),
        }
        for n in extracted_nodes
    ]

    logger.info(
        "Bottom-up scope %r: %d extracted nodes → %d node plans (seed-based, no filter)",
        scope_description[:40],
        len(extracted_nodes),
        len(node_plans),
    )

    return BottomUpScopePlan(
        node_plans=node_plans,
        explore_used=proxy.explore_used,
        gathered_fact_count=proxy.gathered_fact_count,
        extracted_count=len(extracted_nodes),
        content_summary=content_summary,
        source_urls=source_urls,
        super_sources=super_sources,  # type: ignore[arg-type]
    )


_PROPOSE_PERSPECTIVE_TOOL = {
    "type": "function",
    "function": {
        "name": "propose_perspective",
        "description": (
            "Propose a thesis/antithesis perspective pair representing a genuine "
            "debate or tension in this domain. Call this tool once per perspective pair."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "claim": {
                    "type": "string",
                    "description": "Full propositional sentence (the thesis)",
                },
                "antithesis": {
                    "type": "string",
                    "description": "Opposing propositional sentence (the antithesis)",
                },
                "source_concept": {
                    "type": "string",
                    "description": "Name of the most relevant parent concept node from the list",
                },
            },
            "required": ["claim", "antithesis", "source_concept"],
        },
    },
}


async def plan_perspectives(
    ctx: AgentContext,
    scope_description: str,
    built_nodes: list[dict[str, str]],
    content_summary: str = "",
    max_perspectives: int = 5,
) -> list[dict[str, Any]]:
    """Plan thesis/antithesis perspective pairs for a completed scope.

    Uses tool calling instead of JSON generation — models handle tool calls
    more reliably than raw JSON output.

    Call this AFTER nodes have been built, so source_concept references
    can be resolved to UUIDs.
    """
    if not built_nodes:
        return []

    node_list = "\n".join(
        f"- {n.get('concept', n.get('name', '?'))} ({n.get('node_type', 'concept')})" for n in built_nodes
    )

    content_context = ""
    if content_summary:
        content_context = f"Content summary from fact gathering:\n{content_summary}\n\n"

    user_msg = PERSPECTIVE_USER.format(
        scope=scope_description,
        count=len(built_nodes),
        node_list=node_list,
        content_context=content_context,
        max_perspectives=max_perspectives,
    )

    try:
        from kt_models.usage import clear_usage_task, set_usage_task

        set_usage_task("perspective_planning")
        tool_calls = await ctx.model_gateway.generate_with_tools(
            model_id=ctx.model_gateway.scope_model,
            messages=[{"role": "user", "content": user_msg}],
            tools=[_PROPOSE_PERSPECTIVE_TOOL],
            system_prompt=PERSPECTIVE_SYSTEM,
            temperature=0.3,
            max_tokens=2000,
        )
        clear_usage_task()

        plans: list[dict[str, Any]] = []
        for tc in tool_calls:
            if tc["name"] != "propose_perspective":
                continue
            args = tc["arguments"]
            if args.get("claim") and args.get("antithesis") and args.get("source_concept"):
                plans.append(
                    {
                        "claim": args["claim"],
                        "antithesis": args["antithesis"],
                        "source_concept_id": args["source_concept"],
                    }
                )
        return plans[:max_perspectives]

    except Exception:
        logger.warning(
            "Failed to plan perspectives for scope %r",
            scope_description,
            exc_info=True,
        )
        return []


async def plan_and_store_perspective_seeds(
    ctx: AgentContext,
    scope_description: str,
    built_nodes: list[dict[str, str]],
    content_summary: str = "",
    max_perspectives: int = 5,
) -> list[str]:
    """Plan perspectives via LLM, then store them as lightweight seeds.

    Combines plan_perspectives() with store_perspective_seeds() to
    replace the expensive composite node dispatch.

    Returns thesis seed keys.
    """
    from kt_db.repositories.write_seeds import WriteSeedRepository
    from kt_facts.processing.perspective_seeds import store_perspective_seeds
    from kt_hatchet.scope_planner import resolve_perspective_source_ids

    plans = await plan_perspectives(
        ctx,
        scope_description=scope_description,
        built_nodes=built_nodes,
        content_summary=content_summary,
        max_perspectives=max_perspectives,
    )

    if not plans:
        return []

    # Resolve source_concept names → node UUIDs
    plans = resolve_perspective_source_ids(plans, built_nodes)

    # Add scope_description and source_concept_name to each plan
    for plan in plans:
        plan["scope_description"] = scope_description
        # Find the matching built node for a human-readable name
        source_id = plan.get("source_concept_id", "")
        for bn in built_nodes:
            if bn.get("node_id") == source_id:
                plan["source_concept_name"] = bn.get("concept", bn.get("name", ""))
                break

    # Store as seeds via write-db
    if not ctx.graph_engine._write_session:
        logger.warning("No write session available, cannot store perspective seeds")
        return []

    write_seed_repo = WriteSeedRepository(ctx.graph_engine._write_session)

    thesis_keys = await store_perspective_seeds(
        plans=plans,
        write_seed_repo=write_seed_repo,
    )

    return thesis_keys


# -- Internal helpers ------------------------------------------------------


async def _scout_and_build_queries(
    scope_description: str,
    explore_budget: int,
    ctx: AgentContext,
) -> list[str]:
    """Scout for this scope's theme, then build search queries from results.

    Calls scout_impl with the scope description to discover external
    titles/snippets and graph matches specific to this scope. Builds
    search queries from the combination of scope description and
    scout-derived terms.
    """
    from kt_worker_orchestrator.agents.tools.scout import scout_impl

    queries: list[str] = [scope_description]

    try:
        scout_results = await scout_impl([scope_description], ctx)
    except Exception:
        logger.warning(
            "Scout failed for scope %r, falling back to basic queries",
            scope_description[:40],
            exc_info=True,
        )
        # Fallback: basic angle variations
        if len(queries) < explore_budget:
            queries.append(f"{scope_description} key developments and applications")
        if len(queries) < explore_budget:
            queries.append(f"{scope_description} controversies and debates")
        return queries[:explore_budget]

    # Extract useful terms from scout results
    scope_data = scout_results.get(scope_description, {})
    if not isinstance(scope_data, dict):
        scope_data = {}

    # Add queries from external search titles
    for ext in scope_data.get("external", []):
        title = ext.get("title", "") if isinstance(ext, dict) else ""
        if title and len(queries) < explore_budget:
            queries.append(f"{scope_description} {title}")

    # Add queries from graph match concepts
    for match in scope_data.get("graph_matches", []):
        concept = match.get("concept", "") if isinstance(match, dict) else ""
        if concept and concept.lower() != scope_description.lower() and len(queries) < explore_budget:
            queries.append(f"{scope_description} {concept}")

    # Fill remaining budget with angle variations
    if len(queries) < explore_budget:
        queries.append(f"{scope_description} key developments and applications")
    if len(queries) < explore_budget:
        queries.append(f"{scope_description} controversies and debates")

    return queries[:explore_budget]


async def prioritize_extracted_nodes(
    ctx: AgentContext,
    filtered_nodes: list[dict[str, Any]],
    query: str,
    content_summary: str = "",
) -> list[dict[str, Any]]:
    """Assign priority (0-10), selected flag, and perspectives to each node.

    Returns the same nodes enriched with ``priority``, ``selected``, and
    ``perspectives`` fields, sorted by priority descending.
    """
    if not filtered_nodes:
        return []

    node_list = "\n".join(f"- {n.get('name', '?')} ({n.get('node_type', 'concept')})" for n in filtered_nodes)

    user_msg = PRIORITIZE_USER.format(
        query=query,
        content_summary=content_summary or "(not available)",
        count=len(filtered_nodes),
        node_list=node_list,
    )

    try:
        from kt_models.usage import clear_usage_task, set_usage_task

        set_usage_task("prioritization")
        result = await ctx.model_gateway.generate_json(
            model_id=ctx.model_gateway.prioritization_model,
            messages=[{"role": "user", "content": user_msg}],
            system_prompt=PRIORITIZE_SYSTEM,
            temperature=0.2,
            max_tokens=8000,
        )
        clear_usage_task()

        if not result or not isinstance(result.get("nodes"), list):
            # Fallback: return all nodes as selected with default priority
            for n in filtered_nodes:
                n.setdefault("priority", 5)
                n.setdefault("selected", True)
                n.setdefault("perspectives", [])
            return filtered_nodes

        # Build lookup by normalized name.
        # The LLM may rename nodes (e.g. to add subjects to ambiguous events),
        # so we also track the original_name field for reverse mapping.
        prioritized: dict[str, dict[str, Any]] = {}
        for item in result["nodes"]:
            if not isinstance(item, dict) or not item.get("name"):
                continue
            key = str(item["name"]).strip().lower()
            prioritized[key] = item
            # Also index by original_name if the LLM provided one
            orig = item.get("original_name") or ""
            if orig:
                prioritized[str(orig).strip().lower()] = item

        enriched: list[dict[str, Any]] = []
        for n in filtered_nodes:
            key = n.get("name", "").strip().lower()
            match = prioritized.get(key)
            if match:
                # Apply renamed node name if the LLM improved it
                new_name = match.get("name", "").strip()
                if new_name and new_name.lower() != key:
                    n["name"] = new_name
                n["priority"] = max(0, min(10, int(match.get("priority", 5))))
                n["selected"] = bool(match.get("selected", True))
                n["node_type"] = match.get("node_type", n.get("node_type", "concept"))
                perspectives = match.get("perspectives") or []
                n["perspectives"] = [
                    p for p in perspectives if isinstance(p, dict) and p.get("claim") and p.get("antithesis")
                ]
            else:
                n.setdefault("priority", 5)
                n.setdefault("selected", True)
                n.setdefault("perspectives", [])
            enriched.append(n)

        # Sort by priority descending
        enriched.sort(key=lambda x: x.get("priority", 0), reverse=True)
        return enriched

    except Exception:
        logger.warning("Node prioritization LLM call failed, using defaults", exc_info=True)
        for n in filtered_nodes:
            n.setdefault("priority", 5)
            n.setdefault("selected", True)
            n.setdefault("perspectives", [])
        return filtered_nodes


_PRIORITIZE_BATCH_SIZE = 75
"""Max nodes per prioritization LLM call. Keeps context focused so the
model can evaluate each node properly without losing entries."""


async def batched_prioritize_nodes(
    ctx: AgentContext,
    filtered_nodes: list[dict[str, Any]],
    query: str,
    content_summary: str = "",
) -> list[dict[str, Any]]:
    """Prioritize extracted nodes, batching when the list is large.

    For small lists (<=75), delegates directly to ``prioritize_extracted_nodes``.
    For larger lists, splits into batches of 75, runs them in parallel via
    ``asyncio.gather``, merges results, and sorts by priority descending.
    """
    if not filtered_nodes:
        return []

    if len(filtered_nodes) <= _PRIORITIZE_BATCH_SIZE:
        return await prioritize_extracted_nodes(
            ctx,
            filtered_nodes,
            query,
            content_summary=content_summary,
        )

    import asyncio

    batches = [
        filtered_nodes[i : i + _PRIORITIZE_BATCH_SIZE] for i in range(0, len(filtered_nodes), _PRIORITIZE_BATCH_SIZE)
    ]

    logger.info(
        "Batched prioritization: %d nodes → %d batches of ≤%d",
        len(filtered_nodes),
        len(batches),
        _PRIORITIZE_BATCH_SIZE,
    )

    results = await asyncio.gather(
        *[
            prioritize_extracted_nodes(
                ctx,
                batch,
                query,
                content_summary=content_summary,
            )
            for batch in batches
        ],
        return_exceptions=True,
    )

    merged: list[dict[str, Any]] = []
    for i, result in enumerate(results):
        if isinstance(result, BaseException):
            logger.warning("Prioritize batch %d failed: %s — using defaults", i, result)
            for n in batches[i]:
                n.setdefault("priority", 5)
                n.setdefault("selected", True)
                n.setdefault("perspectives", [])
            merged.extend(batches[i])
        else:
            merged.extend(result)

    merged.sort(key=lambda x: x.get("priority", 0), reverse=True)
    return merged
