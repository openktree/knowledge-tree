import type { NodeResponse, EdgeResponse } from "@/types";
import type cytoscape from "cytoscape";

// ---------------------------------------------------------------------------
// Color helpers
// ---------------------------------------------------------------------------

/**
 * Returns a hex color for a given node type.
 * concept → blue, perspective → purple, entity → emerald, event → orange.
 */
export function getNodeTypeColor(nodeType: string): string {
  const colors: Record<string, string> = {
    concept: "#3b82f6", // blue-500
    perspective: "#a855f7", // purple-500
    entity: "#10b981", // emerald-500
    event: "#f97316", // orange-500
    location: "#06b6d4", // cyan-500
    synthesis: "#ec4899", // pink-500
  };
  return colors[nodeType] ?? "#6b7280"; // gray-500 fallback
}

/**
 * Returns a hex color for an edge based on its type and weight.
 * Weight is a fact count — higher counts produce stronger colors.
 * Cross-type edges use a violet palette; related edges use green.
 */
export function getEdgeColor(type: string, weight?: number): string {
  if (weight === undefined || weight === null) return "#6b7280";
  if (type === "contradicts") return "#f59e0b"; // amber-500
  if (type === "cross_type") {
    if (weight >= 10) return "#7c3aed"; // violet-600 — strong evidence
    return "#a78bfa"; // violet-400 — moderate evidence
  }
  if (weight >= 10) return "#22c55e"; // green-500 — strong evidence
  return "#86efac"; // green-300 — moderate evidence
}

// ---------------------------------------------------------------------------
// Type entry constants (used by legend + filters)
// ---------------------------------------------------------------------------

export interface TypeEntry {
  key: string;
  label: string;
  color: string;
}

export const NODE_TYPE_ENTRIES: TypeEntry[] = [
  { key: "concept", label: "Concept", color: "#3b82f6" },
  { key: "perspective", label: "Perspective", color: "#a855f7" },
  { key: "entity", label: "Entity", color: "#10b981" },
  { key: "event", label: "Event", color: "#f97316" },
];

export const EDGE_TYPE_ENTRIES: TypeEntry[] = [
  { key: "related", label: "Related", color: "#6b7280" },
  { key: "contradicts", label: "Contradicts", color: "#f59e0b" },
  { key: "cross_type", label: "Cross-Type", color: "#8b5cf6" },
  { key: "parent", label: "Parent", color: "#94a3b8" },
];

// ---------------------------------------------------------------------------
// Root node detection
// ---------------------------------------------------------------------------

/** Concept names of the three root/default-parent nodes. */
export const ROOT_NODE_CONCEPTS = new Set([
  "All Concepts",
  "All Events",
  "All Perspectives",
]);

/** Returns true if a node is one of the three root container nodes. */
export function isRootNode(node: NodeResponse): boolean {
  return ROOT_NODE_CONCEPTS.has(node.concept);
}

// ---------------------------------------------------------------------------
// Visibility filtering
// ---------------------------------------------------------------------------

/**
 * Filters nodes and edges by hidden type sets.
 * - Removes nodes whose node_type is in hiddenNodeTypes
 * - Removes edges whose relationship_type is in hiddenEdgeTypes
 * - Removes edges where either endpoint was filtered out
 * - Optionally hides root container nodes (All Concepts, All Events, All Perspectives)
 */
export function filterGraphByVisibility(
  nodes: NodeResponse[],
  edges: EdgeResponse[],
  hiddenNodeTypes: ReadonlySet<string>,
  hiddenEdgeTypes: ReadonlySet<string>,
  hideRootNodes: boolean = false,
): { nodes: NodeResponse[]; edges: EdgeResponse[] } {
  const visibleNodes = nodes.filter(
    (n) =>
      !hiddenNodeTypes.has(n.node_type ?? "concept") &&
      !(hideRootNodes && isRootNode(n)),
  );
  const visibleNodeIds = new Set(visibleNodes.map((n) => n.id));
  const visibleEdges = edges.filter(
    (e) =>
      !hiddenEdgeTypes.has(e.relationship_type) &&
      visibleNodeIds.has(e.source_node_id) &&
      visibleNodeIds.has(e.target_node_id),
  );
  return { nodes: visibleNodes, edges: visibleEdges };
}

// ---------------------------------------------------------------------------
// Cytoscape element conversion
// ---------------------------------------------------------------------------

/**
 * Converts Knowledge Tree NodeResponse[] and EdgeResponse[] into
 * Cytoscape ElementDefinition[] suitable for react-cytoscapejs.
 */
/** Default max node size in pixels. */
export const DEFAULT_MAX_NODE_SIZE = 70;

export function toCytoscapeElements(
  nodes: NodeResponse[],
  edges: EdgeResponse[],
  maxNodeSize: number = DEFAULT_MAX_NODE_SIZE,
  hiddenEdgeTypes?: ReadonlySet<string>,
): cytoscape.ElementDefinition[] {
  // Build a set of node IDs so we only include edges with valid endpoints
  const nodeIds = new Set(nodes.map((n) => n.id));

  // Compute connection score per node: count weighted edges
  const connectionScore = new Map<string, number>();
  for (const node of nodes) {
    connectionScore.set(node.id, 0);
  }
  for (const edge of edges) {
    if (!nodeIds.has(edge.source_node_id) || !nodeIds.has(edge.target_node_id))
      continue;
    connectionScore.set(
      edge.source_node_id,
      (connectionScore.get(edge.source_node_id) ?? 0) +
        Math.abs(edge.weight),
    );
    connectionScore.set(
      edge.target_node_id,
      (connectionScore.get(edge.target_node_id) ?? 0) +
        Math.abs(edge.weight),
    );
  }

  // Normalize with log scale so largest node is 3× smallest.
  // Shift scores so the minimum maps to 1 (log10(1) = 0), then normalize
  // the log-scaled values. This means each 10× increase in the shifted
  // connection score roughly doubles the visual size.
  const MIN_NODE_SIZE = 10;
  const MAX_NODE_SIZE = Math.max(MIN_NODE_SIZE, maxNodeSize);
  const scores = Array.from(connectionScore.values());
  const minScore = Math.min(...scores);
  const maxScore = Math.max(...scores);
  const maxLog = Math.log10(maxScore - minScore + 1);

  const cyNodes: cytoscape.ElementDefinition[] = nodes.map((node) => {
    const score = connectionScore.get(node.id) ?? 0;
    const logVal = Math.log10(score - minScore + 1);
    const t = maxLog > 0 ? logVal / maxLog : 0.5;
    return {
      data: {
        id: node.id,
        label: node.concept,
        concept: node.concept,
        attractor: node.attractor,
        filter_id: node.filter_id,
        max_content_tokens: node.max_content_tokens,
        created_at: node.created_at,
        updated_at: node.updated_at,
        update_count: node.update_count,
        access_count: node.access_count,
        richness: node.richness,
        node_type: node.node_type ?? "concept",
        dialectic_role: (node.metadata?.dialectic_role as string) ?? null,
        dialectic_pair_id: (node.metadata?.dialectic_pair_id as string) ?? null,
        // Pre-computed visual mappings stored as data for stylesheet use
        nodeTypeColor: getNodeTypeColor(node.node_type ?? "concept"),
        nodeSize: Math.round(MIN_NODE_SIZE + t * (MAX_NODE_SIZE - MIN_NODE_SIZE)),
      },
      group: "nodes" as const,
    };
  });

  const cyEdges: cytoscape.ElementDefinition[] = edges
    .filter(
      (edge) =>
        nodeIds.has(edge.source_node_id) && nodeIds.has(edge.target_node_id),
    )
    .map((edge) => ({
      data: {
        id: edge.id,
        source: edge.source_node_id,
        target: edge.target_node_id,
        relationship_type: edge.relationship_type,
        weight: edge.weight,
        justification: edge.justification ?? null,
        supporting_fact_ids: edge.supporting_fact_ids ?? [],
        created_at: edge.created_at,
        edgeColor: getEdgeColor(edge.relationship_type, edge.weight),
        // Weight is a fact count — use log scale for visual width
        edgeWidth: Math.max(0.5, Math.min(4, Math.log10(Math.max(1, edge.weight)) * 2 + 0.5)),
      },
      group: "edges" as const,
    }));

  // Generate synthetic parent edges for nodes whose parent is in the graph
  const parentEdges: cytoscape.ElementDefinition[] = hiddenEdgeTypes?.has("parent") ? [] : nodes
    .filter((n) => n.parent_id && nodeIds.has(n.parent_id))
    .map((n) => ({
      data: {
        id: `parent-${n.id}`,
        source: n.id,
        target: n.parent_id!,
        edgeType: "parent",
        isParent: true,
        edgeColor: "#94a3b8", // slate-400
        edgeWidth: 1,
      },
      group: "edges" as const,
    }));

  return [...cyNodes, ...cyEdges, ...parentEdges];
}

// ---------------------------------------------------------------------------
// Layout configurations
// ---------------------------------------------------------------------------

/**
 * Per-edge force settings for the "related" edge type.
 * Base ideal edge length (before the adaptive scale factor is added).
 */
export interface EdgeForceSettings {
  /** Base ideal edge length for "related" edges */
  related: number;
  /** Base ideal edge length for "cross_type" edges */
  cross_type: number;
  /** Base ideal edge length for "contradicts" edges */
  contradicts: number;
  /** Base ideal edge length for synthetic "parent" edges */
  parent: number;
}

/** Sensible defaults for edge ideal lengths. */
export const defaultEdgeForces: EdgeForceSettings = {
  related: 120,
  cross_type: 140,
  contradicts: 60,
  parent: 216,
};

/** Display labels for each layout option (used by GraphControls). */
export const layoutLabels: Record<string, string> = {
  fcose: "Force-directed",
  grid: "Grid",
  circle: "Circle",
};

/**
 * Returns Cytoscape layout options for the given layout name, scaled to the
 * current node count. For fcose, per-edge ideal lengths use weight to set
 * attraction: positive weights pull nodes together, negative weights push apart.
 */
export function getLayoutOptions(
  name: string,
  nodeCount: number,
  edgeForces: EdgeForceSettings = defaultEdgeForces,
): cytoscape.LayoutOptions {
  // Adaptive scale factor: more nodes → more spacing
  const scale = Math.min(nodeCount * 2, 100);

  switch (name) {
    case "fcose":
      return {
        name: "fcose",
        animate: true,
        animationDuration: 500,
        quality: "default",
        nodeRepulsion: () => 8000 + nodeCount * 300,
        idealEdgeLength: (edge: cytoscape.EdgeSingular) => {
          // Synthetic parent edges have no weight — use a long, fixed length
          if (edge.data("isParent")) return edgeForces.parent + scale;
          const weight = (edge.data("weight") as number) ?? 0;
          const relType = (edge.data("relationship_type") as string) ?? "related";
          // Contradicts (thesis↔antithesis) pairs stay close together
          if (relType === "contradicts") return edgeForces.contradicts + scale;
          const base = relType === "cross_type" ? edgeForces.cross_type : edgeForces.related;
          // Weight is a fact count — higher counts pull nodes together (shorter edge)
          const logWeight = Math.log10(Math.max(1, weight));
          const weightFactor = Math.max(0.4, 1.0 - logWeight * 0.2);
          return base * weightFactor + scale;
        },
        edgeElasticity: () => 100,
        gravity: 0.05,
        gravityRange: 3.8,
        numIter: 2500,
        padding: 50,
        nodeSeparation: 100,
        // Separate disconnected subgraphs instead of stacking them
        tile: true,
        tilingPaddingVertical: 60,
        tilingPaddingHorizontal: 60,
      } as cytoscape.LayoutOptions;

    case "grid":
      return {
        name: "grid",
        animate: true,
        animationDuration: 500,
        padding: 40,
        avoidOverlap: true,
        condense: true,
      } as cytoscape.LayoutOptions;

    case "circle":
      return {
        name: "circle",
        animate: true,
        animationDuration: 500,
        padding: 40,
        avoidOverlap: true,
      } as cytoscape.LayoutOptions;

    default:
      // Fall back to fcose for unknown names
      return getLayoutOptions("fcose", nodeCount);
  }
}
