"use client";

import {
  useRef,
  useEffect,
  useMemo,
  useCallback,
  useState,
  type CSSProperties,
} from "react";
import dynamic from "next/dynamic";
import cytoscape from "cytoscape";
import fcose from "cytoscape-fcose";
import type { NodeResponse, EdgeResponse } from "@/types";
import { toCytoscapeElements, getLayoutOptions, type EdgeForceSettings, DEFAULT_MAX_NODE_SIZE } from "@/lib/graph-utils";
import graphStylesheet from "@/components/graph/GraphStylesheet";

// Register fcose layout extension (safe in client-only component)
cytoscape.use(fcose);

// Dynamically import react-cytoscapejs with SSR disabled.
// Cytoscape requires `window` and `document` which do not exist on the server.
const CytoscapeComponent = dynamic(() => import("react-cytoscapejs"), {
  ssr: false,
  loading: () => (
    <div className="flex h-full w-full items-center justify-center text-muted-foreground">
      Loading graph...
    </div>
  ),
});

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

export interface GraphViewProps {
  nodes: NodeResponse[];
  edges: EdgeResponse[];
  selectedNodeId: string | null;
  onNodeSelect: (nodeId: string | null) => void;
  /** Layout name key from the layouts map. Defaults to "fcose". */
  layoutName?: string;
  /** Called when a node is double-clicked (expand). */
  onNodeExpand?: (nodeId: string) => void;
  /**
   * Called when a node is clicked — navigate to it (select + expand neighbors).
   * When set, enables navigation mode: the selected node is pinned at the
   * viewport center during layout and single-click triggers navigation.
   * When absent, single-click only selects (standard query view behavior).
   */
  onNodeNavigate?: (nodeId: string) => void;
  /** Per-edge-type force settings for the fcose layout. */
  edgeForces?: EdgeForceSettings;
  /** Maximum node size in pixels. Defaults to 70. */
  maxNodeSize?: number;
  /** When set, nodes whose label includes this string get a "highlighted" class. */
  highlightQuery?: string;
  /** Called when the Cytoscape core instance is ready. */
  onCyReady?: (cy: cytoscape.Core) => void;
  /** Called when a node is Shift+clicked to hide it from the view. */
  onNodeHide?: (nodeId: string) => void;
  /** Edge types currently hidden by the legend filter. */
  hiddenEdgeTypes?: ReadonlySet<string>;
  /** Node IDs on the active compare path(s). */
  pathNodeIds?: ReadonlySet<string>;
  /** Edge IDs on the active compare path(s). */
  pathEdgeIds?: ReadonlySet<string>;
  /** Source node ID for compare mode highlighting. */
  compareSourceId?: string | null;
  /** Target node ID for compare mode highlighting. */
  compareTargetId?: string | null;
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

// Use a static preset layout for CytoscapeComponent so it never runs its own
// layout on element changes. All layout logic is handled manually in our
// useEffect to support pinning the selected node at viewport center.
const presetLayout = { name: "preset" } as cytoscape.LayoutOptions;

const containerStyle: CSSProperties = {
  width: "100%",
  height: "100%",
  minHeight: 300,
};

const tooltipStyle: CSSProperties = {
  position: "absolute",
  pointerEvents: "none",
  zIndex: 1000,
  padding: "4px 8px",
  borderRadius: 4,
  backgroundColor: "rgba(15, 23, 42, 0.92)",
  color: "#f1f5f9",
  fontSize: "12px",
  maxWidth: 300,
  whiteSpace: "pre-wrap",
  wordBreak: "break-word",
  border: "1px solid rgba(100, 116, 139, 0.5)",
  display: "none",
};

const edgeDetailStyle: CSSProperties = {
  position: "absolute",
  zIndex: 1001,
  padding: "12px 16px",
  borderRadius: 8,
  backgroundColor: "rgba(15, 23, 42, 0.95)",
  color: "#f1f5f9",
  fontSize: "13px",
  maxWidth: 340,
  whiteSpace: "pre-wrap",
  wordBreak: "break-word",
  border: "1px solid rgba(100, 116, 139, 0.6)",
  boxShadow: "0 4px 12px rgba(0, 0, 0, 0.4)",
  display: "none",
};

export default function GraphView({
  nodes,
  edges,
  selectedNodeId,
  onNodeSelect,
  layoutName = "fcose",
  onNodeExpand,
  onNodeNavigate,
  edgeForces,
  maxNodeSize = DEFAULT_MAX_NODE_SIZE,
  highlightQuery,
  onCyReady,
  onNodeHide,
  hiddenEdgeTypes,
  pathNodeIds,
  pathEdgeIds,
  compareSourceId,
  compareTargetId,
}: GraphViewProps) {
  const cyRef = useRef<cytoscape.Core | null>(null);
  const layoutRef = useRef<cytoscape.Layouts | null>(null);
  const tooltipRef = useRef<HTMLDivElement | null>(null);
  const edgeDetailRef = useRef<HTMLDivElement | null>(null);
  const mountedRef = useRef(true);
  const [isReady, setIsReady] = useState(false);

  // Navigation mode is active when onNodeNavigate is provided
  const navigationMode = !!onNodeNavigate;

  // Store selectedNodeId in a ref so the layout effect can read it without
  // re-triggering on every selection change (which would re-run layout in
  // the query view on every click).
  const selectedNodeIdRef = useRef(selectedNodeId);
  useEffect(() => {
    selectedNodeIdRef.current = selectedNodeId;
  }, [selectedNodeId]);

  // Track mounted state so we never touch a destroyed cy instance
  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);

  // Store callbacks in refs so handleCy stays stable
  const onNodeSelectRef = useRef(onNodeSelect);
  const onNodeExpandRef = useRef(onNodeExpand);
  const onNodeNavigateRef = useRef(onNodeNavigate);
  const onCyReadyRef = useRef(onCyReady);
  const onNodeHideRef = useRef(onNodeHide);
  useEffect(() => {
    onNodeSelectRef.current = onNodeSelect;
    onNodeExpandRef.current = onNodeExpand;
    onNodeNavigateRef.current = onNodeNavigate;
    onCyReadyRef.current = onCyReady;
    onNodeHideRef.current = onNodeHide;
  }, [onNodeSelect, onNodeExpand, onNodeNavigate, onCyReady, onNodeHide]);

  // Convert our data model to Cytoscape elements
  const elements = useMemo(
    () => toCytoscapeElements(nodes, edges, maxNodeSize, hiddenEdgeTypes),
    [nodes, edges, maxNodeSize, hiddenEdgeTypes],
  );

  // Resolve layout options (adaptive to node count)
  const layoutOptions = useMemo(
    () => getLayoutOptions(layoutName, nodes.length, edgeForces),
    [layoutName, nodes.length, edgeForces],
  );

  // Stop any running layout safely
  const stopLayout = useCallback(() => {
    if (layoutRef.current) {
      try {
        layoutRef.current.stop();
      } catch {
        // Layout already stopped or destroyed — safe to ignore
      }
      layoutRef.current = null;
    }
  }, []);

  // Capture the Cytoscape core instance (stable ref — no deps that change).
  // react-cytoscapejs may call this multiple times when elements change, so
  // we guard against re-registering listeners on the same instance.
  const registeredCyRef = useRef<cytoscape.Core | null>(null);

  const handleCy = useCallback(
    (cy: cytoscape.Core) => {
      cyRef.current = cy;

      // Only wire up listeners once per cy instance
      if (registeredCyRef.current === cy) {
        if (!isReady) setIsReady(true);
        return;
      }
      registeredCyRef.current = cy;
      setIsReady(true);
      onCyReadyRef.current?.(cy);

      // Node tap → navigate or select depending on mode
      cy.on("tap", "node", (evt) => {
        const nodeId = evt.target.id();
        const originalEvent = evt.originalEvent as MouseEvent | undefined;

        // Shift+click → hide node
        if (originalEvent?.shiftKey && onNodeHideRef.current) {
          onNodeHideRef.current(nodeId);
          return;
        }

        if (onNodeNavigateRef.current) {
          // Navigation mode: the layout effect handles centering via pin + RAF.
          // Do NOT call cy.animate here — it would fight with the RAF loop.
          onNodeNavigateRef.current(nodeId);
        } else {
          // Standard mode: just select
          onNodeSelectRef.current(nodeId);
        }
      });

      // Background tap -> deselect
      cy.on("tap", (evt) => {
        if (evt.target === cy) {
          onNodeSelectRef.current(null);
        }
      });

      // Double-click to expand
      cy.on("dbltap", "node", (evt) => {
        onNodeExpandRef.current?.(evt.target.id());
      });

      // Right-click to pin/unpin
      cy.on("cxttap", "node", (evt) => {
        const node = evt.target;
        if (node.locked()) {
          node.unlock();
          node.removeClass("pinned");
        } else {
          node.lock();
          node.addClass("pinned");
        }
      });

      // Tooltip on hover for nodes and edges
      cy.on("mouseover", "node, edge", (evt) => {
        const tip = tooltipRef.current;
        if (!tip) return;
        const el = evt.target;
        if (el.isNode()) {
          const label = (el.data("label") as string) ?? "";
          if (!label) return;
          tip.textContent = onNodeHideRef.current
            ? `${label}\nShift+click to hide`
            : label;
        } else {
          const relType = (el.data("relationship_type") as string) ?? "";
          const justification = (el.data("justification") as string) ?? "";
          const label = relType.replace(/_/g, " ");
          if (justification) {
            const preview = justification.length > 120
              ? justification.slice(0, 120) + "..."
              : justification;
            tip.textContent = `${label}\n${preview}`;
          } else {
            tip.textContent = label;
          }
        }
        tip.style.display = "block";
      });

      cy.on("mousemove", "node, edge", (evt) => {
        const tip = tooltipRef.current;
        if (!tip || tip.style.display === "none") return;
        const pos = evt.renderedPosition ?? evt.position;
        tip.style.left = `${pos.x + 12}px`;
        tip.style.top = `${pos.y + 12}px`;
      });

      cy.on("mouseout", "node, edge", () => {
        const tip = tooltipRef.current;
        if (tip) tip.style.display = "none";
      });

      // Edge click → show detail card
      cy.on("tap", "edge", (evt) => {
        const detail = edgeDetailRef.current;
        if (!detail) return;
        const el = evt.target;
        const relType = (el.data("relationship_type") as string) ?? "";
        const justification = (el.data("justification") as string | null) ?? null;
        const factIds = (el.data("supporting_fact_ids") as string[]) ?? [];
        const sourceNode = cy.getElementById(el.data("source") as string);
        const targetNode = cy.getElementById(el.data("target") as string);
        const sourceName = sourceNode.length > 0
          ? (sourceNode.data("label") as string) ?? "?"
          : "?";
        const targetName = targetNode.length > 0
          ? (targetNode.data("label") as string) ?? "?"
          : "?";

        // Build detail content as HTML so fact tokens become clickable links
        const esc = (s: string) =>
          s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");

        const lines: string[] = [];
        lines.push(`<strong>${esc(relType.replace(/_/g, " ").toUpperCase())}</strong>`);
        lines.push("");
        lines.push(`${esc(sourceName)}  \u2192  ${esc(targetName)}`);
        lines.push(`Facts: ${factIds.length}`);
        if (justification) {
          lines.push("");
          // Replace {fact:uuid} tokens with clickable citation links
          let citationIdx = 0;
          const linked = esc(justification).replace(
            /\{fact:([0-9a-f-]{36})\}/gi,
            (_m, uuid: string) => {
              citationIdx++;
              return `<a href="/facts/${uuid}" target="_blank" rel="noopener noreferrer" `
                + `style="display:inline-flex;align-items:center;justify-content:center;`
                + `min-width:1.25rem;height:1rem;padding:0 4px;font-size:10px;font-weight:500;`
                + `border-radius:4px;background:rgba(59,130,246,0.2);color:#93c5fd;`
                + `text-decoration:none;vertical-align:super;line-height:1;cursor:pointer;">`
                + `${citationIdx}</a>`;
            },
          );
          lines.push(linked);
        }

        detail.innerHTML = lines.join("\n");
        detail.style.display = "block";

        // Position near the click
        const pos = evt.renderedPosition ?? evt.position;
        detail.style.left = `${pos.x + 16}px`;
        detail.style.top = `${pos.y + 16}px`;
      });

      // Dismiss edge detail on background or node tap
      cy.on("tap", (evt) => {
        if (evt.target === cy || evt.target.isNode?.()) {
          const detail = edgeDetailRef.current;
          if (detail) detail.style.display = "none";
        }
      });
    },
    [isReady],
  );

  // Clean up layout on unmount
  useEffect(() => {
    return () => {
      stopLayout();
      registeredCyRef.current = null;
      cyRef.current = null;
    };
  }, [stopLayout]);

  // Track whether compare mode is active (path highlighting owns fading)
  const compareModeActive = pathNodeIds != null && pathNodeIds.size > 0;

  // Sync selection state with Cytoscape + focus/context dimming
  useEffect(() => {
    const cy = cyRef.current;
    if (!cy || !isReady) return;

    // Unselect everything first
    cy.elements().unselect();
    cy.elements().removeClass("faded");

    // Skip selection-based fading when compare mode owns the fading
    if (compareModeActive) return;

    if (selectedNodeId) {
      const node = cy.getElementById(selectedNodeId);
      if (node.length > 0) {
        node.select();
        // Also select connected edges for visual clarity
        node.connectedEdges().select();

        // Focus + context dimming: fade non-connected elements
        const neighborhood = node.neighborhood().add(node);
        cy.elements().not(neighborhood).addClass("faded");
        neighborhood.removeClass("faded");
      }
    }
  }, [selectedNodeId, isReady, compareModeActive]);

  // Path-finding highlight: apply path classes and fade non-path elements
  useEffect(() => {
    const cy = cyRef.current;
    if (!cy || !isReady) return;

    // Always clear path classes first
    cy.elements().removeClass("path-node path-source path-target path-edge");

    if (!pathNodeIds || pathNodeIds.size === 0) return;

    // Apply path-node class to matching nodes
    cy.nodes().forEach((node) => {
      const id = node.id();
      if (pathNodeIds.has(id)) {
        node.addClass("path-node");
        if (id === compareSourceId) node.addClass("path-source");
        if (id === compareTargetId) node.addClass("path-target");
      }
    });

    // Apply path-edge class to matching edges
    if (pathEdgeIds && pathEdgeIds.size > 0) {
      cy.edges().forEach((edge) => {
        if (pathEdgeIds.has(edge.id())) {
          edge.addClass("path-edge");
        }
      });
    }

    // Fade everything NOT on the path
    const pathElements = cy.elements(".path-node, .path-edge");
    cy.elements().not(pathElements).addClass("faded");
    pathElements.removeClass("faded");
  }, [pathNodeIds, pathEdgeIds, compareSourceId, compareTargetId, isReady, elements]);

  // Highlight nodes matching the search query
  useEffect(() => {
    const cy = cyRef.current;
    if (!cy || !isReady) return;

    cy.nodes().removeClass("highlighted");

    if (highlightQuery && highlightQuery.trim().length > 0) {
      const q = highlightQuery.toLowerCase();
      cy.nodes().forEach((node) => {
        const label = (node.data("label") as string) ?? "";
        if (label.toLowerCase().includes(q)) {
          node.addClass("highlighted");
        }
      });
    }
  }, [highlightQuery, isReady, elements]);

  // Re-run layout when elements change.
  // In navigation mode, pin the selected node at exact screen center and
  // continuously enforce centering throughout the layout animation.
  useEffect(() => {
    const cy = cyRef.current;
    if (!cy || !isReady || elements.length === 0) return;

    // Small delay to let Cytoscape render the new elements before running layout
    let rafId = 0;
    const timer = setTimeout(() => {
      if (!mountedRef.current || !cyRef.current) return;
      stopLayout();
      try {
        let pinnedNode: cytoscape.NodeSingular | null = null;
        let wasPreviouslyLocked = false;
        let opts = layoutOptions;

        // In navigation mode, pin the selected node at screen center
        const currentSelectedId = selectedNodeIdRef.current;
        if (navigationMode && currentSelectedId) {
          const node = cyRef.current.getElementById(currentSelectedId);
          if (node.length > 0 && node.isNode()) {
            pinnedNode = node as cytoscape.NodeSingular;
            wasPreviouslyLocked = pinnedNode.locked();
            // Convert pixel center of viewport to model coordinates
            const pan = cyRef.current.pan();
            const zoom = cyRef.current.zoom();
            const modelCx = (cyRef.current.width() / 2 - pan.x) / zoom;
            const modelCy = (cyRef.current.height() / 2 - pan.y) / zoom;
            pinnedNode.position({ x: modelCx, y: modelCy });
            pinnedNode.lock();
            // Disable fit so the layout doesn't shift the viewport
            opts = { ...layoutOptions, fit: false } as cytoscape.LayoutOptions;
          }
        }

        const layout = cyRef.current.layout(opts);
        layoutRef.current = layout;

        // In navigation mode, continuously enforce viewport centering on
        // the pinned node for the entire duration of the layout animation.
        let tracking = !!pinnedNode;
        if (pinnedNode) {
          const trackNode = pinnedNode;
          const trackCy = cyRef.current;
          const keepCentered = () => {
            if (!tracking || !mountedRef.current) return;
            const pos = trackNode.position();
            const zoom = trackCy.zoom();
            const targetPanX = trackCy.width() / 2 - pos.x * zoom;
            const targetPanY = trackCy.height() / 2 - pos.y * zoom;
            trackCy.pan({ x: targetPanX, y: targetPanY });
            rafId = requestAnimationFrame(keepCentered);
          };
          rafId = requestAnimationFrame(keepCentered);
        }

        layout.on("layoutstop", () => {
          tracking = false;
          cancelAnimationFrame(rafId);
          if (pinnedNode) {
            // One final center enforcement after layout settles
            if (mountedRef.current && cyRef.current) {
              const pos = pinnedNode.position();
              const zoom = cyRef.current.zoom();
              cyRef.current.pan({
                x: cyRef.current.width() / 2 - pos.x * zoom,
                y: cyRef.current.height() / 2 - pos.y * zoom,
              });
            }
            if (!wasPreviouslyLocked) {
              pinnedNode.unlock();
            }
          }
        });

        layout.run();
      } catch {
        // cy may have been destroyed between the timeout and now — safe to ignore
      }
    }, 50);

    return () => {
      clearTimeout(timer);
      cancelAnimationFrame(rafId);
      stopLayout();
    };
  }, [elements, layoutOptions, isReady, stopLayout, navigationMode]);

  if (elements.length === 0) {
    return (
      <div className="flex h-full w-full items-center justify-center text-sm text-muted-foreground">
        No nodes to display. Submit a query to start building the knowledge
        graph.
      </div>
    );
  }

  return (
    <div style={{ position: "relative", width: "100%", height: "100%" }}>
      <CytoscapeComponent
        elements={elements}
        stylesheet={graphStylesheet}
        layout={presetLayout}
        style={containerStyle}
        cy={handleCy}
        minZoom={0.1}
        maxZoom={4}
        wheelSensitivity={0.3}
        boxSelectionEnabled={false}
      />
      <div ref={tooltipRef} style={tooltipStyle} />
      <div ref={edgeDetailRef} style={edgeDetailStyle} />
    </div>
  );
}
