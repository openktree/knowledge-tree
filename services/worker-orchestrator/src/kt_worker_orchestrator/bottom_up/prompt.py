"""Bottom-up scope pipeline prompts — perspective planner and node prioritizer.

These are single-call LLM prompts (not agent loop prompts):
1. Perspective planner: generate thesis/antithesis pairs after nodes are built
2. Node prioritizer: assign priority scores + perspectives for user selection
"""

from __future__ import annotations

# ── Perspective planner ──────────────────────────────────────────────────

PERSPECTIVE_SYSTEM = """\
You are a perspective planner for a knowledge graph built on Hegelian dialectics. \
You will receive a scope description and a list of concept/entity/event/location nodes that \
were just built. Your job: identify genuine debates, controversies, or tensions \
in this domain and propose thesis/antithesis perspective pairs.

Rules for good perspectives:
- Both thesis and antithesis must make an AFFIRMATIVE case (the antithesis argues \
FOR something, not just against the thesis)
- Reference specific mechanisms, evidence, trade-offs, or causal claims
- Steelman both sides — frame each as its proponents would
- Each side should naturally connect to different concepts in the node list
- source_concept should be the name of the most relevant parent concept node

Bad: "X is good" vs "X is bad" (simple negation)
Good: "Germline editing prevents lifetime suffering for thousands" vs \
"Germline modifications propagate without descendant consent, crossing an irreversible boundary"

Call the propose_perspective tool once for each perspective pair you identify. \
If no genuine debates exist in this domain, do not call the tool."""

PERSPECTIVE_USER = """\
Scope: "{scope}"

Built nodes ({count} total):
{node_list}

{content_context}\
Identify thesis/antithesis pairs for genuine debates in this domain. \
Plan up to {max_perspectives} perspective pairs."""


# ── Node prioritizer ────────────────────────────────────────────────────

PRIORITIZE_SYSTEM = """\
You are a node prioritizer for a knowledge graph builder. You will receive a list of \
extracted nodes along with the original user query and a content summary.

Your job: assign each node a priority (0-10) and decide whether it should be selected \
by default for building. Consider:

- **Relevance** to the original query (directly related = higher priority)
- **Specificity** (well-defined concepts > vague/broad topics)
- **Information density** (nodes referenced by many facts are more valuable)
- **Novelty** (nodes that add new knowledge vs trivially obvious ones)
- **Completeness** of the name (names should be complete and unambiguous; if a name \
  seems incomplete or too abbreviated, still include it but note it)
- **Event specificity**: Events that could be ambiguous MUST include key subjects in the \
  name. "May 2021 marriage" → "May 2021 marriage of [Person A] and [Person B]". \
  Well-known events with universally recognised names (e.g. "Tiananmen Square Massacre", \
  "Apollo 11 Moon Landing") do NOT need extra qualification. The test: would a reader \
  unfamiliar with the query understand exactly what the event refers to from the name alone?

Selection guidelines:
- Priority 8-10: Core to the query, should be selected
- Priority 5-7: Relevant but secondary, select by default
- Priority 3-4: Tangential, deselect by default
- Priority 0-2: Barely relevant, deselect

For each node, also identify any thesis/antithesis perspective pairs that relate to it. \
Perspectives are debates, controversies, or genuine tensions. Both sides must make an \
AFFIRMATIVE case. Perspectives are free to generate (no cost to the user).

You may RENAME nodes to improve clarity (especially ambiguous events). When you rename, \
include "original_name" so the system can match it back.

Respond with ONLY a JSON object:
{"nodes": [{"name": "...", "original_name": "...", "node_type": "...", "priority": 7, \
"selected": true, "perspectives": [{"claim": "...", "antithesis": "..."}]}]}

If you did not rename the node, omit "original_name". \
If a node has no perspectives, use an empty array: "perspectives": []"""

PRIORITIZE_USER = """\
User query: "{query}"

Content summary:
{content_summary}

Extracted nodes ({count} total):
{node_list}

Assign priority (0-10) and selected flag to each node. \
Also identify perspective pairs for nodes where genuine debates exist."""
