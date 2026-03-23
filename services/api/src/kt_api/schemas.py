"""Pydantic request/response models for the Knowledge Tree API."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, computed_field


class CreateConversationRequest(BaseModel):
    """Request body for creating a new conversation (initial query).

    Modes:
    - ``research`` — Standard wave-based exploration (default).
    - ``query`` — Read-only graph navigation, no new nodes.
    - ``bottom_up`` — Exhaustive node extraction from sources.
    """

    message: str
    nav_budget: int = 200
    explore_budget: int = 20
    wave_count: int = 2
    title: str | None = None
    mode: str = "research"


class UpdateConversationRequest(BaseModel):
    """Request body for updating a conversation (e.g. editing the query title)."""

    title: str


class SendMessageRequest(BaseModel):
    """Request body for sending a follow-up message in a conversation."""

    message: str
    nav_budget: int = 20
    explore_budget: int = 2
    wave_count: int = 1


class ConversationMessageResponse(BaseModel):
    """Response body for a single conversation message."""

    id: str
    turn_number: int
    role: str
    content: str
    nav_budget: int | None = None
    explore_budget: int | None = None
    nav_used: int | None = None
    explore_used: int | None = None
    visited_nodes: list[str] | None = None
    created_nodes: list[str] | None = None
    created_edges: list[str] | None = None
    subgraph: SubgraphResponse | None = None
    status: str | None = None
    error: str | None = None
    workflow_run_id: str | None = None
    created_at: datetime


class ConversationResponse(BaseModel):
    """Response body for a conversation with all messages."""

    id: str
    title: str | None = None
    mode: str = "research"
    messages: list[ConversationMessageResponse] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class ConversationListItem(BaseModel):
    """Summary item for conversation listing."""

    id: str
    title: str | None = None
    mode: str = "research"
    message_count: int = 0
    created_at: datetime
    updated_at: datetime


class PaginatedConversationsResponse(BaseModel):
    """Paginated list of conversations."""

    items: list[ConversationListItem]
    total: int
    offset: int
    limit: int


class NodeResponse(BaseModel):
    """Response body for a single node."""

    id: str
    concept: str
    node_type: str = "concept"
    key: str = ""
    entity_subtype: str | None = None
    parent_id: str | None = None
    parent_concept: str | None = None
    parent_key: str | None = None
    attractor: str | None = None
    filter_id: str | None = None
    max_content_tokens: int = 500
    created_at: datetime
    updated_at: datetime
    update_count: int = 0
    access_count: int = 0
    edge_count: int = 0
    child_count: int = 0
    fact_count: int = 0
    seed_fact_count: int = 0
    pending_facts: int = 0
    richness: float = 0.0
    convergence_score: float = 0.0
    definition: str | None = None
    definition_source: str | None = None
    definition_generated_at: str | None = None
    enrichment_status: str | None = None
    metadata: dict[str, object] | None = None
    embedding: list[float] | None = None


class EdgeResponse(BaseModel):
    """Response body for a single edge."""

    id: str
    source_node_id: str
    source_node_concept: str | None = None
    target_node_id: str
    target_node_concept: str | None = None
    relationship_type: str
    weight: float
    justification: str | None = None
    weight_source: str | None = None
    supporting_fact_ids: list[str] = Field(default_factory=list)
    created_at: datetime


class FactSourceInfo(BaseModel):
    """Provenance info linking a fact to a raw source."""

    source_id: str
    uri: str
    title: str | None = None
    provider_id: str
    retrieved_at: datetime
    context_snippet: str | None = None
    attribution: str | None = None
    author_person: str | None = None
    author_org: str | None = None
    raw_content: str | None = None
    content_hash: str | None = None
    is_full_text: bool = False
    content_type: str | None = None
    provider_metadata: dict[str, object] | None = None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def published_date(self) -> str | None:
        """Extract publication date from provider_metadata.

        Checks html_metadata.date, then Serper date, then Brave age.
        """
        if not isinstance(self.provider_metadata, dict):
            return None
        html_meta = self.provider_metadata.get("html_metadata")
        if isinstance(html_meta, dict) and html_meta.get("date"):
            return str(html_meta["date"])
        if self.provider_metadata.get("date"):
            return str(self.provider_metadata["date"])
        if self.provider_metadata.get("age"):
            return str(self.provider_metadata["age"])
        return None


class FactNodeInfo(BaseModel):
    """Lightweight info about a node linked to a fact."""

    node_id: str
    concept: str
    node_type: str = "concept"
    relevance_score: float = 1.0
    stance: str | None = None
    linked_at: datetime


class FactResponse(BaseModel):
    """Response body for a single fact."""

    id: str
    content: str
    fact_type: str
    stance: str | None = None
    metadata: dict[str, object] | None = None
    created_at: datetime
    sources: list[FactSourceInfo] = Field(default_factory=list)
    embedding: list[float] | None = None


class DimensionResponse(BaseModel):
    """Response body for a single dimension (model perspective)."""

    id: str
    node_id: str
    model_id: str
    content: str
    confidence: float
    suggested_concepts: list[str] | None = None
    generated_at: datetime
    batch_index: int = 0
    fact_count: int = 0
    is_definitive: bool = False


class ConvergenceResponse(BaseModel):
    """Response body for convergence analysis results."""

    convergence_score: float
    converged_claims: list[str] = Field(default_factory=list)
    divergent_claims: list[dict[str, object]] = Field(default_factory=list)
    recommended_content: str | None = None


class ProhibitedChunkResponse(BaseModel):
    """A text chunk rejected by LLM safety filters during extraction."""

    id: str
    chunk_text: str
    model_id: str
    fallback_model_id: str | None = None
    error_message: str
    created_at: datetime


class SourceResponse(BaseModel):
    """Response body for a single raw source."""

    id: str
    uri: str
    title: str | None = None
    provider_id: str
    retrieved_at: datetime
    fact_count: int = 0
    prohibited_chunk_count: int = 0
    is_super_source: bool = False
    is_full_text: bool = False
    fetch_attempted: bool = False


class SourceLinkedNode(BaseModel):
    """A node linked to a source via facts."""

    node_id: str
    concept: str
    node_type: str
    fact_count: int


class SourceDetailResponse(BaseModel):
    """Full detail for a single raw source, including its facts and linked nodes."""

    id: str
    uri: str
    title: str | None = None
    provider_id: str
    retrieved_at: datetime
    fact_count: int = 0
    prohibited_chunk_count: int = 0
    is_full_text: bool = False
    content_type: str | None = None
    content_preview: str | None = None
    facts: list[FactResponse] = Field(default_factory=list)
    linked_nodes: list[SourceLinkedNode] = Field(default_factory=list)
    prohibited_chunks: list[ProhibitedChunkResponse] = Field(default_factory=list)


class SourceReingestResponse(BaseModel):
    """Response after reingesting a source."""

    source: SourceDetailResponse
    new_facts_count: int
    content_updated: bool
    message: str


class PaginatedSourcesResponse(BaseModel):
    """Paginated list of raw sources."""

    items: list[SourceResponse]
    total: int
    offset: int
    limit: int


class SubgraphResponse(BaseModel):
    """Response body for a subgraph (collection of nodes and edges)."""

    nodes: list[NodeResponse] = Field(default_factory=list)
    edges: list[EdgeResponse] = Field(default_factory=list)


class GraphStatsResponse(BaseModel):
    """Response body for graph-wide statistics."""

    node_count: int
    edge_count: int
    fact_count: int
    source_count: int


class NodeVersionResponse(BaseModel):
    """Response body for a node version snapshot."""

    id: str
    version_number: int
    snapshot: dict[str, object] | None = None
    source_node_count: int = 0
    is_default: bool = False
    created_at: datetime


class NodeUpdateRequest(BaseModel):
    """Request body for updating a node."""

    concept: str | None = None
    attractor: str | None = None
    max_content_tokens: int | None = None


class FactUpdateRequest(BaseModel):
    """Request body for updating a fact."""

    content: str | None = None
    fact_type: str | None = None


class PaginatedNodesResponse(BaseModel):
    """Paginated list of nodes."""

    items: list[NodeResponse]
    total: int
    offset: int
    limit: int


class PaginatedFactsResponse(BaseModel):
    """Paginated list of facts."""

    items: list[FactResponse]
    total: int
    offset: int
    limit: int


class EdgeDetailResponse(BaseModel):
    """Detailed response for a single edge with resolved node names and full facts."""

    id: str
    source_node_id: str
    source_node_concept: str | None = None
    target_node_id: str
    target_node_concept: str | None = None
    relationship_type: str
    weight: float
    justification: str | None = None
    weight_source: str | None = None
    supporting_fact_ids: list[str] = Field(default_factory=list)
    supporting_facts: list[FactResponse] = Field(default_factory=list)
    created_at: datetime


class PaginatedEdgesResponse(BaseModel):
    """Paginated list of edges."""

    items: list[EdgeResponse]
    total: int
    offset: int
    limit: int


class DeleteResponse(BaseModel):
    """Response for delete operations."""

    deleted: bool
    id: str


class ExportMetadata(BaseModel):
    """Metadata included in every export payload."""

    exported_at: datetime
    export_type: str
    version: str = "1.1"
    total_items: int
    embedding_model: str | None = None


class ConversationExportResponse(BaseModel):
    """Full export of a conversation with its nodes, edges, and facts."""

    metadata: ExportMetadata
    conversation: ConversationResponse
    nodes: list[NodeResponse] = Field(default_factory=list)
    edges: list[EdgeResponse] = Field(default_factory=list)
    facts: list[FactResponse] = Field(default_factory=list)
    node_fact_links: list[NodeFactLinkItem] = Field(default_factory=list)


class NodeFactLinkItem(BaseModel):
    """A link between a node and a fact (for import/export)."""

    node_id: str
    fact_id: str
    relevance_score: float = 1.0
    stance: str | None = None


class NodesExportResponse(BaseModel):
    """Export of all nodes in the graph."""

    metadata: ExportMetadata
    nodes: list[NodeResponse] = Field(default_factory=list)
    edges: list[EdgeResponse] = Field(default_factory=list)
    facts: list[FactResponse] = Field(default_factory=list)
    node_fact_links: list[NodeFactLinkItem] = Field(default_factory=list)


class FactsExportResponse(BaseModel):
    """Export of all facts with their sources."""

    metadata: ExportMetadata
    facts: list[FactResponse] = Field(default_factory=list)


# ── Import schemas ───────────────────────────────────────────────────────


class ImportFactsRequest(BaseModel):
    """Request body for importing facts."""

    facts: list[FactResponse]
    cleanup: bool = False
    cleanup_min_words: int = 12
    embedding_model: str | None = None


class ImportNodesRequest(BaseModel):
    """Request body for importing nodes with their facts, edges, and links."""

    nodes: list[NodeResponse]
    edges: list[EdgeResponse] = Field(default_factory=list)
    facts: list[FactResponse] = Field(default_factory=list)
    node_fact_links: list[NodeFactLinkItem] = Field(default_factory=list)
    cleanup: bool = False
    cleanup_min_words: int = 12
    embedding_model: str | None = None


class PathStepResponse(BaseModel):
    """A single step in a graph path."""

    node_id: str
    node_concept: str
    node_type: str
    edge: EdgeResponse | None = None


class PathResponse(BaseModel):
    """A single path between two nodes."""

    steps: list[PathStepResponse]
    length: int


class PathsResponse(BaseModel):
    """Response for shortest-paths query between two nodes."""

    source_id: str
    target_id: str
    paths: list[PathResponse]
    total_found: int
    max_depth: int
    truncated: bool


class RejectedFactInfo(BaseModel):
    """A fact rejected during import cleanup with the reason."""

    content: str
    reason: str


class ImportResultItem(BaseModel):
    """Result of importing a single item (fact or node)."""

    old_id: str
    new_id: str
    is_new: bool


class ResynthesizeResponse(BaseModel):
    """Response body for re-synthesis request."""

    message_id: str
    status: str


class PipelineTaskItem(BaseModel):
    """A single task from a Hatchet workflow run (historical snapshot)."""

    task_id: str
    display_name: str
    status: str  # "QUEUED" | "RUNNING" | "SUCCEEDED" | "FAILED" | "CANCELLED" | "TIMED_OUT"
    duration_ms: int | None = None
    started_at: str | None = None  # ISO 8601
    wave_number: int | None = None  # Only set for explore_scope tasks
    node_type: str | None = None  # "concept" | "entity" | "event" | "perspective" etc.
    children: list["PipelineTaskItem"] = Field(default_factory=list)


PipelineTaskItem.model_rebuild()


class PipelineSnapshotResponse(BaseModel):
    """Historical pipeline snapshot for a completed message.

    Returned by GET /conversations/{id}/messages/{msgId}/pipeline.
    Contains the Hatchet task tree mapped to pipeline scope/task data.
    """

    message_id: str
    workflow_run_id: str | None = None
    status: str
    tasks: list[PipelineTaskItem] = Field(default_factory=list)


class ProgressResponse(BaseModel):
    """Combined progress response for polling-based progress updates.

    Merges message state (status, content, subgraph, budgets) with
    Hatchet pipeline task state into a single response.
    """

    message_id: str
    status: str  # "pending" | "running" | "completed" | "failed"
    content: str  # answer text (empty while running)
    error: str | None = None
    subgraph: SubgraphResponse | None = None
    nav_budget: int | None = None
    explore_budget: int | None = None
    nav_used: int | None = None
    explore_used: int | None = None
    visited_nodes: list[str] | None = None
    created_nodes: list[str] | None = None
    created_edges: list[str] | None = None
    tasks: list[PipelineTaskItem] = Field(default_factory=list)


class TaskLogLineResponse(BaseModel):
    """A single log line from a Hatchet task run."""

    message: str
    created_at: datetime
    level: str | None = None  # "DEBUG" | "INFO" | "WARN" | "ERROR"


class ResearchReportResponse(BaseModel):
    """Persisted outcome summary for an orchestrator run.

    Returned by GET /conversations/{id}/messages/{msgId}/report.
    """

    message_id: str
    nodes_created: int
    edges_created: int
    waves_completed: int
    explore_budget: int | None = None
    explore_used: int
    nav_budget: int | None = None
    nav_used: int
    scope_summaries: list[str] = Field(default_factory=list)
    super_sources: list[dict[str, Any]] | None = None
    total_prompt_tokens: int = 0
    total_completion_tokens: int = 0
    total_cost_usd: float = 0.0
    created_at: datetime


class TokenUsageByModel(BaseModel):
    """Per-model token usage breakdown."""

    model_id: str
    prompt_tokens: int
    completion_tokens: int
    cost_usd: float


class MessageUsageSummary(BaseModel):
    """Usage summary for a single message/turn."""

    message_id: str
    total_prompt_tokens: int = 0
    total_completion_tokens: int = 0
    total_cost_usd: float = 0.0
    created_at: datetime


class ConversationUsageResponse(BaseModel):
    """Token usage for a conversation, broken down by message."""

    conversation_id: str
    total_prompt_tokens: int = 0
    total_completion_tokens: int = 0
    total_cost_usd: float = 0.0
    messages: list[MessageUsageSummary] = Field(default_factory=list)
    by_model: list[TokenUsageByModel] = Field(default_factory=list)


class UsageSummaryResponse(BaseModel):
    """Global token usage summary."""

    total_prompt_tokens: int = 0
    total_completion_tokens: int = 0
    total_cost_usd: float = 0.0
    report_count: int = 0
    by_model: list[TokenUsageByModel] = Field(default_factory=list)
    by_task: list[TokenUsageByModel] = Field(default_factory=list)


class ConversationUsageSummary(BaseModel):
    """Usage totals for a single conversation."""

    conversation_id: str
    title: str | None = None
    total_prompt_tokens: int = 0
    total_completion_tokens: int = 0
    total_cost_usd: float = 0.0
    report_count: int = 0
    last_at: datetime | None = None
    report_types: list[str] = Field(default_factory=lambda: ["research"])


class IngestSourceResponse(BaseModel):
    """Response body for an ingest source."""

    id: str
    conversation_id: str
    source_type: str  # "file" | "link"
    original_name: str
    mime_type: str | None = None
    file_size: int | None = None
    section_count: int | None = None
    summary: str | None = None
    status: str
    error: str | None = None
    created_at: datetime


class ChunkInfoResponse(BaseModel):
    """Info about a single chunk with LLM recommendation."""

    source_id: str
    source_name: str
    chunk_index: int
    char_count: int
    preview: str
    is_image: bool = False
    recommended: bool = True
    reason: str = ""


class IngestPrepareResponse(BaseModel):
    """Response from the ingest prepare step — chunk counts for user confirmation."""

    conversation_id: str
    sources: list[IngestSourceResponse] = Field(default_factory=list)
    chunks: list[ChunkInfoResponse] = Field(default_factory=list)
    total_chunks: int
    image_count: int
    recommended_chunks: int
    estimated_decompose_calls: int  # total_chunks + image_count
    title: str
    suggested_nav_budget: int = 50  # 1 node per ~1K tokens
    total_token_estimate: int = 0  # Approximate total tokens in content


class IngestConfirmRequest(BaseModel):
    """Request body for confirming an ingest after reviewing the prepare response."""

    nav_budget: int = 50  # max nodes to create
    selected_chunks: list[int] | None = None  # chunk indices to process; None = all


# ── Quick action schemas ────────────────────────────────────────────────


class QuickAddNodeRequest(BaseModel):
    """Request body for the quick-add-node action."""

    concept: str


class QuickAddNodeResponse(BaseModel):
    """Response for quick-add-node: reports whether a node was created or refreshed."""

    status: str  # "created" | "refreshed" | "started"
    action: str  # "created" | "refreshed"
    node_id: str
    concept: str


class QuickPerspectiveRequest(BaseModel):
    """Request body for the quick-add-perspective action."""

    thesis: str
    antithesis: str
    parent_concept: str | None = None


class QuickPerspectiveValidateResponse(BaseModel):
    """Response for antithesis LLM validation."""

    valid: bool
    feedback: str


class QuickPerspectiveResponse(BaseModel):
    """Response for quick-add-perspective."""

    status: str  # "created" | "error"
    thesis_id: str | None = None
    antithesis_id: str | None = None
    thesis_concept: str
    antithesis_concept: str
    validation: QuickPerspectiveValidateResponse


# ── Bottom-up ingest schemas ───────────────────────────────────────────


class BottomUpPrepareRequest(BaseModel):
    """Request for bottom-up ingest Phase 1 — gather facts from web."""

    query: str
    explore_budget: int = 20
    title: str | None = None


class BottomUpProposedPerspective(BaseModel):
    """A perspective proposed for a parent concept."""

    claim: str
    antithesis: str


class ProposedNodeAmbiguityResponse(BaseModel):
    """Ambiguity metadata for a proposed seed."""

    is_disambiguated: bool = False
    ambiguity_type: str | None = None
    parent_name: str | None = None
    sibling_names: list[str] = Field(default_factory=list)


class BottomUpProposedNodeResponse(BaseModel):
    """A proposed node from Phase 1 results."""

    name: str
    node_type: str = "concept"
    entity_subtype: str | None = None
    priority: int = 5  # 0-10
    selected: bool = True
    seed_key: str = ""
    existing_node_id: str | None = None
    fact_count: int = 0
    aliases: list[str] = Field(default_factory=list)
    perspectives: list[BottomUpProposedPerspective] = Field(default_factory=list)
    ambiguity: ProposedNodeAmbiguityResponse | None = None


class BottomUpSourceUrl(BaseModel):
    """A source URL from fact gathering."""

    url: str
    title: str = ""


class BottomUpPrepareResponse(BaseModel):
    """Phase 1 result — fact gathering complete, proposed nodes returned."""

    conversation_id: str
    message_id: str
    fact_count: int
    source_count: int
    fact_previews: list[str] = Field(default_factory=list)
    proposed_nodes: list[BottomUpProposedNodeResponse] = Field(default_factory=list)
    content_summary: str = ""
    explore_used: int = 0
    source_urls: list[BottomUpSourceUrl] = Field(default_factory=list)
    agent_select_status: str | None = None


class ResearchSeedResponse(BaseModel):
    """A seed in the research summary."""

    key: str
    name: str
    node_type: str
    fact_count: int = 0
    aliases: list[str] = Field(default_factory=list)
    status: str = "active"
    entity_subtype: str | None = None


class ResearchSummaryResponse(BaseModel):
    """Research result — facts + seeds gathered, no nodes built."""

    conversation_id: str
    message_id: str
    fact_count: int = 0
    source_count: int = 0
    source_urls: list[BottomUpSourceUrl] = Field(default_factory=list)
    seeds: list[ResearchSeedResponse] = Field(default_factory=list)
    content_summary: str = ""
    explore_used: int = 0


class BottomUpConfirmedNode(BaseModel):
    """A node confirmed by the user for Phase 2 building."""

    name: str
    node_type: str = "concept"
    entity_subtype: str | None = None
    seed_key: str = ""
    existing_node_id: str | None = None
    perspectives: list[BottomUpProposedPerspective] = Field(default_factory=list)


class AgentSelectRequest(BaseModel):
    """Request for agent-assisted node selection."""

    max_select: int = 20
    instructions: str = ""


class AgentSelectResponse(BaseModel):
    """Acknowledgement that agent selection workflow was dispatched."""

    conversation_id: str
    message_id: str
    status: str = "running"


class AgentSelectStatusResponse(BaseModel):
    """Status of agent-assisted node selection."""

    status: str  # "running" | "completed" | "not_started"


# ── Phased document ingest schemas ────────────────────────────────────


class IngestDecomposeRequest(BaseModel):
    """Request for phased ingest Phase 1 — decompose + extract + prioritize."""

    selected_chunks: list[int] | None = None


class IngestDecomposeResponse(BaseModel):
    """Phase 1 acknowledgement."""

    conversation_id: str
    message_id: str
    status: str = "running"


class IngestProposalsResponse(BaseModel):
    """Phase 1 result — proposed nodes from document decomposition."""

    conversation_id: str
    message_id: str
    fact_count: int
    proposed_nodes: list[BottomUpProposedNodeResponse] = Field(default_factory=list)
    content_summary: str = ""
    key_topics: list[str] = Field(default_factory=list)
    fact_type_counts: dict[str, int] = Field(default_factory=dict)
    agent_select_status: str | None = None


class IngestBuildRequest(BaseModel):
    """Request for phased ingest Phase 2 — build confirmed nodes."""

    selected_nodes: list[BottomUpConfirmedNode]


class IngestBuildResponse(BaseModel):
    """Phase 2 acknowledgement."""

    conversation_id: str
    message_id: str
    node_count: int = 0
    status: str = "running"


# ── Seed schemas ─────────────────────────────────────────────────────


class SeedResponse(BaseModel):
    """Response body for a single seed."""

    key: str
    seed_uuid: str
    name: str
    node_type: str
    entity_subtype: str | None = None
    status: str
    merged_into_key: str | None = None
    promoted_node_key: str | None = None
    fact_count: int = 0
    source_fact_count: int = 0
    phonetic_code: str | None = None
    aliases: list[str] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class SeedRouteResponse(BaseModel):
    """A disambiguation route from a parent seed to a child seed."""

    child_key: str
    child_name: str
    child_status: str
    child_fact_count: int = 0
    label: str


class SeedMergeResponse(BaseModel):
    """Audit trail entry for a seed merge or split."""

    operation: str
    source_seed_key: str
    target_seed_key: str
    reason: str | None = None
    fact_count_moved: int = 0
    created_at: datetime


class SeedFactResponse(BaseModel):
    """A fact linked to a seed."""

    fact_id: str
    fact_content: str | None = None
    confidence: float = 1.0
    extraction_context: str | None = None
    extraction_role: str = "mentioned"


class SeedDivergenceResponse(BaseModel):
    """Fact embedding divergence metrics for a seed."""

    seed_key: str
    fact_count: int
    vectors_found: int
    mean_pairwise_distance: float | None = None
    max_pairwise_distance: float | None = None
    min_pairwise_distance: float | None = None
    std_pairwise_distance: float | None = None
    cluster_estimate: int = 1


class SeedDetailResponse(BaseModel):
    """Full detail for a single seed."""

    key: str
    seed_uuid: str
    name: str
    node_type: str
    entity_subtype: str | None = None
    status: str
    merged_into_key: str | None = None
    promoted_node_key: str | None = None
    fact_count: int = 0
    source_fact_count: int = 0
    phonetic_code: str | None = None
    aliases: list[str] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime
    promotion_threshold: int = 10
    routes: list[SeedRouteResponse] = Field(default_factory=list)
    merges: list[SeedMergeResponse] = Field(default_factory=list)
    facts: list[SeedFactResponse] = Field(default_factory=list)
    parent_seed: SeedResponse | None = None


class PaginatedSeedsResponse(BaseModel):
    """Paginated list of seeds."""

    items: list[SeedResponse]
    promotion_threshold: int = 10
    total: int
    offset: int
    limit: int


class SeedTreeNode(BaseModel):
    """A node in the seed disambiguation tree."""

    key: str
    name: str
    status: str
    node_type: str
    fact_count: int = 0
    promoted_node_key: str | None = None
    ambiguity_type: str | None = None
    children: list["SeedTreeNode"] = Field(default_factory=list)


class SeedTreeResponse(BaseModel):
    """Full seed disambiguation tree rooted at the ancestor."""

    root: SeedTreeNode
    focus_key: str


class PerspectiveSeedPairResponse(BaseModel):
    """A thesis/antithesis perspective seed pair."""

    thesis_key: str
    thesis_claim: str
    antithesis_key: str | None = None
    antithesis_claim: str | None = None
    source_concept_name: str | None = None
    scope_description: str | None = None
    fact_count: int = 0
    status: str = "active"
    created_at: datetime
    updated_at: datetime


class PaginatedPerspectiveSeedsResponse(BaseModel):
    """Paginated list of perspective seed pairs."""

    items: list[PerspectiveSeedPairResponse]
    total: int
    offset: int
    limit: int


class SynthesizeResponse(BaseModel):
    """Response from triggering perspective synthesis."""

    thesis_seed_key: str
    antithesis_seed_key: str | None = None
    status: str = "synthesizing"


class PromoteSeedResponse(BaseModel):
    """Response from promoting a seed to a node."""

    seed_key: str
    status: str  # "started" | "already_promoted"
    workflow_run_id: str | None = None
    node_id: str | None = None


class EdgeCandidatePairSummary(BaseModel):
    """Summary of an edge candidate pair."""

    seed_key_a: str
    seed_key_b: str
    seed_name_a: str | None = None
    seed_name_b: str | None = None
    pending_count: int = 0
    accepted_count: int = 0
    rejected_count: int = 0
    total_count: int = 0
    latest_evaluated_at: datetime | None = None


class PaginatedEdgeCandidatePairs(BaseModel):
    """Paginated list of edge candidate pairs."""

    items: list[EdgeCandidatePairSummary]
    total: int
    offset: int
    limit: int


class EdgeCandidateFactItem(BaseModel):
    """A single fact row within an edge candidate pair."""

    id: str
    fact_id: str
    fact_content: str | None = None
    status: str
    discovery_strategy: str | None = None
    evaluation_result: dict | None = None
    last_evaluated_at: datetime | None = None
    created_at: datetime


class EdgeCandidatePairDetail(BaseModel):
    """Full detail for a specific edge candidate pair."""

    seed_key_a: str
    seed_key_b: str
    seed_name_a: str | None = None
    seed_name_b: str | None = None
    facts: list[EdgeCandidateFactItem] = Field(default_factory=list)
    pending_count: int = 0
    accepted_count: int = 0
    rejected_count: int = 0


class ImportResponse(BaseModel):
    """Response body for import operations."""

    imported_facts: list[ImportResultItem] = Field(default_factory=list)
    imported_nodes: list[ImportResultItem] = Field(default_factory=list)
    imported_edges: int = 0
    imported_sources: int = 0
    imported_seeds: int = 0
    rejected_count: int = 0
    rejected_facts: list[RejectedFactInfo] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
