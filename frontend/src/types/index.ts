/**
 * TypeScript types mirroring the backend API schemas.
 *
 * These types are the frontend's contract with the Knowledge Tree backend.
 * Keep them in sync with backend/src/knowledge_tree/api/schemas.py and
 * backend/src/knowledge_tree/shared/types.py.
 */

// ---------------------------------------------------------------------------
// Enums
// ---------------------------------------------------------------------------

export const FactType = {
  claim: "claim",
  account: "account",
  measurement: "measurement",
  formula: "formula",
  quote: "quote",
  procedure: "procedure",
  reference: "reference",
  code: "code",
  perspective: "perspective",
} as const;

export type FactType = (typeof FactType)[keyof typeof FactType];

export const EdgeType = {
  related: "related",
  cross_type: "cross_type",
  draws_from: "draws_from",
} as const;

export type EdgeType = (typeof EdgeType)[keyof typeof EdgeType];

// ---------------------------------------------------------------------------
// Query status
// ---------------------------------------------------------------------------

export const QueryStatus = {
  pending: "pending",
  running: "running",
  completed: "completed",
  failed: "failed",
} as const;

export type QueryStatus = (typeof QueryStatus)[keyof typeof QueryStatus];

// ---------------------------------------------------------------------------
// Request types
// ---------------------------------------------------------------------------

export interface CreateConversationRequest {
  message: string;
  nav_budget?: number; // default 200
  explore_budget?: number; // default 20
  wave_count?: number; // default 2
  title?: string | null;
  mode?: string; // "query" | "ingest", default "query"
}

export interface UpdateConversationRequest {
  title: string;
}

export interface SendMessageRequest {
  message: string;
  nav_budget?: number; // default 20
  explore_budget?: number; // default 2
  wave_count?: number; // default 1
}

// ── Quick action types ──────────────────────────────────────────────────

export interface QuickAddNodeRequest {
  concept: string;
}

export interface QuickAddNodeResponse {
  status: string;
  action: "created" | "refreshed";
  node_id: string;
  concept: string;
}

export interface QuickPerspectiveRequest {
  thesis: string;
  antithesis: string;
  parent_concept?: string | null;
}

export interface QuickPerspectiveValidateResponse {
  valid: boolean;
  feedback: string;
}

export interface QuickPerspectiveResponse {
  status: string;
  thesis_id: string | null;
  antithesis_id: string | null;
  thesis_concept: string;
  antithesis_concept: string;
  validation: QuickPerspectiveValidateResponse;
}

// ---------------------------------------------------------------------------
// Response types
// ---------------------------------------------------------------------------

export interface ConversationMessageResponse {
  id: string;
  turn_number: number;
  role: "user" | "assistant";
  content: string;
  nav_budget: number | null;
  explore_budget: number | null;
  nav_used: number | null;
  explore_used: number | null;
  visited_nodes: string[] | null;
  created_nodes: string[] | null;
  created_edges: string[] | null;
  subgraph: SubgraphResponse | null;
  status: string | null;
  error: string | null;
  workflow_run_id: string | null;
  created_at: string; // ISO 8601 datetime
}

export interface ConversationResponse {
  id: string;
  title: string | null;
  mode: string; // "query" | "ingest"
  messages: ConversationMessageResponse[];
  created_at: string; // ISO 8601 datetime
  updated_at: string; // ISO 8601 datetime
}

export interface ConversationListItem {
  id: string;
  title: string | null;
  mode: string; // "query" | "ingest"
  message_count: number;
  latest_status: string | null;
  created_at: string; // ISO 8601 datetime
  updated_at: string; // ISO 8601 datetime
}

export interface PaginatedConversationsResponse {
  items: ConversationListItem[];
  total: number;
  offset: number;
  limit: number;
}

export interface NodeResponse {
  id: string;
  concept: string;
  node_type: string; // "concept" | "perspective" | "entity" | "event" | "location" | "synthesis"
  entity_subtype: string | null; // "person" | "organization" | "other" (entities only)
  parent_id: string | null;
  parent_concept: string | null;
  attractor: string | null;
  filter_id: string | null;
  max_content_tokens: number;
  created_at: string; // ISO 8601 datetime
  updated_at: string; // ISO 8601 datetime
  update_count: number;
  access_count: number;
  fact_count: number;
  seed_fact_count: number;
  pending_facts: number;
  richness: number;
  definition: string | null;
  definition_generated_at: string | null;
  enrichment_status: string | null;
  metadata: Record<string, unknown> | null;
  embedding?: number[] | null;
}

export interface EdgeResponse {
  id: string;
  source_node_id: string;
  source_node_concept: string | null;
  target_node_id: string;
  target_node_concept: string | null;
  relationship_type: string;
  weight: number;
  justification: string | null;
  weight_source: string | null;
  supporting_fact_ids: string[];
  created_at: string; // ISO 8601 datetime
}

export interface FactSourceInfo {
  source_id: string;
  uri: string;
  title: string | null;
  provider_id: string;
  retrieved_at: string; // ISO 8601 datetime
  context_snippet: string | null;
  attribution: string | null;
  author_person: string | null;
  author_org: string | null;
  raw_content?: string | null;
  content_hash?: string | null;
  is_full_text?: boolean;
  content_type?: string | null;
  provider_metadata?: Record<string, unknown> | null;
}

export interface FactNodeInfo {
  node_id: string;
  concept: string;
  node_type: string;
  relevance_score: number;
  stance: string | null;
  linked_at: string; // ISO 8601 datetime
}

export interface FactResponse {
  id: string;
  content: string;
  fact_type: FactType;
  metadata: Record<string, unknown> | null;
  created_at: string; // ISO 8601 datetime
  sources: FactSourceInfo[];
  embedding?: number[] | null;
}

export interface DimensionResponse {
  id: string;
  node_id: string;
  model_id: string;
  content: string;
  confidence: number;
  suggested_concepts: string[] | null;
  generated_at: string; // ISO 8601 datetime
  batch_index: number;
  fact_count: number;
  is_definitive: boolean;
}

export interface ProhibitedChunkResponse {
  id: string;
  chunk_text: string;
  model_id: string;
  fallback_model_id: string | null;
  error_message: string;
  created_at: string;
}

export interface FetcherAttempt {
  provider_id: string;
  success: boolean;
  error: string | null;
  elapsed_ms: number;
}

export interface FetcherAudit {
  winner: string | null;
  attempts: FetcherAttempt[];
}

export interface SourceResponse {
  id: string;
  uri: string;
  title: string | null;
  provider_id: string;
  retrieved_at: string; // ISO 8601 datetime
  fact_count: number;
  prohibited_chunk_count: number;
  is_super_source: boolean;
  is_full_text: boolean;
  fetch_attempted: boolean;
  fetch_error: string | null;
  fetcher: FetcherAudit | null;
}

export interface SourceLinkedNode {
  node_id: string;
  concept: string;
  node_type: string;
  fact_count: number;
}

export interface SourceDetailResponse {
  id: string;
  uri: string;
  title: string | null;
  provider_id: string;
  retrieved_at: string; // ISO 8601 datetime
  fact_count: number;
  prohibited_chunk_count: number;
  is_full_text: boolean;
  fetch_error: string | null;
  fetcher: FetcherAudit | null;
  content_type: string | null;
  content_preview: string | null;
  facts: FactResponse[];
  linked_nodes: SourceLinkedNode[];
  prohibited_chunks: ProhibitedChunkResponse[];
}

export interface SourceReingestResponse {
  source: SourceDetailResponse;
  new_facts_count: number;
  content_updated: boolean;
  message: string;
}

export interface PaginatedSourcesResponse {
  items: SourceResponse[];
  total: number;
  offset: number;
  limit: number;
}

export interface DomainFailureCount {
  domain: string;
  failure_count: number;
}

export interface ErrorGroupCount {
  error_group: string;
  count: number;
}

export interface DailyFailureCount {
  day: string;
  failure_count: number;
}

export interface SourceInsightsResponse {
  total_count: number;
  failed_count: number;
  pending_super_count: number;
  top_failed_domains: DomainFailureCount[];
  common_errors: ErrorGroupCount[];
  failures_per_day: DailyFailureCount[];
}

export interface SubgraphResponse {
  nodes: NodeResponse[];
  edges: EdgeResponse[];
}

export interface GraphStatsResponse {
  node_count: number;
  edge_count: number;
  fact_count: number;
  source_count: number;
}

export interface NodeVersionResponse {
  id: string;
  version_number: number;
  snapshot: Record<string, unknown> | null;
  source_node_count: number;
  is_default: boolean;
  created_at: string; // ISO 8601 datetime
}

// ---------------------------------------------------------------------------
// Management request types
// ---------------------------------------------------------------------------

export interface NodeUpdateRequest {
  concept?: string;
  attractor?: string | null;
  max_content_tokens?: number;
}

export interface FactUpdateRequest {
  content?: string;
  fact_type?: string;
}

// ---------------------------------------------------------------------------
// Paginated response types
// ---------------------------------------------------------------------------

export interface PaginatedNodesResponse {
  items: NodeResponse[];
  total: number;
  offset: number;
  limit: number;
}

export interface PaginatedFactsResponse {
  items: FactResponse[];
  total: number;
  offset: number;
  limit: number;
}

export interface EdgeDetailResponse {
  id: string;
  source_node_id: string;
  source_node_concept: string | null;
  target_node_id: string;
  target_node_concept: string | null;
  relationship_type: string;
  weight: number;
  justification: string | null;
  supporting_fact_ids: string[];
  supporting_facts: FactResponse[];
  created_at: string; // ISO 8601 datetime
}

export interface PaginatedEdgesResponse {
  items: EdgeResponse[];
  total: number;
  offset: number;
  limit: number;
}

export interface DeleteResponse {
  deleted: boolean;
  id: string;
}

// ---------------------------------------------------------------------------
// Export response types
// ---------------------------------------------------------------------------

export interface ExportMetadata {
  exported_at: string; // ISO 8601 datetime
  export_type: string;
  version: string;
  total_items: number;
  embedding_model?: string | null;
}

export interface NodeFactLinkItem {
  node_id: string;
  fact_id: string;
  relevance_score?: number;
  stance?: string | null;
}

export interface ConversationExportResponse {
  metadata: ExportMetadata;
  conversation: ConversationResponse;
  nodes: NodeResponse[];
  edges: EdgeResponse[];
  facts: FactResponse[];
  node_fact_links: NodeFactLinkItem[];
}

export interface NodesExportResponse {
  metadata: ExportMetadata;
  nodes: NodeResponse[];
  edges: EdgeResponse[];
  facts: FactResponse[];
  node_fact_links: NodeFactLinkItem[];
}

export interface FactsExportResponse {
  metadata: ExportMetadata;
  facts: FactResponse[];
}

// ---------------------------------------------------------------------------
// Import request/response types
// ---------------------------------------------------------------------------

export interface ImportFactsRequest {
  facts: FactResponse[];
  cleanup?: boolean;
  cleanup_min_words?: number;
  embedding_model?: string | null;
}

export interface ImportNodesRequest {
  nodes: NodeResponse[];
  edges?: EdgeResponse[];
  facts?: FactResponse[];
  node_fact_links?: NodeFactLinkItem[];
  cleanup?: boolean;
  cleanup_min_words?: number;
  embedding_model?: string | null;
}

export interface ImportResultItem {
  old_id: string;
  new_id: string;
  is_new: boolean;
}

export interface RejectedFactInfo {
  content: string;
  reason: string;
}

export interface ImportResponse {
  imported_facts: ImportResultItem[];
  imported_nodes: ImportResultItem[];
  imported_edges: number;
  imported_sources: number;
  imported_seeds: number;
  rejected_count: number;
  rejected_facts: RejectedFactInfo[];
  errors: string[];
}

export interface ImportProgress {
  phase: string;
  processed: number;
  total: number;
}

// ---------------------------------------------------------------------------
// Path finding
// ---------------------------------------------------------------------------

export interface PathStepResponse {
  node_id: string;
  node_concept: string;
  node_type: string;
  edge: EdgeResponse | null;
}

export interface PathResponse {
  steps: PathStepResponse[];
  length: number;
}

export interface PathsResponse {
  source_id: string;
  target_id: string;
  paths: PathResponse[];
  total_found: number;
  max_depth: number;
  truncated: boolean;
}

// ---------------------------------------------------------------------------
// Ingest
// ---------------------------------------------------------------------------

export interface IngestSourceResponse {
  id: string;
  conversation_id: string;
  source_type: "file" | "link";
  original_name: string;
  mime_type: string | null;
  file_size: number | null;
  section_count: number | null;
  summary: string | null;
  token_estimate: number;
  status: string;
  error: string | null;
  created_at: string; // ISO 8601 datetime
}

export interface IngestPrepareResponse {
  conversation_id: string;
  sources: IngestSourceResponse[];
  image_count: number;
  title: string;
  suggested_nav_budget: number;
  total_token_estimate: number;
}

// ---------------------------------------------------------------------------
// Bottom-up ingest
// ---------------------------------------------------------------------------

export interface BottomUpProposedPerspective {
  claim: string;
  antithesis: string;
}

export interface ProposedNodeAmbiguity {
  is_disambiguated: boolean;
  ambiguity_type: string | null;
  parent_name: string | null;
  sibling_names: string[];
}

export interface BottomUpProposedNode {
  name: string;
  node_type: string;
  entity_subtype: string | null;
  priority: number; // 0-10
  selected: boolean;
  seed_key: string;
  existing_node_id: string | null;
  fact_count: number;
  aliases: string[];
  perspectives: BottomUpProposedPerspective[];
  ambiguity: ProposedNodeAmbiguity | null;
}

export interface BottomUpSourceUrl {
  url: string;
  title: string;
}

export interface BottomUpPrepareResponse {
  conversation_id: string;
  message_id: string;
  fact_count: number;
  source_count: number;
  fact_previews: string[];
  proposed_nodes: BottomUpProposedNode[];
  content_summary: string;
  explore_used: number;
  source_urls: BottomUpSourceUrl[];
  agent_select_status?: string | null;
}

export interface BottomUpBuildResponse {
  conversation_id: string;
  message_id: string;
  node_count: number;
  status: string;
}

// ---------------------------------------------------------------------------
// Research summary (new simplified output)
// ---------------------------------------------------------------------------

export interface ResearchSeedResponse {
  key: string;
  name: string;
  node_type: string;
  fact_count: number;
  aliases: string[];
  status: string;
  entity_subtype: string | null;
}

export interface ResearchSummaryResponse {
  conversation_id: string;
  message_id: string;
  fact_count: number;
  source_count: number;
  source_urls: BottomUpSourceUrl[];
  seeds: ResearchSeedResponse[];
  content_summary: string;
  explore_used: number;
}

// ---------------------------------------------------------------------------
// Phased document ingest
// ---------------------------------------------------------------------------

// ---------------------------------------------------------------------------
// WebSocket stream events
// ---------------------------------------------------------------------------

export const StreamEventType = {
  node_visited: "node_visited",
  node_created: "node_created",
  node_expanded: "node_expanded",
  node_hidden: "node_hidden",
  edge_created: "edge_created",
  edge_removed: "edge_removed",
  budget_update: "budget_update",
  phase_change: "phase_change",
  synthesis_started: "synthesis_started",
  answer_chunk: "answer_chunk",
  complete: "complete",
  error: "error",
  status_update: "status_update",
  activity_log: "activity_log",
  stream_reset: "stream_reset",
  scope_start: "scope_start",
  scope_end: "scope_end",
  wave_start: "wave_start",
  wave_end: "wave_end",
  scope_phase: "scope_phase",
  pipeline_scope_start: "pipeline_scope_start",
  pipeline_scope_end: "pipeline_scope_end",
  pipeline_phase: "pipeline_phase",
  pipeline_error: "pipeline_error",
  pipeline_phase_outcome: "pipeline_phase_outcome",
  // Event-driven pipeline events
  search_started: "search_started",
  search_summary: "search_summary",
  decompose_started: "decompose_started",
  chunk_completed: "chunk_completed",
  page_completed: "page_completed",
  search_completed: "search_completed",
  node_queued: "node_queued",
  perspective_queued: "perspective_queued",
  pipeline_event: "pipeline_event",
  graph_update: "graph_update",
} as const;

export type StreamEventType =
  (typeof StreamEventType)[keyof typeof StreamEventType];

export interface StreamEvent {
  type: StreamEventType;
  query_id: string;
  data: Record<string, unknown>;
  timestamp?: string; // ISO 8601 datetime
}

export interface ActivityEntry {
  action: string;
  tool: string;
  detail?: Record<string, unknown>;
  timestamp: string;
}

// ---------------------------------------------------------------------------
// Cost estimation
// ---------------------------------------------------------------------------

export interface CostBreakdownCategory {
  label: string;
  model_id: string;
  calls: number;
  input_tokens: number;
  output_tokens: number;
  cost: number;
}

export interface CostEstimate {
  estimated_cost_usd: number;
  breakdown: {
    nav_reads: number;
    explore_creates: number;
    model_calls: number;
    search_api_calls: number;
  };
  models: string[];
  categories: CostBreakdownCategory[];
  unsupported_models: string[];
}

export interface ModelRoles {
  orchestrator: string;
  sub_explorer: string;
  edge_resolution: string;
  decomposition: string;
  dimension: string;
  synthesis: string;
}

// ---------------------------------------------------------------------------
// Model configuration
// ---------------------------------------------------------------------------

export interface ModelConfig {
  model_id: string;
  provider: string;
  display_name: string;
}

// ---------------------------------------------------------------------------
// Wave pipeline progress
// ---------------------------------------------------------------------------

export type ScopePhase =
  | "processing"
  | "decomposition"
  | "scout"
  | "planning"
  | "gathering"
  | "searching"
  | "enriching"
  | "building"
  | "classifying"
  | "creating"
  | "dimensions"
  | "definitions"
  | "edges"
  | "parents"
  | "synthesis"
  | "complete"
  // Event-driven pipeline phases
  | "search_task"
  | "decompose_page"
  | "decompose_chunk"
  | "node_task"
  | "perspective_task";

export interface WaveProgressState {
  wave: number;
  totalWaves: number;
  exploreBudget: number;
  navBudget: number;
  status: "planning" | "running" | "complete";
  scopes: Record<string, ScopeProgressState>;
}

export interface ScopeProgressState {
  scope: string;
  phase: ScopePhase;
}

// ---------------------------------------------------------------------------
// Pipeline progress tracking (persistent DB-backed)
// ---------------------------------------------------------------------------

export interface PipelineEventData {
  id: number;
  event_type:
    | "phase_start"
    | "phase_end"
    | "phase_outcome"
    | "error"
    | "info";
  phase: string | null;
  detail: string | null;
  tool_name: string | null;
  tool_params: Record<string, unknown> | null;
  error: string | null;
  created_at: string;
}

export interface PipelineScopeData {
  id: string;
  scope_id: string;
  scope_name: string;
  wave_number: number | null;
  task_run_id: string | null; // Hatchet task run ID — used to fetch task logs
  status: "running" | "completed" | "failed";
  started_at: string;
  completed_at: string | null;
  error: string | null;
  node_count: number;
  events: PipelineEventData[];
}

export interface TaskLogLine {
  message: string;
  created_at: string; // ISO 8601
  level: string | null; // "DEBUG" | "INFO" | "WARN" | "ERROR" | null
}

// ---------------------------------------------------------------------------
// Timeline
// ---------------------------------------------------------------------------

export type TimelineEventKind =
  | "node_created"
  | "node_visited"
  | "node_expanded"
  | "edge_created";

export interface TimelineEntry {
  index: number;
  kind: TimelineEventKind;
  timestamp: string; // ISO 8601
  node?: NodeResponse;
  edge?: EdgeResponse;
}

export const TimelineSpeed = {
  "0.5x": 0.5,
  "1x": 1,
  "2x": 2,
  "4x": 4,
} as const;

export type TimelineSpeed = (typeof TimelineSpeed)[keyof typeof TimelineSpeed];

// ---------------------------------------------------------------------------
// Pipeline snapshot (historical view from Hatchet)
// ---------------------------------------------------------------------------

export interface PipelineTaskItem {
  task_id: string;
  display_name: string;
  status: string; // "QUEUED" | "RUNNING" | "SUCCEEDED" | "FAILED" | "CANCELLED" | "TIMED_OUT"
  duration_ms: number | null;
  started_at: string | null;
  wave_number?: number | null; // Only set for explore_scope tasks
  node_type?: string | null; // "concept" | "entity" | "event" | "perspective" etc.
  has_children: boolean;
  children: PipelineTaskItem[];
}

export interface TaskChildrenResponse {
  tasks: PipelineTaskItem[];
}

export interface PipelineSnapshotResponse {
  message_id: string;
  workflow_run_id: string | null;
  status: string; // "pending" | "running" | "completed" | "failed"
  tasks: PipelineTaskItem[];
}

export interface ProgressResponse {
  message_id: string;
  workflow_run_id: string | null;
  status: string; // "pending" | "running" | "completed" | "failed"
  content: string; // answer text (empty while running)
  error: string | null;
  subgraph: SubgraphResponse | null;
  nav_budget: number | null;
  explore_budget: number | null;
  nav_used: number | null;
  explore_used: number | null;
  visited_nodes: string[] | null;
  created_nodes: string[] | null;
  created_edges: string[] | null;
  tasks: PipelineTaskItem[];
}

export interface SuperSourceInfo {
  raw_source_id: string;
  uri: string;
  title: string | null;
  estimated_tokens: number;
  content_type: string | null;
}

export interface ResearchReportResponse {
  message_id: string;
  nodes_created: number;
  edges_created: number;
  waves_completed: number;
  explore_budget: number | null;
  explore_used: number;
  nav_budget: number | null;
  nav_used: number;
  scope_summaries: string[];
  super_sources: SuperSourceInfo[] | null;
  total_prompt_tokens: number;
  total_completion_tokens: number;
  total_cost_usd: number;
  created_at: string;
}

// ---------------------------------------------------------------------------
// Usage tracking
// ---------------------------------------------------------------------------

export interface TokenUsageByModel {
  model_id: string;
  prompt_tokens: number;
  completion_tokens: number;
  cost_usd: number;
}

export interface MessageUsageSummary {
  message_id: string;
  total_prompt_tokens: number;
  total_completion_tokens: number;
  total_cost_usd: number;
  created_at: string;
}

export interface ConversationUsageResponse {
  conversation_id: string;
  total_prompt_tokens: number;
  total_completion_tokens: number;
  total_cost_usd: number;
  messages: MessageUsageSummary[];
  by_model: TokenUsageByModel[];
}

export interface UsageSummaryResponse {
  total_prompt_tokens: number;
  total_completion_tokens: number;
  total_cost_usd: number;
  report_count: number;
  by_model: TokenUsageByModel[];
  by_task: TokenUsageByModel[];
}

export interface ConversationUsageSummary {
  conversation_id: string;
  title: string | null;
  total_prompt_tokens: number;
  total_completion_tokens: number;
  total_cost_usd: number;
  report_count: number;
  last_at: string | null;
  report_types: string[];
}

// ---------------------------------------------------------------------------
// Auth
// ---------------------------------------------------------------------------

export interface UserRead {
  id: string;
  email: string;
  is_active: boolean;
  is_superuser: boolean;
  is_verified: boolean;
  display_name: string | null;
  created_at: string;
  has_api_key: boolean;
}

export interface MemberResponse {
  id: string;
  email: string;
  display_name: string | null;
  is_superuser: boolean;
  is_active: boolean;
  created_at: string;
  has_byok: boolean;
}

export interface UpdateRoleRequest {
  is_superuser: boolean;
}

export interface ApiTokenRead {
  id: string;
  name: string;
  created_at: string;
  graph_slugs: string[] | null;
  expires_at: string | null;
  last_used_at: string | null;
}

export interface ApiTokenCreated extends ApiTokenRead {
  token: string;
}

// ── System Settings ───────────────────────────────────────────────────

export interface SystemSettingsResponse {
  disable_self_registration: boolean;
  disable_self_registration_source: "env" | "db" | "default";
}

export interface UpdateSystemSettingsRequest {
  disable_self_registration?: boolean;
}

export interface RegistrationStatusResponse {
  registration_open: boolean;
  waitlist_enabled?: boolean;
  reason?: string;
}

// ── Waitlist ────────────────────────────────────────────────────────────

export interface WaitlistSubmitRequest {
  email: string;
  display_name?: string;
  message?: string;
}

export interface WaitlistSubmitResponse {
  status: string;
}

export interface WaitlistEntryResponse {
  id: string;
  email: string;
  display_name: string | null;
  message: string | null;
  status: string;
  reviewed_at: string | null;
  created_at: string;
}

// ── Invites ─────────────────────────────────────────────────────────────

export interface InviteCreateRequest {
  email: string;
  expires_in_days?: number;
}

export interface InviteResponse {
  id: string;
  email: string;
  code: string;
  expires_at: string;
  redeemed_at: string | null;
  created_at: string;
}

export interface InviteValidateRequest {
  email: string;
  code: string;
}

export interface InviteValidateResponse {
  valid: boolean;
  email: string;
}

export interface InviteInfo {
  id: string;
  email: string;
  code: string;
  expires_at: string;
}

export interface WaitlistReviewRequest {
  status: string;
  expires_in_days?: number;
}

export interface WaitlistReviewResponse {
  entry: WaitlistEntryResponse;
  invite: InviteInfo | null;
}

// ── Seeds ──────────────────────────────────────────────────────────────

export interface SeedResponse {
  key: string;
  seed_uuid: string;
  name: string;
  node_type: string;
  entity_subtype: string | null;
  status: string; // "active" | "promoted" | "merged" | "ambiguous"
  merged_into_key: string | null;
  promoted_node_key: string | null;
  fact_count: number;
  source_fact_count: number;
  phonetic_code: string | null;
  aliases: string[];
  created_at: string;
  updated_at: string;
}

export interface SeedRouteResponse {
  child_key: string;
  child_name: string;
  child_status: string;
  child_fact_count: number;
  label: string;
}

export interface SeedMergeResponse {
  operation: string; // "merge" | "split"
  source_seed_key: string;
  target_seed_key: string;
  reason: string | null;
  fact_count_moved: number;
  created_at: string;
}

export interface SeedFactResponse {
  fact_id: string;
  fact_content: string | null;
  confidence: number;
  extraction_context: string | null;
  extraction_role: "mentioned" | "source_attribution";
}

export interface SeedDetailResponse {
  key: string;
  seed_uuid: string;
  name: string;
  node_type: string;
  entity_subtype: string | null;
  status: string;
  merged_into_key: string | null;
  promoted_node_key: string | null;
  fact_count: number;
  source_fact_count: number;
  phonetic_code: string | null;
  aliases: string[];
  created_at: string;
  updated_at: string;
  promotion_threshold: number;
  routes: SeedRouteResponse[];
  merges: SeedMergeResponse[];
  facts: SeedFactResponse[];
  parent_seed: SeedResponse | null;
}

export interface PaginatedSeedsResponse {
  items: SeedResponse[];
  promotion_threshold: number;
  total: number;
  offset: number;
  limit: number;
}

export interface SeedTreeNode {
  key: string;
  name: string;
  status: string;
  node_type: string;
  fact_count: number;
  promoted_node_key: string | null;
  ambiguity_type: string | null;
  children: SeedTreeNode[];
}

export interface SeedTreeResponse {
  root: SeedTreeNode;
  focus_key: string;
}

// ── Perspective Seeds ─────────────────────────────────────────────────

export interface PerspectiveSeedPairResponse {
  thesis_key: string;
  thesis_claim: string;
  antithesis_key: string | null;
  antithesis_claim: string | null;
  source_concept_name: string | null;
  scope_description: string | null;
  fact_count: number;
  status: string;
  created_at: string;
  updated_at: string;
}

export interface PaginatedPerspectiveSeedsResponse {
  items: PerspectiveSeedPairResponse[];
  total: number;
  offset: number;
  limit: number;
}

// ── Edge Candidates ───────────────────────────────────────────────────

export interface EdgeCandidatePairSummary {
  seed_key_a: string;
  seed_key_b: string;
  seed_name_a: string | null;
  seed_name_b: string | null;
  pending_count: number;
  accepted_count: number;
  rejected_count: number;
  total_count: number;
  latest_evaluated_at: string | null;
}

export interface PaginatedEdgeCandidatePairs {
  items: EdgeCandidatePairSummary[];
  total: number;
  offset: number;
  limit: number;
}

export interface EdgeCandidateFactItem {
  id: string;
  fact_id: string;
  fact_content: string | null;
  status: string;
  discovery_strategy: string | null;
  evaluation_result: Record<string, unknown> | null;
  last_evaluated_at: string | null;
  created_at: string;
}

export interface EdgeCandidatePairDetail {
  seed_key_a: string;
  seed_key_b: string;
  seed_name_a: string | null;
  seed_name_b: string | null;
  facts: EdgeCandidateFactItem[];
  pending_count: number;
  accepted_count: number;
  rejected_count: number;
}

// ---------------------------------------------------------------------------
// Synthesis types
// ---------------------------------------------------------------------------

export interface SentenceFactLink {
  fact_id: string;
  content: string;
  fact_type: string;
  embedding_distance: number;
  source_title: string;
  source_uri: string;
  author: string;
}

export interface SynthesisSentenceResponse {
  position: number;
  text: string;
  fact_count: number;
  node_ids: string[];
}

export interface SynthesisNodeResponse {
  node_id: string;
  concept: string;
  node_type: string;
}

export interface SynthesisDocumentResponse {
  id: string;
  concept: string;
  node_type: string;
  visibility: string;
  definition: string | null;
  model_id: string | null;
  sentences: SynthesisSentenceResponse[];
  referenced_nodes: SynthesisNodeResponse[];
  sub_syntheses: SynthesisNodeResponse[];
  created_at: string | null;
}

export interface SynthesisListItem {
  id: string;
  concept: string;
  node_type: string;
  visibility: string;
  model_id: string | null;
  sentence_count: number;
  sub_synthesis_ids: string[];
  created_at: string | null;
}

export interface PaginatedSynthesesResponse {
  items: SynthesisListItem[];
  total: number;
  offset: number;
  limit: number;
}

export interface SentenceFactResponse {
  fact_id: string;
  content: string;
  fact_type: string;
  embedding_distance: number;
}

export interface SentenceFactsBySourceResponse {
  source_id: string;
  source_uri: string;
  source_title: string;
  facts: SentenceFactResponse[];
}

export interface CreateSynthesisRequest {
  topic: string;
  starting_node_ids?: string[];
  exploration_budget?: number;
  visibility?: string;
  model_id?: string;
}

export interface CreateSuperSynthesisRequest {
  topic: string;
  sub_configs?: CreateSynthesisRequest[];
  existing_synthesis_ids?: string[];
  scope_count?: number;
  visibility?: string;
  distance_threshold?: number;
  model_id?: string;
}

export interface SynthesisModelOption {
  model_id: string;
  display_name: string;
  provider: string;
}

// ── Graphs (multi-graph) ─────────────────────────────────────────────

export interface GraphResponse {
  id: string;
  slug: string;
  name: string;
  description: string | null;
  is_default: boolean;
  graph_type: string;
  byok_enabled: boolean;
  storage_mode: string;
  schema_name: string;
  database_connection_id: string | null;
  database_connection_name: string | null;
  status: string;
  created_by: string | null;
  created_at: string;
  updated_at: string;
  member_count: number;
  node_count: number;
  // Multigraph public-cache toggles. Both default to true for new
  // non-default graphs; the default graph itself ignores them.
  contribute_to_public: boolean;
  use_public_cache: boolean;
}

export interface CreateGraphRequest {
  slug: string;
  name: string;
  description?: string;
  graph_type?: string;
  byok_enabled?: boolean;
  // "default" or omitted = system DB; otherwise a config_key from
  // listDatabaseConnections() (an external DB provisioned in the infra layer).
  database_connection_config_key?: string;
  contribute_to_public?: boolean;
  use_public_cache?: boolean;
}

export interface UpdateGraphRequest {
  name?: string;
  description?: string;
  // Use ``null``/omit to leave the existing value unchanged. The API
  // rejects toggle edits on the default graph with HTTP 400.
  contribute_to_public?: boolean;
  use_public_cache?: boolean;
}

export interface GraphMemberResponse {
  id: string;
  user_id: string;
  email: string;
  display_name: string | null;
  role: string;
  created_at: string;
}

export interface AddGraphMemberRequest {
  user_id: string;
  role?: string;
}

export interface UpdateGraphMemberRoleRequest {
  role: string;
}

export interface DatabaseConnectionResponse {
  // null for the synthetic "default" entry; non-null for real database_connections rows
  id: string | null;
  name: string;
  config_key: string;
  created_at: string | null;
}
