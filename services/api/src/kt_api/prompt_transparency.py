"""Prompt transparency endpoint.

Exposes all LLM system prompts used in the knowledge pipeline so users
can understand how AI was used to produce content. This supports
research credibility and reproducibility.
"""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel, Field

router = APIRouter(prefix="/api/v1/prompts", tags=["prompt-transparency"])


class PromptEntry(BaseModel):
    id: str
    name: str
    stage: str
    purpose: str
    prompt: str


class PromptTransparencyResponse(BaseModel):
    prompts: list[PromptEntry] = Field(default_factory=list)


def _load_prompts() -> list[PromptEntry]:
    """Load all system prompts from across the codebase."""
    entries: list[PromptEntry] = []

    # ── Synthesis ──────────────────────────────────────────────
    try:
        from kt_worker_synthesis.prompts.synthesizer import SYNTHESIZER_SYSTEM_PROMPT

        entries.append(
            PromptEntry(
                id="synthesizer",
                name="Synthesizer Agent",
                stage="Synthesis",
                purpose="Navigates the knowledge graph with an exploration budget and produces a standalone research document. Uses 8 navigation tools to search nodes, read facts, trace paths, and analyze evidence.",
                prompt=SYNTHESIZER_SYSTEM_PROMPT,
            )
        )
    except ImportError:
        pass

    try:
        from kt_worker_synthesis.prompts.super_synthesizer import SUPER_SYNTHESIZER_SYSTEM_PROMPT

        entries.append(
            PromptEntry(
                id="super_synthesizer",
                name="Super-Synthesizer Agent",
                stage="Synthesis",
                purpose="Reads multiple sub-synthesis documents produced by independent synthesizer agents and produces a comprehensive meta-synthesis that cross-pollinates findings across scopes.",
                prompt=SUPER_SYNTHESIZER_SYSTEM_PROMPT,
            )
        )
    except ImportError:
        pass

    # ── Fact Extraction ────────────────────────────────────────
    try:
        from kt_facts.processing.entity_extraction import _NODE_EXTRACTION_SYSTEM

        entries.append(
            PromptEntry(
                id="entity_extraction",
                name="Entity Extraction",
                stage="Fact Decomposition",
                purpose="Extracts entities, concepts, events, and locations mentioned in each fact. Maps each fact to the knowledge graph nodes it references.",
                prompt=_NODE_EXTRACTION_SYSTEM,
            )
        )
    except ImportError:
        pass

    try:
        from kt_facts.author import _LLM_SYSTEM_PROMPT as AUTHOR_PROMPT

        entries.append(
            PromptEntry(
                id="author_extraction",
                name="Author Extraction",
                stage="Fact Decomposition",
                purpose="Extracts author person and organization metadata from source content headers and metadata.",
                prompt=AUTHOR_PROMPT,
            )
        )
    except ImportError:
        pass

    try:
        from kt_facts.processing.cleanup import _EVALUATE_SYSTEM_PROMPT

        entries.append(
            PromptEntry(
                id="fact_cleanup",
                name="Fact Quality Validation",
                stage="Fact Decomposition",
                purpose="Validates extracted facts for quality, relevance, and correctness before they are added to the knowledge base.",
                prompt=_EVALUATE_SYSTEM_PROMPT,
            )
        )
    except ImportError:
        pass

    # ── Node Pipeline ──────────────────────────────────────────
    try:
        from kt_models.dimensions import DIMENSION_SYSTEM_PROMPT

        entries.append(
            PromptEntry(
                id="dimension_analysis",
                name="Dimension Analysis",
                stage="Node Pipeline",
                purpose="Each AI model independently analyzes a node's facts to produce a dimension — a reasoned interpretation of the evidence. Multiple models produce different dimensions, revealing where they converge or diverge.",
                prompt=DIMENSION_SYSTEM_PROMPT,
            )
        )
    except ImportError:
        pass

    try:
        from kt_worker_nodes.pipelines.definitions.pipeline import _DEFINITION_SYSTEM_PROMPT

        entries.append(
            PromptEntry(
                id="definition_synthesis",
                name="Definition Synthesis",
                stage="Node Pipeline",
                purpose="Synthesizes a node's definition by combining insights from multiple dimension analyses into a single coherent description.",
                prompt=_DEFINITION_SYSTEM_PROMPT,
            )
        )
    except ImportError:
        pass

    try:
        from kt_worker_nodes.pipelines.edges.classifier import EDGE_RESOLUTION_SYSTEM_PROMPT

        entries.append(
            PromptEntry(
                id="edge_resolution",
                name="Edge Resolution",
                stage="Node Pipeline",
                purpose="Documents relationships between concept pairs by analyzing shared facts and generating justifications for edges in the knowledge graph.",
                prompt=EDGE_RESOLUTION_SYSTEM_PROMPT,
            )
        )
    except ImportError:
        pass

    try:
        from kt_worker_nodes.pipelines.parent.pipeline import _SYSTEM_PROMPT as PARENT_PROMPT

        entries.append(
            PromptEntry(
                id="parent_resolution",
                name="Parent Resolution",
                stage="Node Pipeline",
                purpose="Determines the appropriate parent category node for newly created nodes in the knowledge graph hierarchy.",
                prompt=PARENT_PROMPT,
            )
        )
    except ImportError:
        pass

    # ── Composite Nodes ────────────────────────────────────────
    try:
        from kt_worker_nodes.pipelines.composite.prompts import (
            PERSPECTIVE_SYSTEM_PROMPT,
        )
        from kt_worker_nodes.pipelines.composite.prompts import (
            SYNTHESIS_SYSTEM_PROMPT as COMPOSITE_SYNTHESIS_PROMPT,
        )

        entries.append(
            PromptEntry(
                id="composite_synthesis",
                name="Composite Node Synthesis",
                stage="Composite Nodes",
                purpose="Synthesizes multiple source nodes into a single composite node definition, weaving evidence from all sources into a coherent narrative.",
                prompt=COMPOSITE_SYNTHESIS_PROMPT,
            )
        )
        entries.append(
            PromptEntry(
                id="perspective_analysis",
                name="Perspective Analysis",
                stage="Composite Nodes",
                purpose="Analyzes debatable claims by examining supporting and challenging evidence with radical source neutrality.",
                prompt=PERSPECTIVE_SYSTEM_PROMPT,
            )
        )
    except ImportError:
        pass

    # ── Q&A Synthesis (shared library) ─────────────────────────
    try:
        from kt_agents_core.synthesis import SYNTHESIS_SYSTEM_PROMPT

        entries.append(
            PromptEntry(
                id="qa_synthesis",
                name="Q&A Synthesis",
                stage="Synthesis",
                purpose="Synthesizes answers from navigated nodes by selectively retrieving facts and weaving evidence into a coherent narrative. Used by the composite node pipeline.",
                prompt=SYNTHESIS_SYSTEM_PROMPT,
            )
        )
    except ImportError:
        pass

    return entries


@router.get("", response_model=PromptTransparencyResponse)
async def get_prompts() -> PromptTransparencyResponse:
    """Return all LLM system prompts used in the knowledge pipeline.

    This endpoint supports prompt transparency — users can see exactly
    what instructions the AI models receive at each stage of the pipeline.
    """
    return PromptTransparencyResponse(prompts=_load_prompts())
