import { describe, it, expect } from "vitest";
import {
  getConvergenceColor,
  getEdgeColor,
  getNodeTypeColor,
  toCytoscapeElements,
  getLayoutOptions,
  layoutLabels,
  NODE_TYPE_ENTRIES,
  EDGE_TYPE_ENTRIES,
  filterGraphByVisibility,
} from "@/lib/graph-utils";
import type { NodeResponse, EdgeResponse } from "@/types";

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
    weight: 5,
    justification: null,
    weight_source: null,
    supporting_fact_ids: [],
    created_at: "2026-01-01T00:00:00Z",
    ...overrides,
  };
}

// ---------------------------------------------------------------------------
// getConvergenceColor
// ---------------------------------------------------------------------------

describe("getConvergenceColor", () => {
  it("returns green for scores >= 0.7", () => {
    expect(getConvergenceColor(0.7)).toBe("#22c55e");
    expect(getConvergenceColor(0.85)).toBe("#22c55e");
    expect(getConvergenceColor(1.0)).toBe("#22c55e");
  });

  it("returns yellow for scores >= 0.4 and < 0.7", () => {
    expect(getConvergenceColor(0.4)).toBe("#eab308");
    expect(getConvergenceColor(0.55)).toBe("#eab308");
    expect(getConvergenceColor(0.69)).toBe("#eab308");
  });

  it("returns red for scores < 0.4", () => {
    expect(getConvergenceColor(0)).toBe("#ef4444");
    expect(getConvergenceColor(0.1)).toBe("#ef4444");
    expect(getConvergenceColor(0.39)).toBe("#ef4444");
  });
});

// ---------------------------------------------------------------------------
// getNodeTypeColor
// ---------------------------------------------------------------------------

describe("getNodeTypeColor", () => {
  it("returns blue for concept nodes", () => {
    expect(getNodeTypeColor("concept")).toBe("#3b82f6");
  });

  it("returns purple for perspective nodes", () => {
    expect(getNodeTypeColor("perspective")).toBe("#a855f7");
  });

  it("returns emerald for entity nodes", () => {
    expect(getNodeTypeColor("entity")).toBe("#10b981");
  });

  it("returns orange for event nodes", () => {
    expect(getNodeTypeColor("event")).toBe("#f97316");
  });

  it("returns gray for unknown node types", () => {
    expect(getNodeTypeColor("unknown")).toBe("#6b7280");
    expect(getNodeTypeColor("")).toBe("#6b7280");
  });
});

// ---------------------------------------------------------------------------
// getEdgeColor
// ---------------------------------------------------------------------------

describe("getEdgeColor", () => {
  it("returns strong green for high fact count (>= 10)", () => {
    expect(getEdgeColor("related", 15)).toBe("#22c55e");
    expect(getEdgeColor("related", 100)).toBe("#22c55e");
  });

  it("returns light green for low fact count (< 10)", () => {
    expect(getEdgeColor("related", 3)).toBe("#86efac");
    expect(getEdgeColor("related", 1)).toBe("#86efac");
  });

  it("returns gray for undefined weight", () => {
    expect(getEdgeColor("related")).toBe("#6b7280");
  });

  it("returns strong violet for cross_type with high fact count", () => {
    expect(getEdgeColor("cross_type", 15)).toBe("#7c3aed");
  });

  it("returns light violet for cross_type with low fact count", () => {
    expect(getEdgeColor("cross_type", 3)).toBe("#a78bfa");
  });

  it("returns amber for contradicts regardless of weight", () => {
    expect(getEdgeColor("contradicts", 5)).toBe("#f59e0b");
  });
});

// ---------------------------------------------------------------------------
// toCytoscapeElements
// ---------------------------------------------------------------------------

describe("toCytoscapeElements", () => {
  it("converts nodes and edges into cytoscape element definitions", () => {
    const nodes = [
      makeNode({ id: "n1", concept: "A", richness: 0.8, convergence_score: 0.8, access_count: 5 }),
      makeNode({ id: "n2", concept: "B", richness: 0.3, convergence_score: 0.3, access_count: 1 }),
    ];
    const edges = [
      makeEdge({
        id: "e1",
        source_node_id: "n1",
        target_node_id: "n2",
        relationship_type: "related",
        weight: 8,
      }),
    ];

    const elements = toCytoscapeElements(nodes, edges);

    // Should have 2 nodes + 1 edge
    expect(elements).toHaveLength(3);

    const cyNodes = elements.filter((el) => el.group === "nodes");
    const cyEdges = elements.filter((el) => el.group === "edges");

    expect(cyNodes).toHaveLength(2);
    expect(cyEdges).toHaveLength(1);

    // Verify node data mapping
    expect(cyNodes[0].data.id).toBe("n1");
    expect(cyNodes[0].data.label).toBe("A");
    expect(cyNodes[0].data.concept).toBe("A");
    expect(cyNodes[0].data.nodeTypeColor).toBe("#3b82f6"); // concept = blue

    expect(cyNodes[1].data.id).toBe("n2");
    expect(cyNodes[1].data.nodeTypeColor).toBe("#3b82f6"); // concept = blue

    // Verify edge data mapping
    expect(cyEdges[0].data.id).toBe("e1");
    expect(cyEdges[0].data.source).toBe("n1");
    expect(cyEdges[0].data.target).toBe("n2");
    expect(cyEdges[0].data.relationship_type).toBe("related");
  });

  it("filters out edges with missing node endpoints", () => {
    const nodes = [makeNode({ id: "n1" })];
    const edges = [
      makeEdge({
        id: "e1",
        source_node_id: "n1",
        target_node_id: "n2", // n2 does not exist in nodes
      }),
      makeEdge({
        id: "e2",
        source_node_id: "n3", // n3 does not exist
        target_node_id: "n1",
      }),
    ];

    const elements = toCytoscapeElements(nodes, edges);

    const cyEdges = elements.filter((el) => el.group === "edges");
    expect(cyEdges).toHaveLength(0);
  });

  it("handles empty arrays", () => {
    const elements = toCytoscapeElements([], []);
    expect(elements).toEqual([]);
  });

  it("handles nodes with no edges", () => {
    const nodes = [makeNode({ id: "n1" }), makeNode({ id: "n2" })];
    const elements = toCytoscapeElements(nodes, []);

    expect(elements).toHaveLength(2);
    expect(elements.every((el) => el.group === "nodes")).toBe(true);
  });

  it("uses logarithmic scale for nodeSize based on connection weight", () => {
    const nodes = [
      makeNode({ id: "n1" }),
      makeNode({ id: "n2" }),
      makeNode({ id: "n3" }),
    ];
    const edges = [
      makeEdge({ id: "e1", source_node_id: "n1", target_node_id: "n2", relationship_type: "related", weight: 10 }),
      makeEdge({ id: "e2", source_node_id: "n1", target_node_id: "n3", relationship_type: "related", weight: 5 }),
    ];

    const elements = toCytoscapeElements(nodes, edges);
    const cyNodes = elements.filter((el) => el.group === "nodes");

    // n1 has highest score (10 + 5 = 15) → MAX_NODE_SIZE = 70
    expect(cyNodes[0].data.nodeSize).toBe(70);
    // n2 has score 10
    expect(cyNodes[1].data.nodeSize).toBeGreaterThan(10);
    // n3 has lowest score (5) → MIN_NODE_SIZE = 10
    expect(cyNodes[2].data.nodeSize).toBe(10);
  });

  it("gives equal nodes the same size when all scores are equal", () => {
    const nodes = [makeNode({ id: "n1" }), makeNode({ id: "n2" })];
    const edges = [
      makeEdge({ id: "e1", source_node_id: "n1", target_node_id: "n2", relationship_type: "related", weight: 5 }),
    ];

    const elements = toCytoscapeElements(nodes, edges);
    const cyNodes = elements.filter((el) => el.group === "nodes");

    // Both nodes have same score → same size (midpoint = 40)
    expect(cyNodes[0].data.nodeSize).toBe(cyNodes[1].data.nodeSize);
    expect(cyNodes[0].data.nodeSize).toBe(40);
  });

  it("defaults to midpoint size with no connections", () => {
    const nodes = [makeNode({ id: "n1" })];
    const elements = toCytoscapeElements(nodes, []);

    // Single node, no range → t = 0.5 → 10 + 0.5 * 60 = 40
    expect(elements[0].data.nodeSize).toBe(40);
  });

  it("generates parent edges when parent_id matches a visible node", () => {
    const nodes = [
      makeNode({ id: "child", concept: "Child", parent_id: "parent-node", parent_concept: "Parent" }),
      makeNode({ id: "parent-node", concept: "Parent" }),
    ];
    const elements = toCytoscapeElements(nodes, []);
    const parentEdges = elements.filter((el) => el.group === "edges" && el.data.isParent);

    expect(parentEdges).toHaveLength(1);
    expect(parentEdges[0].data.id).toBe("parent-child");
    expect(parentEdges[0].data.source).toBe("child");
    expect(parentEdges[0].data.target).toBe("parent-node");
    expect(parentEdges[0].data.edgeType).toBe("parent");
  });

  it("does not generate parent edges when parent is not in the node set", () => {
    const nodes = [
      makeNode({ id: "child", concept: "Child", parent_id: "missing-parent", parent_concept: "Missing" }),
      makeNode({ id: "other", concept: "Other" }),
    ];
    const elements = toCytoscapeElements(nodes, []);
    const parentEdges = elements.filter((el) => el.group === "edges" && el.data.isParent);

    expect(parentEdges).toHaveLength(0);
  });

  it("includes dialectic_role and dialectic_pair_id in node data", () => {
    const thesis = makeNode({
      id: "thesis-1",
      concept: "Thesis claim",
      node_type: "perspective",
      metadata: { dialectic_role: "thesis", dialectic_pair_id: "anti-1" },
    });
    const antithesis = makeNode({
      id: "anti-1",
      concept: "Antithesis claim",
      node_type: "perspective",
      metadata: { dialectic_role: "antithesis", dialectic_pair_id: "thesis-1" },
    });
    const plain = makeNode({ id: "plain-1" });

    const elements = toCytoscapeElements([thesis, antithesis, plain], []);
    const cyNodes = elements.filter((el) => el.group === "nodes");

    expect(cyNodes[0].data.dialectic_role).toBe("thesis");
    expect(cyNodes[0].data.dialectic_pair_id).toBe("anti-1");
    expect(cyNodes[1].data.dialectic_role).toBe("antithesis");
    expect(cyNodes[1].data.dialectic_pair_id).toBe("thesis-1");
    expect(cyNodes[2].data.dialectic_role).toBeNull();
    expect(cyNodes[2].data.dialectic_pair_id).toBeNull();
  });

  it("computes edgeWidth using log scale clamped between 0.5 and 4", () => {
    const nodes = [makeNode({ id: "n1" }), makeNode({ id: "n2" })];
    const lowWeight = makeEdge({
      id: "e1",
      source_node_id: "n1",
      target_node_id: "n2",
      weight: 1,
    });
    const highWeight = makeEdge({
      id: "e2",
      source_node_id: "n1",
      target_node_id: "n2",
      weight: 100,
    });

    const elements = toCytoscapeElements(nodes, [lowWeight, highWeight]);
    const cyEdges = elements.filter((el) => el.group === "edges");

    // edgeWidth = max(0.5, min(4, log10(max(1, weight)) * 2 + 0.5))
    expect(cyEdges[0].data.edgeWidth).toBe(0.5); // log10(1) = 0 → 0 * 2 + 0.5 = 0.5
    expect(cyEdges[1].data.edgeWidth).toBe(4); // log10(100) = 2 → 2*2+0.5 = 4.5 → clamped to 4
  });
});

// ---------------------------------------------------------------------------
// layoutLabels
// ---------------------------------------------------------------------------

describe("layoutLabels", () => {
  it("contains entries for fcose, grid, and circle", () => {
    expect(layoutLabels).toHaveProperty("fcose");
    expect(layoutLabels).toHaveProperty("grid");
    expect(layoutLabels).toHaveProperty("circle");
  });

  it("has string labels for all entries", () => {
    for (const label of Object.values(layoutLabels)) {
      expect(typeof label).toBe("string");
      expect(label.length).toBeGreaterThan(0);
    }
  });
});

// ---------------------------------------------------------------------------
// getLayoutOptions
// ---------------------------------------------------------------------------

describe("getLayoutOptions", () => {
  it("returns fcose layout options with correct name", () => {
    const opts = getLayoutOptions("fcose", 10);
    expect(opts.name).toBe("fcose");
  });

  it("returns grid layout options", () => {
    const opts = getLayoutOptions("grid", 10);
    expect(opts.name).toBe("grid");
  });

  it("returns circle layout options", () => {
    const opts = getLayoutOptions("circle", 10);
    expect(opts.name).toBe("circle");
  });

  it("falls back to fcose for unknown layout names", () => {
    const opts = getLayoutOptions("nonexistent", 10);
    expect(opts.name).toBe("fcose");
  });

  it("scales fcose node repulsion with node count", () => {
    const small = getLayoutOptions("fcose", 5) as unknown as Record<string, unknown>;
    const large = getLayoutOptions("fcose", 50) as unknown as Record<string, unknown>;

    // nodeRepulsion is a function — call it to compare
    const smallRepulsion = (small.nodeRepulsion as () => number)();
    const largeRepulsion = (large.nodeRepulsion as () => number)();
    expect(largeRepulsion).toBeGreaterThan(smallRepulsion);
  });
});

// ---------------------------------------------------------------------------
// NODE_TYPE_ENTRIES
// ---------------------------------------------------------------------------

describe("NODE_TYPE_ENTRIES", () => {
  it("has 4 entries", () => {
    expect(NODE_TYPE_ENTRIES).toHaveLength(4);
  });

  it("has colors matching getNodeTypeColor", () => {
    for (const entry of NODE_TYPE_ENTRIES) {
      expect(entry.color).toBe(getNodeTypeColor(entry.key));
    }
  });

  it("has unique keys", () => {
    const keys = NODE_TYPE_ENTRIES.map((e) => e.key);
    expect(new Set(keys).size).toBe(keys.length);
  });
});

// ---------------------------------------------------------------------------
// EDGE_TYPE_ENTRIES
// ---------------------------------------------------------------------------

describe("EDGE_TYPE_ENTRIES", () => {
  it("has entries for related, contradicts, cross_type, and parent", () => {
    expect(EDGE_TYPE_ENTRIES).toHaveLength(4);
    expect(EDGE_TYPE_ENTRIES[0].key).toBe("related");
    expect(EDGE_TYPE_ENTRIES[1].key).toBe("contradicts");
    expect(EDGE_TYPE_ENTRIES[2].key).toBe("cross_type");
    expect(EDGE_TYPE_ENTRIES[3].key).toBe("parent");
  });

  it("has unique keys", () => {
    const keys = EDGE_TYPE_ENTRIES.map((e) => e.key);
    expect(new Set(keys).size).toBe(keys.length);
  });
});

// ---------------------------------------------------------------------------
// filterGraphByVisibility
// ---------------------------------------------------------------------------

describe("filterGraphByVisibility", () => {
  const n1 = makeNode({ id: "n1", node_type: "concept" });
  const n2 = makeNode({ id: "n2", node_type: "perspective" });
  const n3 = makeNode({ id: "n3", node_type: "entity" });
  const e1 = makeEdge({ id: "e1", source_node_id: "n1", target_node_id: "n2", relationship_type: "related" });
  const e2 = makeEdge({ id: "e2", source_node_id: "n2", target_node_id: "n3", relationship_type: "related" });
  const e3 = makeEdge({ id: "e3", source_node_id: "n1", target_node_id: "n3", relationship_type: "related" });

  it("returns all nodes and edges when nothing is hidden", () => {
    const result = filterGraphByVisibility([n1, n2, n3], [e1, e2, e3], new Set(), new Set());
    expect(result.nodes).toHaveLength(3);
    expect(result.edges).toHaveLength(3);
  });

  it("hides nodes of a hidden type", () => {
    const result = filterGraphByVisibility([n1, n2, n3], [e1, e2, e3], new Set(["perspective"]), new Set());
    expect(result.nodes).toHaveLength(2);
    expect(result.nodes.map((n) => n.id)).toEqual(["n1", "n3"]);
  });

  it("removes edges connected to hidden nodes", () => {
    const result = filterGraphByVisibility([n1, n2, n3], [e1, e2, e3], new Set(["perspective"]), new Set());
    // e1 (n1→n2) and e2 (n2→n3) should be removed because n2 is hidden
    expect(result.edges).toHaveLength(1);
    expect(result.edges[0].id).toBe("e3");
  });

  it("hides edges of a hidden type", () => {
    const result = filterGraphByVisibility([n1, n2, n3], [e1, e2, e3], new Set(), new Set(["related"]));
    expect(result.nodes).toHaveLength(3);
    expect(result.edges).toHaveLength(0);
  });

  it("combines node and edge type filters", () => {
    const result = filterGraphByVisibility(
      [n1, n2, n3],
      [e1, e2, e3],
      new Set(["entity"]),
      new Set(),
    );
    // n3 hidden → e2 (n2→n3) and e3 (n1→n3) removed
    expect(result.nodes).toHaveLength(2);
    expect(result.edges).toHaveLength(1);
    expect(result.edges[0].id).toBe("e1");
  });

  it("handles empty inputs", () => {
    const result = filterGraphByVisibility([], [], new Set(["concept"]), new Set(["related"]));
    expect(result.nodes).toHaveLength(0);
    expect(result.edges).toHaveLength(0);
  });

  it("filters cross_type edges independently from related edges", () => {
    const crossEdge = makeEdge({ id: "e-cross", source_node_id: "n1", target_node_id: "n3", relationship_type: "cross_type" });
    const result = filterGraphByVisibility([n1, n2, n3], [e1, e2, e3, crossEdge], new Set(), new Set(["cross_type"]));
    // cross_type edge hidden, related edges kept
    expect(result.edges).toHaveLength(3);
    expect(result.edges.map((e) => e.id)).toEqual(["e1", "e2", "e3"]);
  });
});
