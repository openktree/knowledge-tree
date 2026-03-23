import { describe, it, expect } from "vitest";
import { computeStateAtPosition } from "@/lib/timeline-utils";
import type { NodeResponse, EdgeResponse, TimelineEntry } from "@/types";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function makeNode(overrides: Partial<NodeResponse> = {}): NodeResponse {
  return {
    id: "node-1",
    concept: "Photosynthesis",
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

function makeEntry(
  index: number,
  overrides: Partial<TimelineEntry> = {},
): TimelineEntry {
  return {
    index,
    kind: "node_created",
    timestamp: "2026-01-01T00:00:00Z",
    ...overrides,
  };
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("computeStateAtPosition", () => {
  it("returns empty arrays for empty entries", () => {
    const result = computeStateAtPosition([], 0);
    expect(result.nodes).toEqual([]);
    expect(result.edges).toEqual([]);
  });

  it("returns a single node at position 0", () => {
    const node = makeNode({ id: "n1", concept: "Alpha" });
    const entries: TimelineEntry[] = [
      makeEntry(0, { kind: "node_created", node }),
    ];

    const result = computeStateAtPosition(entries, 0);
    expect(result.nodes).toHaveLength(1);
    expect(result.nodes[0].id).toBe("n1");
    expect(result.edges).toHaveLength(0);
  });

  it("accumulates nodes and edges over multiple entries", () => {
    const n1 = makeNode({ id: "n1", concept: "A" });
    const n2 = makeNode({ id: "n2", concept: "B" });
    const e1 = makeEdge({
      id: "e1",
      source_node_id: "n1",
      target_node_id: "n2",
    });

    const entries: TimelineEntry[] = [
      makeEntry(0, { kind: "node_created", node: n1 }),
      makeEntry(1, { kind: "node_created", node: n2 }),
      makeEntry(2, { kind: "edge_created", edge: e1 }),
    ];

    // At position 1, we should have 2 nodes and 0 edges
    const atPos1 = computeStateAtPosition(entries, 1);
    expect(atPos1.nodes).toHaveLength(2);
    expect(atPos1.edges).toHaveLength(0);

    // At position 2, we should have 2 nodes and 1 edge
    const atPos2 = computeStateAtPosition(entries, 2);
    expect(atPos2.nodes).toHaveLength(2);
    expect(atPos2.edges).toHaveLength(1);
  });

  it("updates nodes when the same ID appears again (node_expanded)", () => {
    const n1v1 = makeNode({ id: "n1", concept: "A", richness: 0.3 });
    const n1v2 = makeNode({ id: "n1", concept: "A", richness: 0.9 });

    const entries: TimelineEntry[] = [
      makeEntry(0, { kind: "node_created", node: n1v1 }),
      makeEntry(1, { kind: "node_expanded", node: n1v2 }),
    ];

    const result = computeStateAtPosition(entries, 1);
    expect(result.nodes).toHaveLength(1);
    expect(result.nodes[0].richness).toBe(0.9);
  });

  it("deduplicates edges by ID", () => {
    const e1 = makeEdge({ id: "e1" });

    const entries: TimelineEntry[] = [
      makeEntry(0, { kind: "edge_created", edge: e1 }),
      makeEntry(1, { kind: "edge_created", edge: e1 }),
    ];

    const result = computeStateAtPosition(entries, 1);
    expect(result.edges).toHaveLength(1);
  });

  it("clamps position above max to last entry", () => {
    const n1 = makeNode({ id: "n1" });
    const entries: TimelineEntry[] = [
      makeEntry(0, { kind: "node_created", node: n1 }),
    ];

    const result = computeStateAtPosition(entries, 100);
    expect(result.nodes).toHaveLength(1);
  });

  it("clamps negative position to 0", () => {
    const n1 = makeNode({ id: "n1" });
    const n2 = makeNode({ id: "n2" });
    const entries: TimelineEntry[] = [
      makeEntry(0, { kind: "node_created", node: n1 }),
      makeEntry(1, { kind: "node_created", node: n2 }),
    ];

    const result = computeStateAtPosition(entries, -5);
    expect(result.nodes).toHaveLength(1);
    expect(result.nodes[0].id).toBe("n1");
  });

  it("handles entries with only edges (no nodes)", () => {
    const e1 = makeEdge({ id: "e1" });
    const entries: TimelineEntry[] = [
      makeEntry(0, { kind: "edge_created", edge: e1 }),
    ];

    const result = computeStateAtPosition(entries, 0);
    expect(result.nodes).toHaveLength(0);
    expect(result.edges).toHaveLength(1);
  });
});
