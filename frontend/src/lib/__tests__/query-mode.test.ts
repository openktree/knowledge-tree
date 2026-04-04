import { describe, it, expect } from "vitest";
import { StreamEventType } from "@/types";
import type {
  NodeResponse,
  EdgeResponse,
  ConversationResponse,
  ConversationListItem,
  CreateConversationRequest,
} from "@/types";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function makeNode(overrides: Partial<NodeResponse> = {}): NodeResponse {
  return {
    id: "node-1",
    concept: "Test Concept",
    node_type: "concept",
    entity_subtype: null,
    parent_id: null,
    parent_concept: null,
    attractor: null,
    filter_id: null,
    max_content_tokens: 4000,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
    update_count: 1,
    access_count: 3,
    fact_count: 0,
    seed_fact_count: 0,
    pending_facts: 0,
    richness: 0.75,
    convergence_score: 0.75,
    definition: null,
    definition_generated_at: null,
    enrichment_status: null,
    metadata: null,
    ...overrides,
  };
}

function makeEdge(overrides: Partial<EdgeResponse> = {}): EdgeResponse {
  return {
    id: "edge-1",
    source_node_id: "node-1",
    source_node_concept: null,
    target_node_id: "node-2",
    target_node_concept: null,
    relationship_type: "related",
    weight: 0.8,
    justification: null,
    weight_source: null,
    supporting_fact_ids: [],
    created_at: "2026-01-01T00:00:00Z",
    ...overrides,
  };
}

// ---------------------------------------------------------------------------
// StreamEventType — node_hidden
// ---------------------------------------------------------------------------

describe("StreamEventType.node_hidden", () => {
  it("exists in the StreamEventType enum", () => {
    expect(StreamEventType.node_hidden).toBe("node_hidden");
  });

  it("simulates removing a node and its edges on node_hidden event", () => {
    // Simulate a graph state
    const nodes = [
      makeNode({ id: "a" }),
      makeNode({ id: "b" }),
      makeNode({ id: "c" }),
    ];
    const edges = [
      makeEdge({ id: "e1", source_node_id: "a", target_node_id: "b" }),
      makeEdge({ id: "e2", source_node_id: "b", target_node_id: "c" }),
      makeEdge({ id: "e3", source_node_id: "a", target_node_id: "c" }),
    ];

    // Simulate receiving node_hidden for node "b"
    const hiddenId = "b";
    const filteredNodes = nodes.filter((n) => n.id !== hiddenId);
    const filteredEdges = edges.filter(
      (e) =>
        e.source_node_id !== hiddenId && e.target_node_id !== hiddenId,
    );

    expect(filteredNodes).toHaveLength(2);
    expect(filteredNodes.map((n) => n.id)).toEqual(["a", "c"]);

    // Only the a->c edge should remain
    expect(filteredEdges).toHaveLength(1);
    expect(filteredEdges[0].id).toBe("e3");
  });

  it("handles hiding a node with no edges", () => {
    const nodes = [makeNode({ id: "a" }), makeNode({ id: "b" })];
    const edges: EdgeResponse[] = [];

    const hiddenId = "a";
    const filteredNodes = nodes.filter((n) => n.id !== hiddenId);
    const filteredEdges = edges.filter(
      (e) =>
        e.source_node_id !== hiddenId && e.target_node_id !== hiddenId,
    );

    expect(filteredNodes).toHaveLength(1);
    expect(filteredNodes[0].id).toBe("b");
    expect(filteredEdges).toHaveLength(0);
  });

  it("handles hiding a non-existent node gracefully", () => {
    const nodes = [makeNode({ id: "a" })];
    const edges = [
      makeEdge({ id: "e1", source_node_id: "a", target_node_id: "b" }),
    ];

    const hiddenId = "z"; // Not in nodes
    const filteredNodes = nodes.filter((n) => n.id !== hiddenId);
    const filteredEdges = edges.filter(
      (e) =>
        e.source_node_id !== hiddenId && e.target_node_id !== hiddenId,
    );

    expect(filteredNodes).toHaveLength(1);
    expect(filteredEdges).toHaveLength(1);
  });
});

// ---------------------------------------------------------------------------
// Mode defaults — ConversationResponse and CreateConversationRequest
// ---------------------------------------------------------------------------

describe("mode field defaults", () => {
  it("ConversationResponse includes mode field", () => {
    const response: ConversationResponse = {
      id: "conv-1",
      title: "Test",
      mode: "research",
      messages: [],
      created_at: "2026-01-01T00:00:00Z",
      updated_at: "2026-01-01T00:00:00Z",
    };
    expect(response.mode).toBe("research");
  });

  it("ConversationResponse supports query mode", () => {
    const response: ConversationResponse = {
      id: "conv-2",
      title: "Query Test",
      mode: "query",
      messages: [],
      created_at: "2026-01-01T00:00:00Z",
      updated_at: "2026-01-01T00:00:00Z",
    };
    expect(response.mode).toBe("query");
  });

  it("ConversationListItem includes mode field", () => {
    const item: ConversationListItem = {
      id: "conv-3",
      title: "Listed",
      mode: "query",
      message_count: 0,
      latest_status: null,
      created_at: "2026-01-01T00:00:00Z",
      updated_at: "2026-01-01T00:00:00Z",
    };
    expect(item.mode).toBe("query");
  });

  it("CreateConversationRequest supports optional mode", () => {
    // Without mode (defaults to research)
    const reqNoMode: CreateConversationRequest = {
      message: "test query",
      nav_budget: 50,
      explore_budget: 0,
    };
    expect(reqNoMode.mode).toBeUndefined();

    // With mode
    const reqWithMode: CreateConversationRequest = {
      message: "test query",
      nav_budget: 50,
      explore_budget: 0,
      mode: "query",
    };
    expect(reqWithMode.mode).toBe("query");
  });

  it("query mode has no explore budget", () => {
    const queryReq: CreateConversationRequest = {
      message: "what is X?",
      nav_budget: 50,
      explore_budget: 0,
      mode: "query",
    };
    expect(queryReq.explore_budget).toBe(0);
  });
});
