import type cytoscape from "cytoscape";

/**
 * Cytoscape stylesheet for the Knowledge Tree graph visualization.
 *
 * Relies on pre-computed data fields set by toCytoscapeElements():
 *   - node: nodeTypeColor, nodeSize, richness, label, node_type
 *   - edge: edgeColor, edgeWidth, relationship_type, weight
 */
const graphStylesheet: cytoscape.StylesheetJsonBlock[] = [
  // -------------------------------------------------------------------------
  // Node styles
  // -------------------------------------------------------------------------
  {
    selector: "node",
    style: {
      // Shape & size
      shape: "ellipse",
      width: "data(nodeSize)",
      height: "data(nodeSize)",

      // Color: background driven by node type
      "background-color": "data(nodeTypeColor)",
      "background-opacity": 0.85,

      // Label
      label: "data(label)",
      color: "#f1f5f9", // slate-100
      "font-size": "7px",
      "font-weight": 500,
      "text-valign": "center",
      "text-halign": "center",
      "text-wrap": "ellipsis",
      "text-max-width": "80px",
      "text-outline-color": "#0f172a", // slate-900
      "text-outline-width": 1.5,

      // Border: solid if richness > 0.5, dashed otherwise
      "border-width": 2,
      "border-color": "#475569", // slate-600
      "border-style": "solid",
      "border-opacity": 0.9,

      // Interaction
      "overlay-opacity": 0,
      "transition-property":
        "background-color, border-color, border-width, width, height",
      "transition-duration": 200,
    } as unknown as cytoscape.Css.Node,
  },

  // Low-richness nodes get a dashed border
  {
    selector: "node[richness <= 0.5]",
    style: {
      "border-style": "dashed",
      "border-color": "#64748b", // slate-500
    } as unknown as cytoscape.Css.Node,
  },

  // High-richness nodes get a brighter border
  {
    selector: "node[richness > 0.5]",
    style: {
      "border-style": "solid",
      "border-color": "#e2e8f0", // slate-200
    } as unknown as cytoscape.Css.Node,
  },

  // Selected node
  {
    selector: "node:selected",
    style: {
      "border-width": 4,
      "border-color": "#38bdf8", // sky-400
      "border-style": "solid",
      "background-opacity": 1,
      "z-index": 999,
    } as unknown as cytoscape.Css.Node,
  },

  // Hover
  {
    selector: "node:active",
    style: {
      "overlay-opacity": 0.08,
      "overlay-color": "#38bdf8",
    } as unknown as cytoscape.Css.Node,
  },

  // -------------------------------------------------------------------------
  // Edge styles
  // -------------------------------------------------------------------------
  {
    selector: "edge",
    style: {
      // Line — color driven by weight (fact count: lighter=fewer, stronger=more)
      "line-color": "data(edgeColor)",
      width: "data(edgeWidth)",
      "curve-style": "bezier",
      opacity: 0.5,

      // No arrows for undirected "related" edges
      "target-arrow-shape": "none",

      // Label: hidden by default, shown on hover via separate selector
      label: "",
      "font-size": "7px",
      color: "#94a3b8", // slate-400
      "text-rotation": "autorotate",
      "text-outline-color": "#0f172a",
      "text-outline-width": 1.5,

      // Interaction
      "overlay-opacity": 0,
      "transition-property": "line-color, width, opacity",
      "transition-duration": 200,
    } as unknown as cytoscape.Css.Edge,
  },

  // Parent edges: dashed, muted, with arrow pointing child → parent
  {
    selector: 'edge[edgeType = "parent"]',
    style: {
      "line-style": "dashed",
      "line-color": "#94a3b8", // slate-400
      width: 1,
      opacity: 0.35,
      "target-arrow-shape": "triangle",
      "target-arrow-color": "#94a3b8",
      "arrow-scale": 0.6,
      "curve-style": "bezier",
    } as unknown as cytoscape.Css.Edge,
  },

  // Contradicts edges (thesis ↔ antithesis): bold amber with double arrows
  {
    selector: 'edge[relationship_type = "contradicts"]',
    style: {
      "line-color": "#f59e0b", // amber-500
      "line-style": "solid",
      width: 3,
      opacity: 0.8,
      "target-arrow-shape": "diamond",
      "target-arrow-color": "#f59e0b",
      "source-arrow-shape": "diamond",
      "source-arrow-color": "#f59e0b",
      "arrow-scale": 0.7,
      label: "contradicts",
      "font-size": "6px",
      color: "#f59e0b",
      "text-rotation": "autorotate",
      "text-outline-color": "#0f172a",
      "text-outline-width": 1.5,
    } as unknown as cytoscape.Css.Edge,
  },

  // Hovered / active edges show the weight
  {
    selector: "edge:active",
    style: {
      label: "data(relationship_type)",
      opacity: 1,
      width: 2,
    } as unknown as cytoscape.Css.Edge,
  },

  // Edges connected to a selected node are highlighted
  {
    selector: "edge:selected",
    style: {
      label: "data(relationship_type)",
      opacity: 1,
      "z-index": 998,
    } as unknown as cytoscape.Css.Edge,
  },

  // Pinned (locked) nodes get an orange dashed border
  {
    selector: "node.pinned",
    style: {
      "border-width": 3,
      "border-color": "#f97316", // orange-500
      "border-style": "dashed",
    } as unknown as cytoscape.Css.Node,
  },

  // Highlighted nodes (from graph search)
  {
    selector: "node.highlighted",
    style: {
      "border-width": 3,
      "border-color": "#3b82f6", // blue-500
      "border-style": "solid",
      "overlay-opacity": 0.1,
      "overlay-color": "#3b82f6",
    } as unknown as cytoscape.Css.Node,
  },

  // Focus + context dimming: faded elements when a node is selected
  {
    selector: "node.faded",
    style: {
      opacity: 0.15,
      "text-opacity": 0.3,
    } as unknown as cytoscape.Css.Node,
  },
  {
    selector: "edge.faded",
    style: {
      opacity: 0.08,
    } as unknown as cytoscape.Css.Edge,
  },

  // -------------------------------------------------------------------------
  // Path-finding highlight styles
  // -------------------------------------------------------------------------
  {
    selector: "node.path-node",
    style: {
      "border-color": "#22d3ee", // cyan-400
      "border-width": 4,
      "border-style": "solid",
      opacity: 1,
      "text-opacity": 1,
      "z-index": 900,
    } as unknown as cytoscape.Css.Node,
  },
  {
    selector: "node.path-source",
    style: {
      "border-color": "#22d3ee", // cyan-400
      "border-width": 5,
      "border-style": "double",
      opacity: 1,
      "text-opacity": 1,
      "z-index": 999,
    } as unknown as cytoscape.Css.Node,
  },
  {
    selector: "node.path-target",
    style: {
      "border-color": "#a78bfa", // violet-400
      "border-width": 5,
      "border-style": "double",
      opacity: 1,
      "text-opacity": 1,
      "z-index": 999,
    } as unknown as cytoscape.Css.Node,
  },
  {
    selector: "edge.path-edge",
    style: {
      "line-color": "#22d3ee", // cyan-400
      "target-arrow-color": "#22d3ee",
      width: 3,
      opacity: 1,
      label: "data(relationship_type)",
      "z-index": 900,
    } as unknown as cytoscape.Css.Edge,
  },
];

export default graphStylesheet;
