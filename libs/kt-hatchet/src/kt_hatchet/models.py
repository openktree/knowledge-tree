"""Pydantic I/O models for Hatchet tasks.

Input models carry task parameters; output models carry results upstream
to parent workflows via Hatchet's return-value plumbing (replacing Redis
Sets, BudgetTracker, and BarrierManager).
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

# -- Graph-aware mixin -------------------------------------------------


class GraphAwareMixin(BaseModel):
    """Mixin that adds optional graph_id to workflow inputs.

    When graph_id is None, the workflow operates on the default graph.
    When set, workers resolve per-graph session factories via GraphSessionResolver.
    """

    graph_id: str | None = None


# -- Token usage -------------------------------------------------------


class TokenUsageSummary(BaseModel):
    """Aggregated token usage from LLM calls within a task or workflow."""

    total_prompt_tokens: int = 0
    total_completion_tokens: int = 0
    total_cost_usd: float = 0.0
    by_model: dict[str, dict[str, int | float]] = Field(default_factory=dict)
    by_task: dict[str, dict[str, int | float]] = Field(default_factory=dict)


# -- Search & decomposition --------------------------------------------


class WebSearchInput(GraphAwareMixin):
    """Input for a web search task."""

    scope_id: str
    query: str
    max_results: int = 10
    message_id: str
    conversation_id: str


class SearchOutput(BaseModel):
    """Results returned by a web search task."""

    total_facts: int = 0
    page_count: int = 0


class DecomposePageInput(GraphAwareMixin):
    """Input for page decomposition (chunking + fan-out)."""

    raw_source_id: str
    url: str
    query_context: str = ""
    message_id: str
    conversation_id: str


class DecomposePageOutput(BaseModel):
    """Results from decomposing a single page."""

    fact_count: int = 0


class DecomposeChunkInput(GraphAwareMixin):
    """Input for decomposing a single text chunk into facts."""

    raw_source_id: str
    chunk_index: int
    content: str
    concept: str = ""
    query_context: str = ""
    message_id: str
    conversation_id: str


class DecomposeChunkOutput(BaseModel):
    """Results from decomposing a single chunk."""

    fact_count: int = 0
    fact_ids: list[str] = Field(default_factory=list)


# -- Node pipeline -----------------------------------------------------


class NodePipelineInput(GraphAwareMixin):
    """Unified input for the node pipeline DAG workflow.

    Supports three modes:
    - ``create`` -- promote a seed to a full node (default)
    - ``rebuild_incremental`` -- enrich existing node with new facts
    - ``rebuild_full`` -- full rebuild: delete dims, regenerate everything
    """

    # Mode discriminator
    mode: Literal["create", "rebuild_incremental", "rebuild_full"] = "create"
    scope: Literal["all", "dimensions", "edges"] = "all"

    # Create-mode fields
    scope_id: str = ""
    concept: str = ""
    node_type: str = "concept"
    entity_subtype: str | None = None
    seed_key: str = ""  # REQUIRED for create mode -- seed to promote

    # Rebuild-mode fields
    node_id: str | None = None  # REQUIRED for rebuild modes
    recalculate_pair: bool = False  # Also rebuild dialectic pair (rebuild_full only)

    # Shared
    message_id: str = ""
    conversation_id: str = ""
    user_id: str | None = None


# Backward-compatible alias -- callers (bottom-up, ingest) use this name.
# They only set create-mode fields; rebuild fields default to None/False.
BuildNodeInput = NodePipelineInput


class BuildNodeOutput(BaseModel):
    """Results from the full node pipeline."""

    node_id: str | None = None
    edge_ids: list[str] = Field(default_factory=list)
    edge_count: int = 0


class GenerateDimensionsInput(GraphAwareMixin):
    """Input for dimension generation task (within node pipeline DAG)."""

    node_id: str
    scope_id: str
    message_id: str
    conversation_id: str
    user_id: str | None = None


class DimensionsOutput(BaseModel):
    """Results from dimension generation (includes edge results)."""

    node_id: str
    dimensions_created: int = 0
    fact_count: int = 0
    edge_ids: list[str] = Field(default_factory=list)


class GenerateDefinitionInput(GraphAwareMixin):
    """Input for definition generation task (within node pipeline DAG)."""

    node_id: str
    message_id: str
    conversation_id: str
    user_id: str | None = None


class UpdateEdgesInput(GraphAwareMixin):
    """Input for a single edge resolution task (candidates-only)."""

    node_id: str
    concept: str = ""  # node concept -- avoids graph-db lookup
    node_type: str = "concept"  # node type -- avoids graph-db lookup
    scope_id: str = ""
    message_id: str = ""
    conversation_id: str = ""
    user_id: str | None = None


class EdgeOutput(BaseModel):
    """Results from edge resolution."""

    edge_ids: list[str] = Field(default_factory=list)
    edges_created: int = 0


# -- Bottom-up exploration ---------------------------------------------


class BottomUpInput(GraphAwareMixin):
    """Input for the bottom-up exploration workflow."""

    query: str
    explore_budget: int
    nav_budget: int = 0
    conversation_id: str
    message_id: str
    user_id: str | None = None


class BottomUpScopeInput(GraphAwareMixin):
    """Input for a bottom-up scope task (gather → extract → prioritize → build)."""

    scope_id: str
    scope_description: str
    explore_slice: int
    nav_slice: int = 0
    wave_number: int = 0
    message_id: str
    conversation_id: str
    user_id: str | None = None


class BottomUpScopeOutput(BaseModel):
    """Results from a bottom-up scope task."""

    created_node_ids: list[str] = Field(default_factory=list)
    created_edge_ids: list[str] = Field(default_factory=list)
    explore_used: int = 0
    nav_used: int = 0
    briefing: str = ""
    node_count: int = 0
    extracted_count: int = 0
    super_sources: list[dict[str, Any]] = Field(default_factory=list)


# -- Bottom-up ingest (two-phase) -------------------------------------


class BottomUpPrepareScopeInput(GraphAwareMixin):
    """Input for a prepare-phase scope (fact gathering only, no node building).

    NOTE: Does NOT receive the original user query — only the scope
    description.  This isolation prevents the scope explorer from being
    biased by the orchestrator's query context.  Each scope runs its own
    scout to discover relevant terms.
    """

    scope_id: str
    scope_description: str
    explore_slice: int
    message_id: str
    conversation_id: str
    user_id: str | None = None


class BottomUpPrepareScopeOutput(BaseModel):
    """Results from a prepare-phase scope — extracted nodes + fact counts."""

    node_plans: list[dict[str, str | None]] = Field(default_factory=list)
    explore_used: int = 0
    gathered_fact_count: int = 0
    extracted_count: int = 0
    content_summary: str = ""
    source_urls: list[dict[str, str]] = Field(default_factory=list)


class ProposedPerspective(BaseModel):
    """A perspective proposed for a parent concept node."""

    claim: str
    antithesis: str


class ProposedNodeAmbiguity(BaseModel):
    """Ambiguity metadata for a proposed seed."""

    is_disambiguated: bool = False  # this seed was split from an ambiguous parent
    ambiguity_type: str | None = None  # "text" or "embedding"
    parent_name: str | None = None  # original ambiguous name
    sibling_names: list[str] = Field(default_factory=list)  # other meanings


class ProposedNode(BaseModel):
    """A single node proposed by Phase 1 of bottom-up ingest."""

    name: str
    node_type: str = "concept"
    entity_subtype: str | None = None
    priority: int = 5  # 0-10
    selected: bool = True  # AI pre-selection
    seed_key: str = ""  # REQUIRED — seed key for this node
    existing_node_id: str | None = None  # set → already in graph
    fact_count: int = 0  # number of facts linked to this seed
    aliases: list[str] = Field(default_factory=list)  # merged seed names
    perspectives: list[ProposedPerspective] = Field(default_factory=list)
    ambiguity: ProposedNodeAmbiguity | None = None


class BottomUpPrepareInput(GraphAwareMixin):
    """Phase 1: gather facts, extract candidate nodes, return proposals."""

    query: str
    explore_budget: int
    conversation_id: str
    message_id: str
    user_id: str | None = None


class BottomUpPrepareOutput(BaseModel):
    """Phase 1 results — stored on message metadata, returned to frontend."""

    fact_count: int = 0
    source_count: int = 0
    fact_previews: list[str] = Field(default_factory=list)
    proposed_nodes: list[ProposedNode] = Field(default_factory=list)
    content_summary: str = ""
    explore_used: int = 0
    source_urls: list[dict[str, str]] = Field(default_factory=list)


class ConfirmedNode(BaseModel):
    """A node confirmed by the user for Phase 2 building."""

    name: str
    node_type: str = "concept"
    entity_subtype: str | None = None
    seed_key: str = ""  # REQUIRED — seed key for this node
    existing_node_id: str | None = None  # set → enrich mode
    perspectives: list[ProposedPerspective] = Field(default_factory=list)


# -- Agent-assisted node selection --------------------------------------


class AgentSelectInput(GraphAwareMixin):
    """Input for agent-assisted node selection workflow."""

    proposed_nodes: list[ProposedNode]
    max_select: int
    instructions: str = ""
    conversation_id: str
    message_id: str
    user_id: str | None = None


class AgentSelectOutput(BaseModel):
    """Output from agent-assisted node selection."""

    proposed_nodes: list[ProposedNode] = Field(default_factory=list)


# -- Conversations -----------------------------------------------------


class QueryInput(GraphAwareMixin):
    """Input for lightweight query workflow (graph navigation + synthesis)."""

    query: str
    nav_budget: int
    conversation_id: str
    message_id: str
    user_id: str | None = None


class FollowUpInput(GraphAwareMixin):
    """Input for follow-up conversation turns."""

    follow_up_query: str
    original_query: str
    nav_budget: int
    explore_budget: int
    mode: str = "query"  # "query" | "ingest"
    wave_count: int = 1
    conversation_id: str
    message_id: str
    user_id: str | None = None


class ResynthesizeInput(GraphAwareMixin):
    """Input for re-synthesis without re-exploration."""

    query: str
    conversation_id: str
    message_id: str
    user_id: str | None = None


class IngestConfirmInput(GraphAwareMixin):
    """Input for the ingest confirmation workflow."""

    nav_budget: int
    selected_chunks: list[int] | None = None
    conversation_id: str
    message_id: str
    user_id: str | None = None
    # Per-ingest opt-out for the multigraph public-cache contribute hook.
    # Defaults to True so the public graph keeps growing with normal use.
    # API forces this to False server-side for file-only ingests regardless
    # of client value (file uploads are always private).
    share_with_public_graph: bool = True


class IngestDecomposeInput(GraphAwareMixin):
    """Input for the phased ingest decompose workflow (Phase 1)."""

    conversation_id: str
    message_id: str
    selected_chunks: list[int] | None = None
    user_id: str | None = None
    # Per-ingest opt-out for the multigraph public-cache contribute hook.
    # See ``IngestConfirmInput.share_with_public_graph``.
    share_with_public_graph: bool = True


class IngestDecomposeOutput(BaseModel):
    """Phase 1 results — stored on message metadata, returned to frontend."""

    fact_count: int = 0
    source_count: int = 0
    proposed_nodes: list[ProposedNode] = Field(default_factory=list)
    content_summary: str = ""
    key_topics: list[str] = Field(default_factory=list)
    fact_type_counts: dict[str, int] = Field(default_factory=dict)


class IngestBuildInput(GraphAwareMixin):
    """Input for the phased ingest build workflow (Phase 2)."""

    selected_nodes: list[ConfirmedNode]
    conversation_id: str
    message_id: str
    user_id: str | None = None


class IngestPartitionInput(GraphAwareMixin):
    """Input for a single ingest partition (parallel agent)."""

    conversation_id: str
    message_id: str
    partition_id: str
    index_range_start: int
    index_range_end: int
    nav_budget: int
    # Full corpus context (lightweight)
    total_facts: int
    fact_type_counts: dict[str, int] = Field(default_factory=dict)
    all_titles: list[str] = Field(default_factory=list)
    partition_facts: int = 0


class IngestPartitionOutput(BaseModel):
    """Results from a single ingest partition."""

    created_node_ids: list[str] = Field(default_factory=list)
    created_edge_ids: list[str] = Field(default_factory=list)
    nav_used: int = 0
    summary: str = ""


# -- Composite nodes ---------------------------------------------------


class BuildCompositeInput(GraphAwareMixin):
    """Input for building a composite node (synthesis or perspective)."""

    node_type: str  # "synthesis" or "perspective"
    concept: str  # Title / claim
    source_node_ids: list[str] = Field(default_factory=list)
    source_fact_ids: list[str] = Field(default_factory=list)
    query_context: str = ""
    parent_concept: str = ""  # For perspectives: parent concept name
    conversation_id: str = ""
    message_id: str = ""
    scope_id: str = ""
    metadata: dict[str, str] | None = None  # e.g. dialectic_role, dialectic_pair_id


class BuildCompositeOutput(BaseModel):
    """Result of building a composite node."""

    node_id: str | None = None
    merged_into: str | None = None  # If merged with existing composite
    version_number: int = 1
    is_new: bool = True  # False if merged into existing
    draws_from_edge_ids: list[str] = Field(default_factory=list)


class RegenerateCompositeInput(GraphAwareMixin):
    """Input for on-demand regeneration of a composite node."""

    node_id: str
    conversation_id: str = ""
    message_id: str = ""


class RegenerateCompositeOutput(BaseModel):
    """Result of regenerating a composite node."""

    node_id: str
    version_number: int = 1
    source_node_count: int = 0
    is_default: bool = True


# -- Seed deduplication ---------------------------------------------------


# -- Source decomposition (scope-based, Flow B) ---------------------------


class DecomposeSourceInput(GraphAwareMixin):
    """Input for decomposing a single raw source into facts."""

    raw_source_id: str
    concept: str
    query_context: str | None = None
    is_image: bool = False
    force: bool = False
    message_id: str = ""
    conversation_id: str = ""


class DecomposeSourceOutput(BaseModel):
    """Results from decomposing a single source."""

    fact_count: int = 0
    fact_ids: list[str] = Field(default_factory=list)
    author_person: str | None = None
    author_org: str | None = None


class DecomposeSourcesInput(GraphAwareMixin):
    """Input for the decompose_sources workflow (fan-out per source)."""

    raw_source_ids: list[str]
    concept: str
    query_context: str | None = None
    image_source_ids: list[str] = Field(default_factory=list)
    message_id: str = ""
    conversation_id: str = ""


class DecomposeSourcesOutput(BaseModel):
    """Aggregated results from decomposing all sources."""

    total_fact_count: int = 0
    fact_ids: list[str] = Field(default_factory=list)
    extracted_nodes: list[dict[str, Any]] = Field(default_factory=list)
    seed_keys: list[str] = Field(default_factory=list)


class EntityExtractionInput(GraphAwareMixin):
    """Input for entity extraction from facts + seed creation."""

    fact_ids: list[str]
    concept: str
    source_authors: list[dict[str, Any]] = Field(default_factory=list)
    message_id: str = ""
    conversation_id: str = ""


class EntityExtractionOutput(BaseModel):
    """Results from entity extraction.

    Seed storage is deferred to the orchestrator (decompose_sources) which
    collects results from all extraction tasks and writes seeds in a single
    batch to avoid hot-row contention on write_seeds.
    """

    extracted_nodes: list[dict[str, Any]] = Field(default_factory=list)
    seed_keys: list[str] = Field(default_factory=list)  # populated by orchestrator, not extraction task


# -- Seed deduplication ---------------------------------------------------


class SeedDedupBatchInput(GraphAwareMixin):
    """Input for a batch of seed deduplication tasks."""

    seed_keys: list[str]
    scope_id: str = ""  # for observability


class SeedDedupBatchOutput(BaseModel):
    """Results from deduplicating a batch of seeds."""

    merges: dict[str, str] = Field(default_factory=dict)  # original_key → surviving_key
    processed: int = 0
    errors: int = 0


# -- Research summary (replaces proposals) ---------------------------------


class SeedSummary(BaseModel):
    """Summary of a seed for research results."""

    key: str
    name: str
    node_type: str
    fact_count: int = 0
    aliases: list[str] = Field(default_factory=list)
    status: str = "active"
    entity_subtype: str | None = None


class ResearchSummaryOutput(BaseModel):
    """Research phase output — facts + seeds gathered, no nodes built."""

    fact_count: int = 0
    source_count: int = 0
    source_urls: list[dict[str, str]] = Field(default_factory=list)
    seeds: list[SeedSummary] = Field(default_factory=list)
    content_summary: str = ""
    explore_used: int = 0


# -- Auto Build ------------------------------------------------------------


class AutoBuildInput(GraphAwareMixin):
    """Input for auto_build_graph task (no params, reads thresholds from settings)."""

    pass


class AutoBuildOutput(BaseModel):
    """Results from auto build graph task."""

    nodes_promoted: int = 0
    nodes_absorbed: int = 0
    nodes_recalculated: int = 0
    nodes_enrichment_dispatched: int = 0


class ReingestSourceInput(GraphAwareMixin):
    """Input for reingesting a raw source (re-fetch + re-decompose)."""

    raw_source_id: str
    concept: str = "general"
    query_context: str | None = None


class ReingestSourceOutput(BaseModel):
    """Results from reingesting a source."""

    fact_count: int = 0
    fact_ids: list[str] = Field(default_factory=list)
    content_updated: bool = False
    message: str = ""


# -- Synthesis workflows -----------------------------------------------


class SynthesizerInput(GraphAwareMixin):
    """Input for the synthesizer workflow."""

    topic: str = ""
    starting_node_ids: list[str] = Field(default_factory=list)
    exploration_budget: int = 20
    visibility: str = "public"
    creator_id: str | None = None
    model_id: str | None = None


class SynthesizerOutput(BaseModel):
    """Output from the synthesizer workflow."""

    synthesis_node_id: str = ""
    sentences_count: int = 0
    facts_linked: int = 0
    nodes_referenced: int = 0


class SuperSynthesizerInput(GraphAwareMixin):
    """Input for the super-synthesizer workflow."""

    topic: str = ""
    sub_configs: list[SynthesizerInput] = Field(default_factory=list)
    existing_synthesis_ids: list[str] = Field(default_factory=list)
    scope_count: int = 0  # 0 = let the LLM decide (3-7)
    visibility: str = "public"
    creator_id: str | None = None
    distance_threshold: float = 0.7
    model_id: str | None = None


class SuperSynthesizerOutput(BaseModel):
    """Output from the super-synthesizer workflow."""

    supersynthesis_node_id: str = ""
    sub_synthesis_node_ids: list[str] = Field(default_factory=list)
    total_sentences: int = 0
    total_facts_linked: int = 0
