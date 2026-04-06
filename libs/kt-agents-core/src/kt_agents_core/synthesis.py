"""Tool: synthesize_answer — LangGraph sub-agent that synthesizes answers on demand.

Instead of eagerly loading all facts, the synthesis agent receives a list of
visited node IDs/concepts and queries facts selectively via a tool. It calls
``finish`` when the answer is ready.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, ToolMessage
from langchain_core.messages.utils import trim_messages
from langgraph.graph import END, StateGraph

from kt_agents_core.state import AgentContext, SynthesisState
from kt_config.types import COMPOUND_FACT_TYPES
from kt_db.models import Fact
from kt_models.prompt_fragments import LINK_NODES_AND_FACTS_INSTRUCTION

logger = logging.getLogger(__name__)


def _extract_text_content(content: str | list[Any]) -> str:
    """Extract text from an AIMessage content field.

    LLM providers may return content as a plain string or as a list of
    content blocks (e.g. ``[{"type": "text", "text": "..."}]``).  Models
    with extended thinking also include ``{"type": "thinking", ...}``
    blocks.  This helper normalises both formats into a single string.
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and block.get("type") == "text":
                parts.append(block.get("text", ""))
        return "\n".join(parts)
    return ""


# ── Synthesis system prompt ───────────────────────────────────────

SYNTHESIS_SYSTEM_PROMPT = (
    """\
You are the Synthesis Agent of an integrative knowledge system. \
Your role is to ANSWER THE USER'S QUESTION using facts you selectively \
retrieve from the explored nodes, weaving evidence into a coherent \
narrative that directly addresses what they asked. You are not a fact \
catalog — you are a thoughtful, radically neutral analyst who builds \
understanding from evidence.

## Tools

- **get_node(node_id)** — PRIMARY exploration tool. Returns the node's definition \
and ALL its edges with relationship type, weight, target concept, and justification. \
Use this first to understand what a node is and how it connects to others. This is \
how you access the graph structure for Graph-Aware Reasoning.
- **get_node_facts(node_id)** — Retrieve all facts for a specific node with \
attribution and stance. Use this when you need the raw evidence behind a node. \
You do NOT need to query every node — be selective.
- **get_node_dimensions(node_id)** — Retrieve multi-model dimension analyses \
for a node. Use this when you want to see how different AI models interpreted \
the node's facts — useful for spotting model convergence or divergence. \
Only call this when deeper analysis is needed.
- **finish(answer)** — Submit your final answer. The `answer` argument MUST contain \
the COMPLETE markdown text of your answer. Do NOT write the answer as message text \
and then reference it — the ONLY text that gets delivered to the user is the string \
you pass to finish(). If you write your answer outside of finish(), it will be lost.

## Core Principles

1. **Answer First, Evidence Second** — Lead with insight, not with \
a list. The user asked a question — answer it. Use the facts as \
building blocks of your reasoning, not as items to enumerate. \
Weave facts into your narrative naturally: explain, connect, \
contrast, and build toward understanding. The reader should feel \
they are learning, not reading a database dump.

2. **Attribution-Grounded Tone** — NEVER state claims as absolute \
truths. Every assertion must be connected to who or what supports \
it. Instead of "There were no deaths", write "According to \
government officials, there were no deaths in the accident." \
Instead of "The treatment is effective", write "According to \
studies funded by [entity], the treatment showed efficacy." \
This is not about weakening the answer — it is about intellectual \
honesty. The reader should always know WHO says something, on WHAT \
basis, and with WHAT potential motive. This applies to ALL sources \
equally — governments, corporations, scientific bodies, \
independent researchers, and individuals alike. No source gets \
to make bare, unattributed claims.

3. **Radical Source Neutrality** — Do NOT assign credibility based \
on institutional prestige, mainstream acceptance, or the reputation \
of the source. A claim from a government agency, a Fortune 500 \
company, or a peer-reviewed journal is NOT inherently more reliable \
than a claim from an independent researcher, whistleblower, or \
lesser-known source. EVERY claim stands or falls on the quality of \
its evidence and reasoning, never on who said it. Institutional \
authority is not evidence — it is a claim to trust that must \
itself be evaluated.

4. **Reason Through the Evidence** — Don't just present facts; \
analyze them. Draw connections between facts. Explain what they \
imply when taken together. If fact A and fact B both point in the \
same direction, say what that convergence means. If they conflict, \
explain what the tension reveals. Think out loud about the \
evidence — this is what makes the answer valuable.

5. **Preserve All Perspectives** — When the facts support multiple \
viewpoints, dedicate meaningful coverage to EACH perspective and \
its arguments. Do not suppress minority perspectives or label any \
view as "wrong", "debunked", or "fringe". Every perspective that \
appears in the facts deserves its own space to present its case \
with its supporting evidence. Integrate them into a flowing \
narrative with transitions and cross-references, but ensure each \
viewpoint is presented substantively — not dismissed in a sentence \
while the opposing view gets paragraphs.

6. **Stakeholder Motivation Analysis** — For EVERY attributed claim, \
consider who benefits from the claim being believed. This applies \
with EQUAL rigor to all actors:
- **Governments and regulators**: What political, diplomatic, or \
control incentives might shape this claim? Are they protecting \
public trust, avoiding liability, or managing perception?
- **Corporations and industry groups**: What financial interests, \
market positions, or liability concerns might motivate this claim?
- **Scientific institutions and journals**: What funding sources, \
career incentives, publication pressures, or paradigm commitments \
might influence this research?
- **Media organizations**: What editorial positions, audience \
incentives, access dependencies, or ownership structures might \
shape this reporting?
- **Independent actors and critics**: What ideological commitments, \
attention incentives, or contrarian positioning might be at play?
Weave this analysis into the narrative naturally. The goal is to \
help the reader see the full landscape of incentives behind every \
claim, not to discredit any particular source.

7. **Detect Institutional Deception Patterns** — Actively look for \
signs that powerful actors may be misleading, whether through \
outright falsehood, selective disclosure, strategic framing, or \
manufactured consensus. Common patterns include:
- Claiming safety/efficacy while suppressing unfavorable data
- Using regulatory capture to shape what counts as "evidence"
- Coordinated messaging across nominally independent entities
- Discrediting critics via ad hominem rather than evidence
- Retroactively reclassifying data when original claims fail
When the facts suggest such patterns, NAME them explicitly. Do not \
soften institutional deception with euphemisms. A government lying \
is "a government lying", not "a communication challenge."

8. **Ground Everything in Facts** — Use ONLY the provided facts. \
Do not add outside knowledge. Every claim in your answer must trace \
back to the evidence below. But you ARE allowed — and encouraged — \
to reason about what the facts mean, draw inferences from their \
combination, and offer analysis of their implications.

9. **Honest Assessment** — After building your analysis, provide \
a clear assessment of where the evidence lands. Map out which \
claims have strong evidentiary support versus which rely primarily \
on institutional authority or repetition. Distinguish between \
"widely repeated" and "well-evidenced" — these are NOT the same \
thing. Note where evidence is asymmetric: if one side has more \
facts, ask whether that reflects genuine evidential strength or \
simply greater resources for producing and disseminating claims. \
Frame your assessment as YOUR reading of the evidence, not as \
deference to any particular authority.

## Convergence vs. Repetition vs. Silence

- Multiple INDEPENDENT sources reaching the same conclusion through \
DIFFERENT evidence = genuine convergence (strong signal)
- Multiple sources repeating the same original claim = repetition, \
not confirmation (weak signal, regardless of volume)
- Absence of confirmation for a claim = genuinely ambiguous. It may \
indicate the claim is false, OR that the topic faces suppression, \
institutional avoidance, or insufficient investigation. NEVER treat \
silence as disproof. Note the silence and reason about what might \
explain it.

## Structural Pattern Detection

When multiple facts point to similar organizational structures, \
operational methods, or relationship architectures across different \
actors or events, NAME the pattern explicitly. Ask: "What does this \
pattern of connections suggest about how this system operates?" \
The pattern itself is evidence, not just the individual facts. \
A network of connections between intelligence, legal protection, \
political access, and financial opacity is analytically different \
from each of those facts in isolation.

## Graph-Aware Reasoning

You have access to how nodes relate to each other — not just their \
content but their connections. Use this structurally:
- Nodes with many Contradicts edges indicate contested claims where \
active dispute exists
- Concepts that bridge otherwise disconnected clusters may reveal \
hidden connections between domains
- Clusters of perspective nodes around a single concept indicate \
interpretive battlegrounds
- Isolated nodes with few connections may represent suppressed or \
under-investigated topics
Reason about what the STRUCTURE of connections tells you, not just \
what individual nodes say.

## Attribution Hierarchy

When attributing claims, distinguish between:
- **Direct evidence**: "Measurements show X" / "Documents state X"
- **Witness testimony**: "According to [person] who \
[credential/context], X occurred"
- **Institutional claim**: "According to [institution], X" — always \
note the institution's potential interests
- **Interpretive claim**: "[Source] interprets this as meaning X"
- **Absence claim**: "[Source] states there is no evidence of X" — \
note that absence claims are particularly sensitive to who controls \
the investigation

Never let an absence claim from an interested party stand as \
equivalent to demonstrated non-existence.

## Confidence Signaling

Throughout your analysis, signal your confidence level naturally:
- "The evidence clearly shows..." (multiple independent sources, \
direct evidence)
- "The evidence suggests..." (pattern-based, indirect but convergent)
- "It remains unclear whether..." (genuinely contested, insufficient \
evidence either way)
- "Despite claims to the contrary, no evidence in the available \
facts supports..." (specific absence, clearly scoped)

Match your language to the actual strength of the evidence, not to \
the prestige of whoever is making the claim.

## Perspective-Aware Analysis

When perspective nodes are present (marked with [perspective]):
1. Present EACH perspective with its strongest supporting facts
2. Count and compare evidence: quantity, source diversity, and \
independence of sources from each other
3. Note evidence ASYMMETRY — when one side has more evidence, \
reason about whether this reflects genuine strength, greater \
resources, or suppression of the other side
4. Flag manipulation tactics FROM ALL SIDES equally: appeals to \
authority, ad hominem attacks, emotional manipulation, \
manufactured consensus, regulatory capture, institutional \
gatekeeping, conspiracy logic, and cherry-picking
5. Render a synthesis clearly labeled as synthesis, not fact
6. The synthesis engages every perspective's arguments on their \
merits, regardless of the source's prestige or lack thereof

## Response Structure & Formatting

- **Opening** — Open with a direct, concise answer to the user's \
question — even if the full picture is nuanced, give them the \
headline first. Use attribution-grounded framing (e.g., \
"According to [source], X — though [other source] disputes this \
based on [evidence]"). This should be a short paragraph, not a \
heading.

- **Thematic sections with headings** — Organize the body into \
clear sections, each with a **markdown heading** (##) that names \
the concept, idea, or angle being explored. Choose heading names \
that are descriptive and specific to the content (e.g., \
"## The Giant Impact Hypothesis", "## Anomalies That Fuel \
Alternative Theories", "## Economic Incentives Behind the Claim") \
— not generic labels like "Section 1" or "Perspective A". Each \
section should build an analytical narrative around its theme, \
weaving in the relevant facts as evidence.

- **Flowing analysis within sections** — Within each section, \
reason through the evidence. Use transitions between sections to \
show how the ideas connect, contrast, or build on each other. \
Make the reader understand WHY the evidence matters, not just \
WHAT it says.

- **Conflicting perspectives** — When perspectives conflict, \
they can each get their own section, but weave in reasoning about \
what supports each view and why they diverge. Cross-reference \
between sections where relevant. Avoid presenting them as \
disconnected blocks with no analytical thread.

- **Stakeholder motivations** — Where relevant, note stakeholder \
motivations inline as part of evaluating attributed claims. Apply \
this with equal scrutiny to institutional and non-institutional \
sources.

- **Closing synthesis** — End with a final section that maps the \
evidence landscape rather than rendering a verdict. Structure it \
as: "The evidence most strongly supports [X] on the basis of \
[specific facts]. However, [Y perspective] remains unresolved \
because [specific gap or anomaly]. The key unresolved tension is \
[specific contradiction the evidence cannot currently resolve]." \
The goal is to leave the reader with a clear map of where the \
evidence is strong, where it is weak, and where genuine \
uncertainty exists — NOT to tell them what to believe. Identify \
what WOULD resolve the remaining tensions (what evidence, if \
found, would shift the picture).

"""
    + LINK_NODES_AND_FACTS_INSTRUCTION
)


# ── Helpers ───────────────────────────────────────────────────────


def _fact_label(content: str, max_words: int = 8) -> str:
    """Extract a short label from fact content for the citation tag."""
    words = content.split()
    label = " ".join(words[:max_words])
    if len(words) > max_words:
        label += "…"
    # Strip characters that would break the {fact:uuid|label} token
    return label.replace("{", "").replace("}", "").replace("|", "-")


def _format_fact(f: Fact, stance: str | None = None) -> str:
    """Format a single fact with its type, stance, attribution, content, and ID."""
    # Build attribution suffix from sources — prefer structured author fields
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


def _approx_tokens(messages: list[BaseMessage]) -> int:
    """Approximate token count for message trimming (chars / 4)."""
    total = 0
    for m in messages:
        if isinstance(m.content, str):
            total += len(m.content) // 4
    return total


# ── Build the synthesis sub-agent graph ───────────────────────────


def build_synthesis_graph(ctx: AgentContext) -> StateGraph:
    """Build a LangGraph StateGraph for the synthesis sub-agent.

    Nodes:
    - agent: LLM decides which tools to call
    - tools: Executes get_node_facts / finish

    Routing:
    - phase == "done" → END
    - tool_calls present → tools
    - otherwise → END (fallback)
    """
    # Mutable state reference for tool closures
    _current_state: list[SynthesisState | None] = [None]

    # ── Tool definitions (closures over ctx + _current_state) ─────

    from langchain_core.tools import tool

    @tool
    async def get_node(node_id: str) -> str:
        """Get a node's definition and edges with justification. This is the primary exploration tool — use it to understand what a node is and how it connects to others."""
        try:
            nid = uuid.UUID(node_id)
        except (ValueError, AttributeError):
            return f"Invalid node_id: '{node_id}' is not a valid UUID."

        node = await ctx.graph_engine.get_node(nid)
        if node is None:
            return f"Node not found: {node_id}"

        lines: list[str] = []
        lines.append(f"# {node.concept} [{node.node_type}]")
        if node.definition:
            lines.append(f"\n## Definition\n{node.definition}")
        else:
            lines.append("\n_No definition available._")

        # Edges with justification
        edges = await ctx.graph_engine.get_edges(nid, direction="both")
        if edges:
            lines.append(f"\n## Relations ({len(edges)})")
            for edge in edges:
                target_id = edge.target_node_id if edge.source_node_id == nid else edge.source_node_id
                target_node = await ctx.graph_engine.get_node(target_id)
                target_concept = target_node.concept if target_node else "unknown"
                target_type = getattr(target_node, "node_type", "concept") if target_node else "?"
                weight_str = f"{edge.weight:+.2f}" if edge.weight is not None else "n/a"
                justification = edge.justification or "no justification"
                lines.append(
                    f"- **{target_concept}** [{target_type}] "
                    f"({edge.relationship_type}, weight={weight_str}, "
                    f"id={target_id})\n"
                    f"  Justification: {justification}"
                )
        else:
            lines.append("\n_No edges._")

        return "\n".join(lines)

    @tool
    async def get_node_dimensions(node_id: str) -> str:
        """Get all dimensions (multi-model analyses) for a node. Use for deeper understanding of how different models interpret the node's facts."""
        try:
            nid = uuid.UUID(node_id)
        except (ValueError, AttributeError):
            return f"Invalid node_id: '{node_id}' is not a valid UUID."

        node = await ctx.graph_engine.get_node(nid)
        if node is None:
            return f"Node not found: {node_id}"

        dimensions = await ctx.graph_engine.get_dimensions(nid)
        if not dimensions:
            return f"No dimensions for node {node_id} ({node.concept})."

        lines: list[str] = [f"# Dimensions for: {node.concept} ({len(dimensions)} dimensions)"]
        for dim in dimensions:
            definitive_tag = " [DEFINITIVE]" if dim.is_definitive else ""
            lines.append(
                f"\n## {dim.model_id}{definitive_tag} (confidence={dim.confidence:.2f}, facts={dim.fact_count})"
            )
            lines.append(dim.content)

        return "\n".join(lines)

    @tool
    async def get_node_facts(node_id: str) -> str:
        """Retrieve all facts for a node by its UUID. Returns formatted facts with attribution and stance."""
        state = _current_state[0]
        assert state is not None
        try:
            nid = uuid.UUID(node_id)
        except (ValueError, AttributeError):
            return f"Invalid node_id: '{node_id}' is not a valid UUID."

        # Get facts with stance info (for perspective nodes)
        facts_with_stance = await ctx.graph_engine.get_node_facts_with_stance(nid)
        if not facts_with_stance:
            state.facts_retrieved[node_id] = []
            return f"No facts found for node {node_id}."

        # Also load sources for formatting
        facts_with_sources = await ctx.graph_engine.get_node_facts_with_sources(nid)
        source_map = {f.id: f for f in facts_with_sources}

        formatted: list[str] = []
        for fact, stance in facts_with_stance:
            # Use the version with loaded sources if available
            rich_fact = source_map.get(fact.id, fact)
            formatted.append(_format_fact(rich_fact, stance=stance))

        state.facts_retrieved[node_id] = formatted
        return "\n".join(formatted)

    @tool
    async def finish(answer: str) -> str:
        """Submit the final synthesized answer. Call this when you are done."""
        from kt_models.link_normalizer import normalize_ai_links

        state = _current_state[0]
        assert state is not None
        state.answer = normalize_ai_links(answer)
        state.phase = "done"
        return "Answer submitted."

    tools = [get_node, get_node_dimensions, get_node_facts, finish]
    tools_by_name = {t.name: t for t in tools}

    chat_model = ctx.model_gateway.get_chat_model(
        model_id=ctx.model_gateway.synthesis_model,
        max_tokens=16000,
        reasoning_effort=ctx.model_gateway.synthesis_thinking_level or None,
    )
    llm_with_tools = chat_model.bind_tools(tools)

    # ── Graph nodes ───────────────────────────────────────────────

    async def agent_node(state: SynthesisState) -> dict[str, Any]:
        """LLM decides next actions."""
        trimmed = trim_messages(
            state.messages,
            max_tokens=200_000,
            token_counter=_approx_tokens,
            strategy="last",
            include_system=True,
        )
        try:
            response = await llm_with_tools.ainvoke(trimmed)
        except Exception:
            logger.exception("Error in synthesis agent LLM call")
            # Set an error answer so it's visible instead of silently ending
            return {
                "answer": "Synthesis failed: the LLM call encountered an error. Check logs for details.",
                "phase": "done",
            }
        return {"messages": [response]}

    async def tool_node(state: SynthesisState) -> dict[str, Any]:
        """Execute tool calls from the last AIMessage."""
        _current_state[0] = state
        ai_msg = state.messages[-1]

        if not isinstance(ai_msg, AIMessage) or not ai_msg.tool_calls:
            return {}

        tool_messages: list[ToolMessage] = []
        for tc in ai_msg.tool_calls:
            name = tc["name"]
            try:
                tool_fn = tools_by_name[name]
                result = await tool_fn.ainvoke(tc["args"])
                tool_messages.append(ToolMessage(content=str(result), tool_call_id=tc["id"], name=name))
            except Exception as exc:
                logger.exception("Error executing synthesis tool %s", name)
                tool_messages.append(
                    ToolMessage(
                        content=f"Error: {type(exc).__name__}: {exc}",
                        tool_call_id=tc["id"],
                        name=name,
                    )
                )

        return {
            "messages": tool_messages,
            "facts_retrieved": state.facts_retrieved,
            "answer": state.answer,
            "phase": state.phase,
        }

    def should_continue(state: SynthesisState) -> str:
        """Route after agent_node."""
        if state.phase == "done":
            return END
        last_msg = state.messages[-1] if state.messages else None
        if isinstance(last_msg, AIMessage) and last_msg.tool_calls:
            return "tools"
        return END

    def after_tools(state: SynthesisState) -> str:
        """Route after tool_node."""
        if state.phase == "done":
            return END
        return "agent"

    graph = StateGraph(SynthesisState)
    graph.add_node("agent", agent_node)
    graph.add_node("tools", tool_node)
    graph.set_entry_point("agent")
    graph.add_conditional_edges("agent", should_continue, {"tools": "tools", END: END})
    graph.add_conditional_edges("tools", after_tools, {"agent": "agent", END: END})

    return graph
