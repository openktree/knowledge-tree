"""Wave planner — shared LLM-based planning for orchestrator wave pipeline.

Extracted from workers/orchestrator.py so both in-process and stream-based
orchestrators can import and reuse the planning logic.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from kt_worker_orchestrator.agents.orchestrator_state import ScopeBriefing, ScopePlan

logger = logging.getLogger(__name__)


# ── Wave planner prompt ──────────────────────────────────────────

WAVE_PLANNER_PROMPT = """\
You are the wave planner for an integrative knowledge graph system. You are a \
strategic research coordinator — your purpose is to plan scoped explorations \
that together cover a query comprehensively. You output a JSON array of scopes \
to explore in the current wave.

Each scope will be executed by an isolated sub-explorer agent that gathers \
facts, builds concepts/entities/perspectives within its scope, and returns a \
briefing summary. Sub-explorers operate in isolation to avoid anchoring bias.

## Output Format (STRICT JSON, no markdown fencing):
[
  {{"scope": "<descriptive scope>", "explore_budget": <int>, "nav_budget": <int>}},
  ...
]

Output ONLY the JSON array, nothing else.

## Good Scope Examples

Scopes should be focused investigation areas, not single keywords:
- "construction techniques and engineering methods of the giza pyramids"
- "historical timeline and pharaohs associated with giza pyramid construction"
- "archaeological discoveries and controversies at the giza plateau"
- "evidence for and against vaccine-autism link in clinical research"
- "vaccine safety monitoring systems and adverse event reporting"
- "economic arguments for universal basic income"
- "counterarguments and implementation challenges of universal basic income"

Bad scopes (too vague or too narrow):
- "pyramids" (too vague — what aspect?)
- "limestone" (too narrow — won't yield enough to justify a sub-explorer)

## Wave Strategy

**BREADTH OVER DEPTH:** Prefer many small sub-explorers over fewer large ones. \
Each sub-explorer should get 3-5 explore budget (max 5). This yields more diverse \
coverage and lets later waves make informed decisions about where to invest more.

**Do NOT pre-plan depth.** Wave 1 should be broad. Let Wave 1 results drive \
Wave 2 decisions about where to go deeper.

**Wave 1 — Broad Coverage (~60% of explore budget):**
- Plan 3-4+ scopes covering as many DISTINCT angles of the query as possible
- Each scope gets 3-5 explore budget
- Focus on breadth: cover the full landscape of the topic

**Subsequent Waves — Informed by Prior Waves (remaining budget):**
- Based on ALL prior wave briefings, plan targeted follow-up scopes that:
  - Fill gaps or weak areas revealed by prior waves
  - Explore surprising tangents or connections discovered
  - Add opposing/complementary perspectives if prior waves were one-sided
  - Deepen ONLY where prior waves revealed unexpected complexity
- The whole point of multiple waves is that subsequent waves are INFORMED by \
prior wave results — if you plan the same scopes you would have planned \
without briefings, you defeat this purpose. Later waves should address gaps \
and surprises from earlier waves, NOT pre-planned topics.

## Budget Allocation Guidance

Each sub-explorer should get 3-5 explore budget (max 5 per scope). \
Total explore across all scopes MUST NOT exceed the wave explore budget. \
Total nav across all scopes MUST NOT exceed the wave nav budget.

Examples for different budget levels:
- wave_explore=12: 3x4 or 4x3 scopes
- wave_explore=9: 3x3 scopes
- wave_explore=6: 2x3 scopes
- wave_explore=4: 1x4 scope
- wave_explore=3: 1x3 scope

(Notation: "3x4" means 3 scopes with 4 explore each)

**Nav budget per scope:** Give each sub-explorer at least 5x its explore budget \
in nav budget. Sub-explorers need nav to read back nodes they built, check \
suggested_concepts from dimensions, and inspect existing nodes. Example: a \
scope with explore=5 should get nav=25. Distribute nav budget generously.

## Perspective Balance Protocol — CORE PRINCIPLE

This system's purpose is to gather facts from ALL perspectives and build a \
larger integrated picture. It is NOT a debate tool — it builds comprehensive \
understanding by representing every viewpoint on its own terms.

When the query involves a debatable topic:
- Dedicate at least one scope to each major position's AFFIRMATIVE case
- Do NOT just search for "why X is wrong" — search for the positive case \
of each position using language its proponents would use
- Thesis/antithesis pairs must make affirmative, independent arguments with \
specific mechanisms — not simple negations or noun swaps
- The goal is a rich graph where ALL sides are represented with genuine \
evidence, not a one-sided argument

## Core Principle: BE CURIOUS

Your job is to leave the knowledge graph richer than you found it. Plan scopes \
that ensure BREADTH and BALANCE across the full topic. Always explore at least \
one tangent or related angle beyond the obvious.
"""


def build_wave_planner_user_msg(
    query: str,
    wave: int,
    total_waves: int,
    wave_explore: int,
    wave_nav: int,
    briefings: list[ScopeBriefing],
    scout_results: dict[str, Any],
) -> str:
    """Build the user message for the wave planner LLM call."""
    parts = [
        f'Query: "{query}"',
        f"Wave {wave} of {total_waves}",
        f"Wave explore budget: {wave_explore}",
        f"Wave nav budget: {wave_nav}",
    ]

    if scout_results:
        scout_lines = []
        for q, data in scout_results.items():
            ext_count = len(data.get("external", []))
            graph_count = len(data.get("graph_matches", []))
            graph_names = [m.get("concept", "?") for m in data.get("graph_matches", [])[:5]]
            scout_lines.append(f"  '{q}': {ext_count} external, {graph_count} graph matches {graph_names}")
        parts.append("\nScout results:\n" + "\n".join(scout_lines))

    if briefings:
        parts.append("\nPrior wave briefings:")
        for b in briefings:
            parts.append(
                f"  Wave {b.wave}, scope '{b.scope}': "
                f"{len(b.created_nodes)} nodes, {b.gathered_fact_count} facts. "
                f"Summary: {b.summary[:200]}"
            )

    return "\n".join(parts)


class WavePlanParseError(ValueError):
    """Raised when the wave planner LLM response cannot be parsed into scope plans."""


def extract_json_array(raw: str) -> str:
    """Extract a JSON array from LLM output, handling markdown fencing and preamble text."""
    text = raw.strip()

    # Strip markdown fencing if present
    if text.startswith("```"):
        lines = text.split("\n")
        lines = [line for line in lines if not line.strip().startswith("```")]
        text = "\n".join(lines).strip()

    # Find the first '[' and last ']' to handle preamble/postamble text
    bracket_start = text.find("[")
    bracket_end = text.rfind("]")
    if bracket_start >= 0 and bracket_end > bracket_start:
        text = text[bracket_start : bracket_end + 1]

    return text


def parse_scope_plans(raw: str, wave_explore: int, wave_nav: int) -> list[ScopePlan]:
    """Parse LLM response into ScopePlan objects, with budget capping.

    Raises WavePlanParseError if the response cannot be parsed.
    """
    text = extract_json_array(raw)

    try:
        plans_raw = json.loads(text)
    except json.JSONDecodeError as exc:
        raise WavePlanParseError(
            f"Invalid JSON in wave planner response: {exc}. "
            f"Raw text (first 300 chars): {raw.strip()[:300]}"
        ) from exc

    if not isinstance(plans_raw, list):
        plans_raw = [plans_raw]

    plans: list[ScopePlan] = []
    remaining_explore = wave_explore
    remaining_nav = wave_nav
    skipped: list[str] = []

    for p in plans_raw:
        if not isinstance(p, dict) or "scope" not in p:
            skipped.append(str(p)[:100])
            continue

        exp = min(int(p.get("explore_budget", 3)), remaining_explore, 5)
        nav = min(int(p.get("nav_budget", exp * 5)), remaining_nav)

        if exp <= 0 and nav <= 0:
            break

        plans.append(ScopePlan(
            scope=p["scope"],
            explore_budget=exp,
            nav_budget=nav,
            search_hints=p.get("search_hints", []),
        ))
        remaining_explore -= exp
        remaining_nav -= nav

    if not plans:
        detail = f"Skipped entries: {skipped}" if skipped else f"Parsed array: {plans_raw}"
        raise WavePlanParseError(
            f"No valid scope plans found in response. Each entry must be an object "
            f"with at least a 'scope' key. {detail}"
        )

    return plans


def generate_scout_queries(query: str) -> list[str]:
    """Generate 3-4 diverse scout queries from the user query."""
    queries = [query]
    words = query.split()
    if len(words) > 3:
        queries.append(" ".join(words[:len(words) // 2]) + " overview")
        queries.append(" ".join(words[len(words) // 2:]) + " research")
    else:
        queries.append(f"{query} overview")
        queries.append(f"{query} research perspectives")
    return queries[:4]
