"""Agent-assisted node selection — LLM picks relevant nodes from batches.

The agent processes proposed nodes in batches of 100, using tool calls to
select nodes by index and optionally edit names/types. It handles deduplication
by choosing one representative when multiple similar nodes exist.

Batches are processed in parallel (up to agent_select_concurrency) for speed.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any

from kt_agents_core.state import AgentContext
from kt_config.settings import get_settings
from kt_hatchet.models import ProposedNode

logger = logging.getLogger(__name__)

_BATCH_SIZE = 100

# ── Tool definitions (OpenAI format) ─────────────────────────────────────

_SELECT_NODES_TOOL = {
    "type": "function",
    "function": {
        "name": "select_nodes",
        "description": (
            "Select nodes from the current batch by their index numbers. "
            "Call this once with all indices you want to select from this batch."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "indices": {
                    "type": "array",
                    "items": {"type": "integer"},
                    "description": "List of node indices to select from the current batch",
                },
            },
            "required": ["indices"],
        },
    },
}

_EDIT_NODE_TOOL = {
    "type": "function",
    "function": {
        "name": "edit_node",
        "description": (
            "Edit a node's name or type before selecting it. Use this to fix "
            "names, disambiguate, or correct the node type. Call select_nodes "
            "separately to actually select the node."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "index": {
                    "type": "integer",
                    "description": "Index of the node to edit in the current batch",
                },
                "name": {
                    "type": "string",
                    "description": "New name for the node (omit to keep current)",
                },
                "node_type": {
                    "type": "string",
                    "enum": ["concept", "entity", "event", "location"],
                    "description": "New type for the node (omit to keep current)",
                },
            },
            "required": ["index"],
        },
    },
}

_TOOLS = [_SELECT_NODES_TOOL, _EDIT_NODE_TOOL]

# ── Prompts ──────────────────────────────────────────────────────────────

_SYSTEM_PROMPT = """\
You are a node selection assistant for a knowledge graph builder. You will receive \
batches of proposed nodes and must select the most relevant ones.

Your goals:
1. Select nodes that are SPECIFIC and well-defined (not overly generic like "science" or "research")
2. Avoid duplicates — if you see "Event X" at index 5 and "1990 Event X" at index 42, \
   pick the more descriptive one and skip the other
3. Prefer nodes with higher information density and relevance to the user's context
4. You may edit node names to improve clarity (e.g., disambiguate events, fix typos) \
   using the edit_node tool BEFORE selecting them
5. You may change node types if they are clearly wrong (e.g., a person classified as "concept" \
   should be "entity")

For each batch, call select_nodes with the indices of nodes you want to keep. \
You may call edit_node first for any nodes that need name/type corrections.

Do NOT select generic/vague nodes like "various factors", "key findings", "the approach". \
Do NOT select both duplicates — pick the better one."""

_USER_PROMPT = """\
{instructions}

I need you to select up to {remaining} more nodes from this batch. \
So far {selected_so_far} nodes have been selected out of {max_select} target.

Batch ({batch_size} nodes, indices {start_idx}-{end_idx}):
{node_list}

Select the specific, relevant nodes and skip generic or duplicate ones. \
Use edit_node first if any names need fixing, then call select_nodes with your chosen indices."""


@dataclass
class _BatchResult:
    """Result from processing a single batch."""

    batch_idx: int
    start: int
    end: int
    edits: list[dict[str, Any]] = field(default_factory=list)
    selections: list[int] = field(default_factory=list)
    error: bool = False


async def _process_batch(
    ctx: AgentContext,
    nodes: list[ProposedNode],
    batch_idx: int,
    start: int,
    end: int,
    remaining: int,
    selected_so_far: int,
    max_select: int,
    instructions: str,
    semaphore: asyncio.Semaphore,
    model_id: str,
) -> _BatchResult:
    """Process a single batch of nodes via LLM. Runs under a semaphore."""
    result = _BatchResult(batch_idx=batch_idx, start=start, end=end)

    async with semaphore:
        batch = nodes[start:end]

        # Format node list for this batch
        node_lines = []
        for i, node in enumerate(batch):
            global_idx = start + i
            node_lines.append(f"[{global_idx}] {node.name} ({node.node_type}) — priority {node.priority}")
        node_list = "\n".join(node_lines)

        user_msg = _USER_PROMPT.format(
            instructions=f"Context: {instructions}\n" if instructions else "",
            remaining=remaining,
            selected_so_far=selected_so_far,
            max_select=max_select,
            batch_size=len(batch),
            start_idx=start,
            end_idx=end - 1,
            node_list=node_list,
        )

        try:
            tool_calls = await ctx.model_gateway.generate_with_tools(
                model_id=model_id,
                messages=[{"role": "user", "content": user_msg}],
                tools=_TOOLS,
                system_prompt=_SYSTEM_PROMPT,
                temperature=0.1,
                max_tokens=4000,
            )

            logger.info(
                "Agent select batch %d: LLM returned %d tool calls",
                batch_idx + 1,
                len(tool_calls),
            )

            if not tool_calls:
                logger.warning(
                    "Agent select batch %d: LLM returned no tool calls — "
                    "model may not support tool calling. Skipping batch.",
                    batch_idx + 1,
                )
                return result

            for tc in tool_calls:
                if tc["name"] == "edit_node":
                    result.edits.append(tc["arguments"])
                elif tc["name"] == "select_nodes":
                    indices = tc["arguments"].get("indices", [])
                    result.selections.extend(indices)

            logger.info(
                "Agent select batch %d: %d edits, %d selections parsed",
                batch_idx + 1,
                len(result.edits),
                len(result.selections),
            )

        except Exception:
            logger.warning(
                "Agent select batch %d failed, skipping",
                batch_idx + 1,
                exc_info=True,
            )
            result.error = True

    return result


async def agent_select_nodes(
    ctx: AgentContext,
    proposed_nodes: list[ProposedNode],
    max_select: int,
    instructions: str = "",
) -> list[ProposedNode]:
    """Use an LLM agent to select and optionally edit nodes from a proposed list.

    Processes nodes in batches of 100. Batches run in parallel (up to
    ``agent_select_concurrency`` from settings). For each batch, the LLM uses
    tool calls to select nodes by index and optionally edit names/types. After
    all batches complete, selections are applied in batch order until max_select
    is reached.

    Args:
        ctx: Agent context with model_gateway.
        proposed_nodes: Nodes sorted by priority descending.
        max_select: Maximum number of nodes to select.
        instructions: User instructions or query context.

    Returns:
        Updated proposed_nodes list with selection flags set.
    """
    if not proposed_nodes:
        return proposed_nodes

    settings = get_settings()
    concurrency = settings.agent_select_concurrency
    model_id = ctx.model_gateway.agent_select_model

    # Start with all deselected
    nodes = [n.model_copy() for n in proposed_nodes]
    for n in nodes:
        n.selected = False

    total_batches = (len(nodes) + _BATCH_SIZE - 1) // _BATCH_SIZE
    semaphore = asyncio.Semaphore(concurrency)

    # Launch all batches in parallel
    # Each batch gets a pessimistic remaining count (max_select) since we
    # can't know exact counts until all batches finish. We'll enforce the
    # cap when merging results.
    tasks = []
    for batch_idx in range(total_batches):
        start = batch_idx * _BATCH_SIZE
        end = min(start + _BATCH_SIZE, len(nodes))
        tasks.append(
            _process_batch(
                ctx=ctx,
                nodes=nodes,
                batch_idx=batch_idx,
                start=start,
                end=end,
                remaining=max_select,
                selected_so_far=0,
                max_select=max_select,
                instructions=instructions,
                semaphore=semaphore,
                model_id=model_id,
            )
        )

    results = await asyncio.gather(*tasks)

    # Apply results in batch order, respecting max_select cap
    selected_count = 0
    for result in sorted(results, key=lambda r: r.batch_idx):
        if selected_count >= max_select:
            break

        # Apply edits first
        for edit in result.edits:
            idx = edit.get("index")
            if idx is None or idx < result.start or idx >= result.end:
                continue
            if "name" in edit and edit["name"]:
                nodes[idx].name = edit["name"]
            if "node_type" in edit and edit["node_type"]:
                nodes[idx].node_type = edit["node_type"]

        # Apply selections (capped at remaining budget)
        remaining = max_select - selected_count
        valid_selections = [
            idx for idx in result.selections if result.start <= idx < result.end and not nodes[idx].selected
        ]
        for idx in valid_selections[:remaining]:
            nodes[idx].selected = True
            selected_count += 1

        logger.info(
            "Agent select batch %d/%d: selected %d nodes (total: %d/%d)",
            result.batch_idx + 1,
            total_batches,
            len(valid_selections[:remaining]),
            selected_count,
            max_select,
        )

    return nodes
