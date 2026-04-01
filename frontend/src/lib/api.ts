import type {
  CreateConversationRequest,
  SendMessageRequest,
  ConversationResponse,
  ConversationMessageResponse,
  PaginatedConversationsResponse,
  ProgressResponse,
  NodeResponse,
  FactResponse,
  FactNodeInfo,
  DimensionResponse,
  ConvergenceResponse,
  SourceDetailResponse,
  PaginatedSourcesResponse,
  SourceReingestResponse,
  SubgraphResponse,
  GraphStatsResponse,
  NodeVersionResponse,
  ModelConfig,
  ModelRoles,
  EdgeResponse,
  EdgeDetailResponse,
  PaginatedNodesResponse,
  PaginatedFactsResponse,
  PaginatedEdgesResponse,
  DeleteResponse,
  NodeUpdateRequest,
  FactUpdateRequest,
  UpdateConversationRequest,
  NodesExportResponse,
  FactsExportResponse,
  ConversationExportResponse,
  ImportFactsRequest,
  ImportNodesRequest,
  ImportResponse,
  ImportProgress,
  PathsResponse,
  IngestSourceResponse,
  IngestPrepareResponse,
  IngestProposalsResponse,
  BottomUpPrepareResponse,
  BottomUpProposedNode,
  BottomUpBuildResponse,
  AgentSelectResponse,
  AgentSelectStatusResponse,
  UserRead,
  ApiTokenRead,
  ApiTokenCreated,
  MemberResponse,
  UpdateRoleRequest,
  ResearchReportResponse,
  UsageSummaryResponse,
  ConversationUsageResponse,
  ConversationUsageSummary,
  TokenUsageByModel,
  TaskLogLine,
  QuickAddNodeRequest,
  QuickAddNodeResponse,
  QuickPerspectiveRequest,
  QuickPerspectiveResponse,
  QuickPerspectiveValidateResponse,
  PaginatedSeedsResponse,
  PaginatedPerspectiveSeedsResponse,
  SeedDetailResponse,
  SeedDivergenceResponse,
  SeedTreeResponse,
  PaginatedEdgeCandidatePairs,
  EdgeCandidatePairDetail,
  ResearchSummaryResponse,
  SystemSettingsResponse,
  UpdateSystemSettingsRequest,
  RegistrationStatusResponse,
  WaitlistSubmitRequest,
  WaitlistSubmitResponse,
  WaitlistEntryResponse,
  WaitlistReviewResponse,
  InviteCreateRequest,
  InviteResponse,
  InviteValidateResponse,
  CreateSynthesisRequest,
  CreateSuperSynthesisRequest,
  SynthesisDocumentResponse,
  PaginatedSynthesesResponse,
  SentenceFactLink,
  SynthesisNodeResponse,
  PipelineSnapshotResponse,
} from "@/types";

const BASE_URL =
  process.env.NEXT_PUBLIC_API_URL?.replace(/\/+$/, "") ||
  "http://localhost:8000";

const API_PREFIX = "/api/v1";

// ---------------------------------------------------------------------------
// Internal helpers
// ---------------------------------------------------------------------------

function getAuthHeader(): Record<string, string> {
  if (typeof window === "undefined") return {};
  const token = localStorage.getItem("access_token");
  return token ? { Authorization: `Bearer ${token}` } : {};
}

async function request<T>(
  path: string,
  options: RequestInit = {},
): Promise<T> {
  const url = `${BASE_URL}${API_PREFIX}${path}`;

  const res = await fetch(url, {
    headers: {
      "Content-Type": "application/json",
      ...getAuthHeader(),
      ...options.headers,
    },
    ...options,
  });

  if (res.status === 401) {
    if (typeof window !== "undefined") {
      localStorage.removeItem("access_token");
      window.location.href = "/login";
    }
    throw new Error("Unauthorized");
  }

  if (!res.ok) {
    const body = await res.text().catch(() => "");
    throw new Error(
      `API error ${res.status} ${res.statusText}: ${body}`.trim(),
    );
  }

  return res.json() as Promise<T>;
}

function buildQuery(params: Record<string, string | undefined>): string {
  const entries = Object.entries(params).filter(
    (entry): entry is [string, string] => entry[1] !== undefined,
  );
  if (entries.length === 0) return "";
  return "?" + new URLSearchParams(entries).toString();
}

// ---------------------------------------------------------------------------
// Typed API client
// ---------------------------------------------------------------------------

export const api = {
  // -------------------------------------------------------------------------
  // Conversations
  // -------------------------------------------------------------------------
  conversations: {
    create(data: CreateConversationRequest): Promise<ConversationResponse> {
      return request<ConversationResponse>("/conversations", {
        method: "POST",
        body: JSON.stringify(data),
      });
    },

    get(id: string): Promise<ConversationResponse> {
      return request<ConversationResponse>(
        `/conversations/${encodeURIComponent(id)}`,
      );
    },

    list(params?: {
      offset?: number;
      limit?: number;
      mode?: string;
    }): Promise<PaginatedConversationsResponse> {
      const qs = buildQuery({
        offset:
          params?.offset !== undefined ? String(params.offset) : undefined,
        limit: params?.limit !== undefined ? String(params.limit) : undefined,
        mode: params?.mode,
      });
      return request<PaginatedConversationsResponse>(`/conversations${qs}`);
    },

    sendMessage(
      conversationId: string,
      data: SendMessageRequest,
    ): Promise<ConversationMessageResponse> {
      return request<ConversationMessageResponse>(
        `/conversations/${encodeURIComponent(conversationId)}/messages`,
        {
          method: "POST",
          body: JSON.stringify(data),
        },
      );
    },

    resynthesize(
      conversationId: string,
      messageId: string,
    ): Promise<{ message_id: string; status: string }> {
      return request<{ message_id: string; status: string }>(
        `/conversations/${encodeURIComponent(conversationId)}/messages/${encodeURIComponent(messageId)}/resynthesize`,
        { method: "POST" },
      );
    },

    stopTurn(
      conversationId: string,
      messageId: string,
    ): Promise<{ message_id: string; status: string }> {
      return request<{ message_id: string; status: string }>(
        `/conversations/${encodeURIComponent(conversationId)}/messages/${encodeURIComponent(messageId)}/stop`,
        { method: "POST" },
      );
    },

    getProgress(
      conversationId: string,
      messageId: string,
    ): Promise<ProgressResponse> {
      return request<ProgressResponse>(
        `/conversations/${encodeURIComponent(conversationId)}/messages/${encodeURIComponent(messageId)}/progress`,
      );
    },

    getMessageReport(
      conversationId: string,
      messageId: string,
    ): Promise<ResearchReportResponse> {
      return request<ResearchReportResponse>(
        `/conversations/${encodeURIComponent(conversationId)}/messages/${encodeURIComponent(messageId)}/report`,
      );
    },

    updateTitle(
      conversationId: string,
      data: UpdateConversationRequest,
    ): Promise<ConversationResponse> {
      return request<ConversationResponse>(
        `/conversations/${encodeURIComponent(conversationId)}`,
        {
          method: "PATCH",
          body: JSON.stringify(data),
        },
      );
    },

    delete(id: string): Promise<DeleteResponse> {
      return request<DeleteResponse>(
        `/conversations/${encodeURIComponent(id)}`,
        { method: "DELETE" },
      );
    },
  },

  // -------------------------------------------------------------------------
  // Tasks (Hatchet task-level utilities)
  // -------------------------------------------------------------------------
  tasks: {
    getLogs(taskRunId: string): Promise<TaskLogLine[]> {
      return request<TaskLogLine[]>(`/tasks/${encodeURIComponent(taskRunId)}/logs`);
    },
  },

  // -------------------------------------------------------------------------
  // Nodes
  // -------------------------------------------------------------------------
  nodes: {
    get(id: string): Promise<NodeResponse> {
      return request<NodeResponse>(`/nodes/${encodeURIComponent(id)}`);
    },

    list(params?: {
      offset?: number;
      limit?: number;
      search?: string;
      node_type?: string;
      sort?: string;
    }): Promise<PaginatedNodesResponse> {
      const qs = buildQuery({
        offset:
          params?.offset !== undefined ? String(params.offset) : undefined,
        limit: params?.limit !== undefined ? String(params.limit) : undefined,
        search: params?.search,
        node_type: params?.node_type,
        sort: params?.sort,
      });
      return request<PaginatedNodesResponse>(`/nodes${qs}`);
    },

    update(id: string, data: NodeUpdateRequest): Promise<NodeResponse> {
      return request<NodeResponse>(`/nodes/${encodeURIComponent(id)}`, {
        method: "PATCH",
        body: JSON.stringify(data),
      });
    },

    delete(id: string): Promise<DeleteResponse> {
      return request<DeleteResponse>(`/nodes/${encodeURIComponent(id)}`, {
        method: "DELETE",
      });
    },

    search(query: string, limit?: number): Promise<NodeResponse[]> {
      const qs = buildQuery({
        query,
        limit: limit !== undefined ? String(limit) : undefined,
      });
      return request<NodeResponse[]>(`/nodes/search${qs}`);
    },

    getDimensions(id: string): Promise<DimensionResponse[]> {
      return request<DimensionResponse[]>(
        `/nodes/${encodeURIComponent(id)}/dimensions`,
      );
    },

    getFacts(id: string): Promise<FactResponse[]> {
      return request<FactResponse[]>(
        `/nodes/${encodeURIComponent(id)}/facts`,
      );
    },

    getEdges(id: string, direction?: string): Promise<EdgeResponse[]> {
      const qs = buildQuery({ direction });
      return request<EdgeResponse[]>(
        `/nodes/${encodeURIComponent(id)}/edges${qs}`,
      );
    },

    getHistory(id: string): Promise<NodeVersionResponse[]> {
      return request<NodeVersionResponse[]>(
        `/nodes/${encodeURIComponent(id)}/history`,
      );
    },

    getConvergence(id: string): Promise<ConvergenceResponse> {
      return request<ConvergenceResponse>(
        `/nodes/${encodeURIComponent(id)}/convergence`,
      );
    },

    rebuildNode(
      id: string,
      mode: "full" | "incremental" = "full",
      scope: "all" | "dimensions" | "edges" = "all",
    ): Promise<{ status: string; node_id: string }> {
      return request<{ status: string; node_id: string }>(
        `/nodes/${encodeURIComponent(id)}/rebuild`,
        {
          method: "POST",
          body: JSON.stringify({ mode, scope }),
        },
      );
    },

    regenerateComposite(
      id: string,
    ): Promise<{ status: string; node_id: string }> {
      return request<{ status: string; node_id: string }>(
        `/nodes/${encodeURIComponent(id)}/regenerate`,
        { method: "POST" },
      );
    },

    getSourceNodes(id: string): Promise<NodeResponse[]> {
      return request<NodeResponse[]>(
        `/nodes/${encodeURIComponent(id)}/source-nodes`,
      );
    },

    quickAdd(data: QuickAddNodeRequest): Promise<QuickAddNodeResponse> {
      return request<QuickAddNodeResponse>("/nodes/quick-add", {
        method: "POST",
        body: JSON.stringify(data),
      });
    },

    getPerspectives(id: string): Promise<NodeResponse[]> {
      return request<NodeResponse[]>(
        `/nodes/${encodeURIComponent(id)}/perspectives`,
      );
    },

    quickPerspectiveValidate(
      data: QuickPerspectiveRequest,
    ): Promise<QuickPerspectiveValidateResponse> {
      return request<QuickPerspectiveValidateResponse>(
        "/nodes/quick-perspective/validate",
        {
          method: "POST",
          body: JSON.stringify(data),
        },
      );
    },

    quickPerspective(
      data: QuickPerspectiveRequest,
    ): Promise<QuickPerspectiveResponse> {
      return request<QuickPerspectiveResponse>("/nodes/quick-perspective", {
        method: "POST",
        body: JSON.stringify(data),
      });
    },
  },

  // -------------------------------------------------------------------------
  // Graph
  // -------------------------------------------------------------------------
  graph: {
    getSubgraph(nodeIds: string[], depth?: number): Promise<SubgraphResponse> {
      const qs = buildQuery({
        node_ids: nodeIds.join(","),
        depth: depth !== undefined ? String(depth) : undefined,
      });
      return request<SubgraphResponse>(`/graph/subgraph${qs}`);
    },

    getStats(): Promise<GraphStatsResponse> {
      return request<GraphStatsResponse>("/graph/stats");
    },

    getPaths(
      source: string,
      target: string,
      maxDepth?: number,
      limit?: number,
    ): Promise<PathsResponse> {
      const qs = buildQuery({
        source,
        target,
        max_depth: maxDepth !== undefined ? String(maxDepth) : undefined,
        limit: limit !== undefined ? String(limit) : undefined,
      });
      return request<PathsResponse>(`/graph/paths${qs}`);
    },
  },

  // -------------------------------------------------------------------------
  // Facts
  // -------------------------------------------------------------------------
  facts: {
    get(id: string): Promise<FactResponse> {
      return request<FactResponse>(`/facts/${encodeURIComponent(id)}`);
    },

    list(params?: {
      offset?: number;
      limit?: number;
      search?: string;
      fact_type?: string;
    }): Promise<PaginatedFactsResponse> {
      const qs = buildQuery({
        offset:
          params?.offset !== undefined ? String(params.offset) : undefined,
        limit: params?.limit !== undefined ? String(params.limit) : undefined,
        search: params?.search,
        fact_type: params?.fact_type,
      });
      return request<PaginatedFactsResponse>(`/facts${qs}`);
    },

    update(id: string, data: FactUpdateRequest): Promise<FactResponse> {
      return request<FactResponse>(`/facts/${encodeURIComponent(id)}`, {
        method: "PATCH",
        body: JSON.stringify(data),
      });
    },

    delete(id: string): Promise<DeleteResponse> {
      return request<DeleteResponse>(`/facts/${encodeURIComponent(id)}`, {
        method: "DELETE",
      });
    },

    search(factType?: string): Promise<FactResponse[]> {
      const qs = buildQuery({ fact_type: factType });
      return request<FactResponse[]>(`/facts/search${qs}`);
    },

    getNodes(id: string): Promise<FactNodeInfo[]> {
      return request<FactNodeInfo[]>(
        `/facts/${encodeURIComponent(id)}/nodes`,
      );
    },
  },

  // -------------------------------------------------------------------------
  // Edges
  // -------------------------------------------------------------------------
  edges: {
    get(id: string): Promise<EdgeDetailResponse> {
      return request<EdgeDetailResponse>(`/edges/${encodeURIComponent(id)}`);
    },

    list(params?: {
      offset?: number;
      limit?: number;
      relationship_type?: string;
      node_id?: string;
      search?: string;
    }): Promise<PaginatedEdgesResponse> {
      const qs = buildQuery({
        offset:
          params?.offset !== undefined ? String(params.offset) : undefined,
        limit: params?.limit !== undefined ? String(params.limit) : undefined,
        relationship_type: params?.relationship_type,
        node_id: params?.node_id,
        search: params?.search,
      });
      return request<PaginatedEdgesResponse>(`/edges${qs}`);
    },

    delete(id: string): Promise<DeleteResponse> {
      return request<DeleteResponse>(`/edges/${encodeURIComponent(id)}`, {
        method: "DELETE",
      });
    },
  },

  // -------------------------------------------------------------------------
  // Sources
  // -------------------------------------------------------------------------
  sources: {
    get(id: string): Promise<SourceDetailResponse> {
      return request<SourceDetailResponse>(`/sources/${encodeURIComponent(id)}`);
    },

    list(params?: {
      offset?: number;
      limit?: number;
      search?: string;
      provider_id?: string;
      sort_by?: string;
      has_prohibited?: boolean;
      is_super_source?: boolean;
      fetch_status?: string;
    }): Promise<PaginatedSourcesResponse> {
      const qs = buildQuery({
        offset:
          params?.offset !== undefined ? String(params.offset) : undefined,
        limit: params?.limit !== undefined ? String(params.limit) : undefined,
        search: params?.search,
        provider_id: params?.provider_id,
        sort_by: params?.sort_by,
        has_prohibited:
          params?.has_prohibited !== undefined
            ? String(params.has_prohibited)
            : undefined,
        is_super_source:
          params?.is_super_source !== undefined
            ? String(params.is_super_source)
            : undefined,
        fetch_status: params?.fetch_status,
      });
      return request<PaginatedSourcesResponse>(`/sources${qs}`);
    },

    skipDomain(id: string): Promise<{ status: string; domain: string }> {
      return request<{ status: string; domain: string }>(
        `/sources/${encodeURIComponent(id)}/skip-domain`,
        { method: "POST" },
      );
    },

    reingest(id: string): Promise<SourceReingestResponse> {
      return request<SourceReingestResponse>(
        `/sources/${encodeURIComponent(id)}/reingest`,
        { method: "POST" },
      );
    },
  },

  // -------------------------------------------------------------------------
  // Seeds
  // -------------------------------------------------------------------------
  seeds: {
    get(key: string): Promise<SeedDetailResponse> {
      return request<SeedDetailResponse>(`/seeds/${encodeURIComponent(key)}`);
    },

    getDivergence(key: string): Promise<SeedDivergenceResponse> {
      return request<SeedDivergenceResponse>(
        `/seeds/divergence/${encodeURIComponent(key)}`,
      );
    },

    list(params?: {
      offset?: number;
      limit?: number;
      search?: string;
      status?: string;
      node_type?: string;
    }): Promise<PaginatedSeedsResponse> {
      const qs = buildQuery({
        offset:
          params?.offset !== undefined ? String(params.offset) : undefined,
        limit: params?.limit !== undefined ? String(params.limit) : undefined,
        search: params?.search,
        status: params?.status,
        node_type: params?.node_type,
      });
      return request<PaginatedSeedsResponse>(`/seeds${qs}`);
    },

    getTree(key: string): Promise<SeedTreeResponse> {
      return request<SeedTreeResponse>(
        `/seeds/tree/${encodeURIComponent(key)}`,
      );
    },

    listPerspectives(params?: {
      offset?: number;
      limit?: number;
      search?: string;
      status?: string;
      source_node_id?: string;
    }): Promise<PaginatedPerspectiveSeedsResponse> {
      const qs = buildQuery({
        offset:
          params?.offset !== undefined ? String(params.offset) : undefined,
        limit: params?.limit !== undefined ? String(params.limit) : undefined,
        search: params?.search,
        status: params?.status,
        source_node_id: params?.source_node_id,
      });
      return request<PaginatedPerspectiveSeedsResponse>(
        `/seeds/perspectives${qs}`,
      );
    },

    synthesizePerspective(
      seedKey: string,
    ): Promise<{ thesis_seed_key: string; antithesis_seed_key: string | null; status: string }> {
      return request(
        `/seeds/perspectives/${encodeURIComponent(seedKey)}/synthesize`,
        { method: "POST" },
      );
    },

    dismissPerspective(
      seedKey: string,
    ): Promise<{ status: string }> {
      return request(
        `/seeds/perspectives/${encodeURIComponent(seedKey)}`,
        { method: "DELETE" },
      );
    },

    promote(
      seedKey: string,
    ): Promise<{
      seed_key: string;
      status: string;
      workflow_run_id: string | null;
      node_id: string | null;
    }> {
      return request(
        `/seeds/promote/${encodeURIComponent(seedKey)}`,
        { method: "POST" },
      );
    },
  },

  // -------------------------------------------------------------------------
  // Edge Candidates
  // -------------------------------------------------------------------------
  edgeCandidates: {
    list(params?: {
      offset?: number;
      limit?: number;
      status?: string;
      search?: string;
      min_facts?: number;
    }): Promise<PaginatedEdgeCandidatePairs> {
      const qs = buildQuery({
        offset:
          params?.offset !== undefined ? String(params.offset) : undefined,
        limit: params?.limit !== undefined ? String(params.limit) : undefined,
        status: params?.status,
        search: params?.search,
        min_facts:
          params?.min_facts !== undefined
            ? String(params.min_facts)
            : undefined,
      });
      return request<PaginatedEdgeCandidatePairs>(`/edge-candidates${qs}`);
    },

    get(
      seedKeyA: string,
      seedKeyB: string,
    ): Promise<EdgeCandidatePairDetail> {
      return request<EdgeCandidatePairDetail>(
        `/edge-candidates/${encodeURIComponent(seedKeyA)}/${encodeURIComponent(seedKeyB)}`,
      );
    },

    bySeed(
      seedKey: string,
      params?: { offset?: number; limit?: number },
    ): Promise<PaginatedEdgeCandidatePairs> {
      const qs = buildQuery({
        offset:
          params?.offset !== undefined ? String(params.offset) : undefined,
        limit: params?.limit !== undefined ? String(params.limit) : undefined,
      });
      return request<PaginatedEdgeCandidatePairs>(
        `/edge-candidates/by-seed/${encodeURIComponent(seedKey)}${qs}`,
      );
    },
  },

  // -------------------------------------------------------------------------
  // Config
  // -------------------------------------------------------------------------
  config: {
    getModels(): Promise<ModelConfig[]> {
      return request<ModelConfig[]>("/config/models");
    },

    getFilters(): Promise<Record<string, unknown>> {
      return request<Record<string, unknown>>("/config/filters");
    },

    getModelRoles(): Promise<ModelRoles> {
      return request<ModelRoles>("/config/model-roles");
    },
  },

  // -------------------------------------------------------------------------
  // Export
  // -------------------------------------------------------------------------
  export: {
    nodes(): Promise<NodesExportResponse> {
      return request<NodesExportResponse>("/export/nodes");
    },

    facts(): Promise<FactsExportResponse> {
      return request<FactsExportResponse>("/export/facts");
    },

    conversation(id: string): Promise<ConversationExportResponse> {
      return request<ConversationExportResponse>(
        `/export/conversations/${encodeURIComponent(id)}`,
      );
    },
  },

  // -------------------------------------------------------------------------
  // Import
  // -------------------------------------------------------------------------
  import: {
    facts(data: ImportFactsRequest): Promise<ImportResponse> {
      return request<ImportResponse>("/import/facts", {
        method: "POST",
        body: JSON.stringify(data),
      });
    },

    nodes(data: ImportNodesRequest): Promise<ImportResponse> {
      return request<ImportResponse>("/import/nodes", {
        method: "POST",
        body: JSON.stringify(data),
      });
    },

    factsStream(
      data: ImportFactsRequest,
      onProgress: (progress: ImportProgress) => void,
    ): Promise<ImportResponse> {
      return streamImport("/import/facts/stream", data, onProgress);
    },

    nodesStream(
      data: ImportNodesRequest,
      onProgress: (progress: ImportProgress) => void,
    ): Promise<ImportResponse> {
      return streamImport("/import/nodes/stream", data, onProgress);
    },
  },
  // -------------------------------------------------------------------------
  // Research
  // -------------------------------------------------------------------------
  research: {
    prepare(formData: FormData): Promise<IngestPrepareResponse> {
      const url = `${BASE_URL}${API_PREFIX}/research/prepare`;
      return fetch(url, {
        method: "POST",
        headers: { ...getAuthHeader() },
        body: formData,
        // Do NOT set Content-Type — browser sets multipart boundary automatically
      }).then(async (res) => {
        if (!res.ok) {
          const body = await res.text().catch(() => "");
          throw new Error(
            `API error ${res.status} ${res.statusText}: ${body}`.trim(),
          );
        }
        return res.json() as Promise<IngestPrepareResponse>;
      });
    },

    confirm(
      conversationId: string,
      navBudget: number,
      selectedChunks?: number[] | null,
    ): Promise<ConversationResponse> {
      return request<ConversationResponse>(
        `/research/${encodeURIComponent(conversationId)}/confirm`,
        {
          method: "POST",
          body: JSON.stringify({
            nav_budget: navBudget,
            selected_chunks: selectedChunks ?? null,
          }),
        },
      );
    },

    getSources(conversationId: string): Promise<IngestSourceResponse[]> {
      return request<IngestSourceResponse[]>(
        `/research/${encodeURIComponent(conversationId)}/sources`,
      );
    },

    getSourceDownloadUrl(conversationId: string, sourceId: string): string {
      return `${BASE_URL}${API_PREFIX}/research/${encodeURIComponent(conversationId)}/sources/${encodeURIComponent(sourceId)}/download`;
    },

    decompose(
      conversationId: string,
      selectedChunks?: number[] | null,
    ): Promise<{ conversation_id: string; message_id: string; status: string }> {
      return request<{ conversation_id: string; message_id: string; status: string }>(
        `/research/${encodeURIComponent(conversationId)}/decompose`,
        {
          method: "POST",
          body: JSON.stringify({
            selected_chunks: selectedChunks ?? null,
          }),
        },
      );
    },

    proposals(conversationId: string): Promise<IngestProposalsResponse> {
      return request<IngestProposalsResponse>(
        `/research/${encodeURIComponent(conversationId)}/proposals`,
      );
    },

    build(
      conversationId: string,
      selectedNodes: BottomUpProposedNode[],
    ): Promise<BottomUpBuildResponse> {
      return request<BottomUpBuildResponse>(
        `/research/${encodeURIComponent(conversationId)}/build`,
        {
          method: "POST",
          body: JSON.stringify({ selected_nodes: selectedNodes }),
        },
      );
    },

    bottomUpPrepare(data: {
      query: string;
      explore_budget: number;
      title?: string;
    }): Promise<ConversationResponse> {
      return request<ConversationResponse>("/research/bottom-up/prepare", {
        method: "POST",
        body: JSON.stringify(data),
      });
    },

    bottomUpProposals(
      conversationId: string,
    ): Promise<BottomUpPrepareResponse> {
      return request<BottomUpPrepareResponse>(
        `/research/${encodeURIComponent(conversationId)}/bottom-up/proposals`,
      );
    },

    agentSelect(
      conversationId: string,
      maxSelect: number,
      instructions?: string,
    ): Promise<AgentSelectResponse> {
      return request<AgentSelectResponse>(
        `/research/${encodeURIComponent(conversationId)}/agent-select`,
        {
          method: "POST",
          body: JSON.stringify({
            max_select: maxSelect,
            instructions: instructions ?? "",
          }),
        },
      );
    },

    agentSelectStatus(
      conversationId: string,
    ): Promise<AgentSelectStatusResponse> {
      return request<AgentSelectStatusResponse>(
        `/research/${encodeURIComponent(conversationId)}/agent-select/status`,
      );
    },

    getSummary(
      conversationId: string,
    ): Promise<ResearchSummaryResponse> {
      return request<ResearchSummaryResponse>(
        `/research/${encodeURIComponent(conversationId)}/summary`,
      );
    },
  },

  // -------------------------------------------------------------------------
  // Auto Build
  // -------------------------------------------------------------------------
  graphBuilder: {
    autoBuild(): Promise<{ status: string; workflow_run_id: string }> {
      return request<{ status: string; workflow_run_id: string }>(
        "/graph-builder/auto-build",
        { method: "POST" },
      );
    },
  },

  // -------------------------------------------------------------------------
  // Usage
  // -------------------------------------------------------------------------
  usage: {
    getSummary(since?: string, until?: string): Promise<UsageSummaryResponse> {
      const params = new URLSearchParams();
      if (since) params.set("since", since);
      if (until) params.set("until", until);
      const qs = params.toString();
      return request<UsageSummaryResponse>(`/usage/summary${qs ? `?${qs}` : ""}`);
    },

    getConversationUsage(
      conversationId: string,
    ): Promise<ConversationUsageResponse> {
      return request<ConversationUsageResponse>(
        `/usage/conversations/${encodeURIComponent(conversationId)}`,
      );
    },

    getByModel(since?: string, until?: string): Promise<TokenUsageByModel[]> {
      const params = new URLSearchParams();
      if (since) params.set("since", since);
      if (until) params.set("until", until);
      const qs = params.toString();
      return request<TokenUsageByModel[]>(`/usage/by-model${qs ? `?${qs}` : ""}`);
    },

    getByConversation(since?: string, until?: string): Promise<ConversationUsageSummary[]> {
      const params = new URLSearchParams();
      if (since) params.set("since", since);
      if (until) params.set("until", until);
      const qs = params.toString();
      return request<ConversationUsageSummary[]>(`/usage/by-conversation${qs ? `?${qs}` : ""}`);
    },
  },

  // -------------------------------------------------------------------------
  // Auth
  // -------------------------------------------------------------------------
  auth: {
    register(data: {
      email: string;
      password: string;
      display_name?: string;
    }): Promise<UserRead> {
      return request<UserRead>("/auth/register", {
        method: "POST",
        body: JSON.stringify(data),
      });
    },

    login(email: string, password: string): Promise<{ access_token: string; token_type: string }> {
      const form = new URLSearchParams({ username: email, password });
      return request<{ access_token: string; token_type: string }>("/auth/login", {
        method: "POST",
        headers: { "Content-Type": "application/x-www-form-urlencoded" },
        body: form.toString(),
      });
    },

    me(): Promise<UserRead> {
      return request<UserRead>("/auth/me");
    },

    googleAuthorize(redirectUrl: string): Promise<{ authorization_url: string }> {
      return request<{ authorization_url: string }>(
        `/auth/google/authorize?redirect_url=${encodeURIComponent(redirectUrl)}`,
      );
    },

    listTokens(): Promise<ApiTokenRead[]> {
      return request<ApiTokenRead[]>("/auth/tokens");
    },

    createToken(name: string, expiresAt?: string): Promise<ApiTokenCreated> {
      return request<ApiTokenCreated>("/auth/tokens", {
        method: "POST",
        body: JSON.stringify({ name, expires_at: expiresAt ?? null }),
      });
    },

    revokeToken(id: string): Promise<void> {
      return request<void>(`/auth/tokens/${id}`, { method: "DELETE" });
    },

    registrationStatus(): Promise<RegistrationStatusResponse> {
      return request<RegistrationStatusResponse>("/auth/registration-status");
    },

    authFeatures(): Promise<{ google_oauth_enabled: boolean }> {
      return request<{ google_oauth_enabled: boolean }>("/auth/features");
    },
  },

  // -------------------------------------------------------------------------
  // System Settings (admin only)
  // -------------------------------------------------------------------------
  systemSettings: {
    get(): Promise<SystemSettingsResponse> {
      return request<SystemSettingsResponse>("/system-settings");
    },

    update(data: UpdateSystemSettingsRequest): Promise<SystemSettingsResponse> {
      return request<SystemSettingsResponse>("/system-settings", {
        method: "PATCH",
        body: JSON.stringify(data),
      });
    },
  },

  // -------------------------------------------------------------------------
  // Members (admin only)
  // -------------------------------------------------------------------------
  members: {
    list(): Promise<MemberResponse[]> {
      return request<MemberResponse[]>("/members");
    },

    updateRole(
      userId: string,
      data: UpdateRoleRequest,
    ): Promise<MemberResponse> {
      return request<MemberResponse>(
        `/members/${encodeURIComponent(userId)}/role`,
        {
          method: "PATCH",
          body: JSON.stringify(data),
        },
      );
    },
  },

  // -------------------------------------------------------------------------
  // Waitlist
  // -------------------------------------------------------------------------
  waitlist: {
    submit(data: WaitlistSubmitRequest): Promise<WaitlistSubmitResponse> {
      return request<WaitlistSubmitResponse>("/waitlist", {
        method: "POST",
        body: JSON.stringify(data),
      });
    },

    list(status?: string): Promise<WaitlistEntryResponse[]> {
      const qs = status ? `?status=${encodeURIComponent(status)}` : "";
      return request<WaitlistEntryResponse[]>(`/waitlist${qs}`);
    },

    review(
      entryId: string,
      data: { status: string; expires_in_days?: number },
    ): Promise<WaitlistReviewResponse> {
      return request<WaitlistReviewResponse>(
        `/waitlist/${encodeURIComponent(entryId)}`,
        {
          method: "PATCH",
          body: JSON.stringify(data),
        },
      );
    },
  },

  // -------------------------------------------------------------------------
  // Invites
  // -------------------------------------------------------------------------
  invites: {
    create(data: InviteCreateRequest): Promise<InviteResponse> {
      return request<InviteResponse>("/invites", {
        method: "POST",
        body: JSON.stringify(data),
      });
    },

    list(): Promise<InviteResponse[]> {
      return request<InviteResponse[]>("/invites");
    },

    validate(
      email: string,
      code: string,
    ): Promise<InviteValidateResponse> {
      return request<InviteValidateResponse>("/invites/validate", {
        method: "POST",
        body: JSON.stringify({ email, code }),
      });
    },

    revoke(inviteId: string): Promise<void> {
      return request<void>(
        `/invites/${encodeURIComponent(inviteId)}`,
        { method: "DELETE" },
      );
    },
  },

  // -------------------------------------------------------------------------
  // BYOK (Bring Your Own Key)
  // -------------------------------------------------------------------------
  byok: {
    status(): Promise<{ has_key: boolean }> {
      return request<{ has_key: boolean }>("/auth/me/api-key/status");
    },

    set(apiKey: string): Promise<{ has_key: boolean }> {
      return request<{ has_key: boolean }>("/auth/me/api-key", {
        method: "PUT",
        body: JSON.stringify({ api_key: apiKey }),
      });
    },

    remove(): Promise<{ has_key: boolean }> {
      return request<{ has_key: boolean }>("/auth/me/api-key", {
        method: "DELETE",
      });
    },
  },
} as const;

// ---------------------------------------------------------------------------
// Streaming import helper
// ---------------------------------------------------------------------------

async function streamImport(
  path: string,
  data: unknown,
  onProgress: (progress: ImportProgress) => void,
): Promise<ImportResponse> {
  const url = `${BASE_URL}${API_PREFIX}${path}`;
  const res = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...getAuthHeader() },
    body: JSON.stringify(data),
  });

  if (!res.ok) {
    const body = await res.text().catch(() => "");
    throw new Error(
      `API error ${res.status} ${res.statusText}: ${body}`.trim(),
    );
  }

  const reader = res.body?.getReader();
  if (!reader) {
    throw new Error("Response body is not readable");
  }

  const decoder = new TextDecoder();
  let buffer = "";
  let result: ImportResponse | null = null;

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;

    buffer += decoder.decode(value, { stream: true });

    // Parse SSE events from the buffer
    const lines = buffer.split("\n");
    buffer = lines.pop() ?? "";

    for (const line of lines) {
      if (!line.startsWith("data: ")) continue;
      const jsonStr = line.slice(6);
      try {
        const event = JSON.parse(jsonStr);
        if (event.type === "progress") {
          onProgress({
            phase: event.phase,
            processed: event.processed,
            total: event.total,
          });
        } else if (event.type === "complete" && event.result) {
          result = event.result as ImportResponse;
        } else if (event.type === "error") {
          throw new Error(event.message ?? "Import failed");
        }
      } catch (e) {
        if (e instanceof SyntaxError) continue;
        throw e;
      }
    }
  }

  if (!result) {
    throw new Error("Import stream ended without a result");
  }
  return result;
}

// ---------------------------------------------------------------------------
// Syntheses
// ---------------------------------------------------------------------------

export async function createSynthesis(data: CreateSynthesisRequest) {
  return request<{ status: string; workflow_run_id: string; topic: string }>(
    "/syntheses",
    { method: "POST", body: JSON.stringify(data) }
  );
}

export async function createSuperSynthesis(data: CreateSuperSynthesisRequest) {
  return request<{ status: string; workflow_run_id: string; topic: string }>(
    "/super-syntheses",
    { method: "POST", body: JSON.stringify(data) }
  );
}

export async function getWorkflowProgress(workflowRunId: string) {
  return request<PipelineSnapshotResponse>(
    `/workflows/${encodeURIComponent(workflowRunId)}/progress`
  );
}

export async function listSyntheses(offset = 0, limit = 20, visibility?: string) {
  const params = new URLSearchParams({ offset: String(offset), limit: String(limit) });
  if (visibility) params.set("visibility", visibility);
  return request<PaginatedSynthesesResponse>(`/syntheses?${params}`);
}

export async function getSynthesis(id: string) {
  return request<SynthesisDocumentResponse>(`/syntheses/${id}`);
}

export async function getSentenceFacts(synthesisId: string, position: number) {
  return request<SentenceFactLink[]>(
    `/syntheses/${synthesisId}/sentences/${position}/facts`
  );
}

export async function getSynthesisNodes(synthesisId: string) {
  return request<SynthesisNodeResponse[]>(`/syntheses/${synthesisId}/nodes`);
}

export async function deleteSynthesis(id: string) {
  return request<{ deleted: boolean; id: string }>(
    `/syntheses/${id}`,
    { method: "DELETE" }
  );
}

export async function updateSynthesisVisibility(id: string, visibility: string) {
  return request<{ id: string; visibility: string }>(
    `/syntheses/${id}`,
    { method: "PATCH", body: JSON.stringify({ visibility }) }
  );
}
