"use client";

import { useState, useCallback, useMemo, useRef, useEffect, type MutableRefObject } from "react";
import dynamic from "next/dynamic";
import type cytoscape from "cytoscape";
import { Search, Loader2, Trash2, X, GitCompareArrows } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import GraphControls from "@/components/graph/GraphControls";
import GraphLegend from "@/components/graph/GraphLegend";
import { CompareOverlay } from "@/components/graph/CompareOverlay";
import NodeDetailPanel from "@/components/node/NodeDetailPanel";
import { useGraphExplorer } from "@/hooks/useGraphExplorer";
import { useCompareMode } from "@/hooks/useCompareMode";
import { getLayoutOptions, defaultEdgeForces, DEFAULT_MAX_NODE_SIZE, filterGraphByVisibility, NODE_TYPE_ENTRIES, EDGE_TYPE_ENTRIES, type EdgeForceSettings } from "@/lib/graph-utils";
import { api } from "@/lib/api";
import type { NodeResponse } from "@/types";

const GraphView = dynamic(() => import("@/components/graph/GraphView"), {
  ssr: false,
});

interface SearchResult {
  nodes: NodeResponse[];
  visible: boolean;
}

interface GraphExplorerProps {
  /** Seed node IDs to load on mount (from URL params). */
  initialSeedIds?: string[];
  /** Called whenever seed IDs change so the parent can sync URL params. */
  onSeedsChange?: (seedIds: string[]) => void;
  /** Initial compare target node ID (from URL param). */
  initialCompareTargetId?: string;
  /** Called when compare target changes (for URL sync). */
  onCompareChange?: (targetId: string | null) => void;
}

export default function GraphExplorer({ initialSeedIds, onSeedsChange, initialCompareTargetId, onCompareChange }: GraphExplorerProps) {
  const {
    nodes,
    edges,
    seedIds,
    selectedNodeId,
    neighborDepth,
    isLoading,
    error,
    searchAndAdd,
    expandNode,
    navigateToNode,
    setNeighborDepth,
    selectNode,
    clearView,
    ensureNodesInView,
  } = useGraphExplorer(initialSeedIds);

  const compareMode = useCompareMode(edges);

  const [searchQuery, setSearchQuery] = useState("");
  const [searchResults, setSearchResults] = useState<SearchResult>({
    nodes: [],
    visible: false,
  });
  const [isSearching, setIsSearching] = useState(false);
  const [layoutName, setLayoutName] = useState("fcose");
  const [graphSearchQuery, setGraphSearchQuery] = useState("");
  const [edgeForces, setEdgeForces] = useState<EdgeForceSettings>({ ...defaultEdgeForces });
  const [maxNodeSize, setMaxNodeSize] = useState(DEFAULT_MAX_NODE_SIZE);
  const [hiddenNodeTypes, setHiddenNodeTypes] = useState<Set<string>>(new Set());
  const [hiddenEdgeTypes, setHiddenEdgeTypes] = useState<Set<string>>(new Set());
  const [hideRootNodes, setHideRootNodes] = useState(false);
  const searchRef = useRef<HTMLDivElement>(null);
  const cyRef = useRef<cytoscape.Core | null>(null) as MutableRefObject<cytoscape.Core | null>;

  // Compute filtered nodes/edges based on hidden types
  const { nodes: filteredNodes, edges: filteredEdges } = useMemo(
    () => filterGraphByVisibility(nodes, edges, hiddenNodeTypes, hiddenEdgeTypes, hideRootNodes),
    [nodes, edges, hiddenNodeTypes, hiddenEdgeTypes, hideRootNodes],
  );

  // Clear selection if the selected node's type becomes hidden
  useEffect(() => {
    if (!selectedNodeId) return;
    const selectedNode = nodes.find((n) => n.id === selectedNodeId);
    if (selectedNode && hiddenNodeTypes.has(selectedNode.node_type ?? "concept")) {
      selectNode(null);
    }
  }, [hiddenNodeTypes, selectedNodeId, nodes, selectNode]);

  // Filter toggle handlers
  const handleToggleNodeType = useCallback((key: string) => {
    setHiddenNodeTypes((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  }, []);

  const handleToggleEdgeType = useCallback((key: string) => {
    setHiddenEdgeTypes((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  }, []);

  const handleShowAllNodeTypes = useCallback(() => setHiddenNodeTypes(new Set()), []);
  const handleHideAllNodeTypes = useCallback(
    () => setHiddenNodeTypes(new Set(NODE_TYPE_ENTRIES.map((e) => e.key))),
    [],
  );
  const handleShowAllEdgeTypes = useCallback(() => setHiddenEdgeTypes(new Set()), []);
  const handleHideAllEdgeTypes = useCallback(
    () => setHiddenEdgeTypes(new Set(EDGE_TYPE_ENTRIES.map((e) => e.key))),
    [],
  );

  const handleCyReady = useCallback((cy: cytoscape.Core) => {
    cyRef.current = cy;
  }, []);

  const handleZoomIn = useCallback(() => {
    const cy = cyRef.current;
    if (!cy) return;
    cy.zoom({
      level: cy.zoom() * 1.3,
      renderedPosition: { x: cy.width() / 2, y: cy.height() / 2 },
    });
  }, []);

  const handleZoomOut = useCallback(() => {
    const cy = cyRef.current;
    if (!cy) return;
    cy.zoom({
      level: cy.zoom() / 1.3,
      renderedPosition: { x: cy.width() / 2, y: cy.height() / 2 },
    });
  }, []);

  const handleFitGraph = useCallback(() => {
    const cy = cyRef.current;
    if (!cy) return;
    cy.fit(undefined, 40);
    cy.center();
  }, []);

  const handleReorganize = useCallback(() => {
    const cy = cyRef.current;
    if (!cy || cy.nodes().length === 0) return;
    const opts = getLayoutOptions(layoutName, cy.nodes().length, edgeForces);
    cy.layout(opts).run();
  }, [layoutName, edgeForces]);

  // Notify parent when seed IDs change (for URL sync)
  const prevSeedsRef = useRef<string>("");
  useEffect(() => {
    const serialized = [...seedIds].sort().join(",");
    if (serialized !== prevSeedsRef.current) {
      prevSeedsRef.current = serialized;
      onSeedsChange?.([...seedIds]);
    }
  }, [seedIds, onSeedsChange]);

  const handleSearch = useCallback(async () => {
    if (!searchQuery.trim()) return;
    setIsSearching(true);
    try {
      const results = await api.nodes.search(searchQuery.trim());
      setSearchResults({ nodes: results, visible: true });
    } catch {
      setSearchResults({ nodes: [], visible: true });
    } finally {
      setIsSearching(false);
    }
  }, [searchQuery]);

  const handleAddResult = useCallback(
    async (node: NodeResponse) => {
      setSearchResults({ nodes: [], visible: false });
      setSearchQuery("");
      await searchAndAdd(node.concept);
    },
    [searchAndAdd],
  );

  const handleSearchKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter") handleSearch();
    if (e.key === "Escape")
      setSearchResults((prev) => ({ ...prev, visible: false }));
  };

  const handleNodeExpand = useCallback(
    (nodeId: string) => {
      expandNode(nodeId);
    },
    [expandNode],
  );

  const handleNodeNavigate = useCallback(
    (nodeId: string) => {
      // In compare mode: select node (opens detail panel) but don't navigate
      if (compareMode.isCompareActive) {
        selectNode(nodeId);
        return;
      }
      navigateToNode(nodeId);
    },
    [navigateToNode, selectNode, compareMode],
  );

  // Exit compare mode and navigate back to source node
  const handleExitCompare = useCallback(() => {
    const sourceId = compareMode.sourceNodeId;
    compareMode.exitCompare();
    if (sourceId) {
      navigateToNode(sourceId);
    }
  }, [compareMode, navigateToNode]);

  // Start compare mode for the selected node
  const handleStartCompare = useCallback(() => {
    if (selectedNodeId) {
      compareMode.startCompare(selectedNodeId);
    }
  }, [selectedNodeId, compareMode]);

  // When paths load, ensure all path nodes + edges are in the graph view
  useEffect(() => {
    if (!compareMode.pathsData) return;
    const allNodeIds: string[] = [];
    const pathEdges: import("@/types").EdgeResponse[] = [];
    for (const path of compareMode.pathsData.paths) {
      for (const step of path.steps) {
        allNodeIds.push(step.node_id);
        if (step.edge) {
          pathEdges.push(step.edge);
        }
      }
    }
    if (allNodeIds.length > 0) {
      ensureNodesInView(allNodeIds, pathEdges);
    }
  }, [compareMode.pathsData, ensureNodesInView]);

  // Notify parent of compare target changes (for URL sync)
  const prevCompareRef = useRef<string | null>(null);
  useEffect(() => {
    const targetId = compareMode.targetNodeId;
    if (targetId !== prevCompareRef.current) {
      prevCompareRef.current = targetId;
      onCompareChange?.(targetId);
    }
  }, [compareMode.targetNodeId, onCompareChange]);

  // Load initial compare target if provided via URL
  const initialCompareLoadedRef = useRef(false);
  useEffect(() => {
    if (initialCompareLoadedRef.current) return;
    if (!initialCompareTargetId || !selectedNodeId) return;
    initialCompareLoadedRef.current = true;
    compareMode.startCompare(selectedNodeId);
    compareMode.selectTarget(initialCompareTargetId);
  }, [initialCompareTargetId, selectedNodeId, compareMode]);

  // Escape key exits compare mode
  useEffect(() => {
    if (!compareMode.isCompareActive) return;
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        handleExitCompare();
      }
    };
    document.addEventListener("keydown", handleKeyDown);
    return () => document.removeEventListener("keydown", handleKeyDown);
  }, [compareMode.isCompareActive, handleExitCompare]);

  // Look up node concepts for the compare bar
  const sourceNodeConcept = useMemo(() => {
    if (!compareMode.sourceNodeId) return "";
    const node = nodes.find((n) => n.id === compareMode.sourceNodeId);
    return node?.concept ?? "Unknown";
  }, [compareMode.sourceNodeId, nodes]);

  const targetNodeConcept = useMemo(() => {
    if (!compareMode.targetNodeId) return null;
    const node = nodes.find((n) => n.id === compareMode.targetNodeId);
    return node?.concept ?? "Unknown";
  }, [compareMode.targetNodeId, nodes]);

  // Empty state
  if (nodes.length === 0 && !isLoading) {
    return (
      <div className="flex flex-col items-center justify-center h-full gap-6">
        <div className="flex flex-col items-center gap-2 text-center">
          <Search className="size-12 text-muted-foreground/50" />
          <h3 className="text-lg font-medium">Search for a concept to start exploring</h3>
          <p className="text-sm text-muted-foreground max-w-md">
            Search for nodes by concept name. Add them to the graph, then click any node to navigate — its neighbors load automatically.
          </p>
        </div>
        <div className="flex items-center gap-2 w-full max-w-md">
          <div className="relative flex-1">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 size-4 text-muted-foreground" />
            <Input
              placeholder="Search concepts..."
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              onKeyDown={handleSearchKeyDown}
              className="pl-9"
            />
          </div>
          <Button onClick={handleSearch} disabled={isSearching}>
            {isSearching ? (
              <Loader2 className="size-4 animate-spin" />
            ) : (
              "Search"
            )}
          </Button>
        </div>
        {/* Search results dropdown */}
        {searchResults.visible && (
          <div className="w-full max-w-md border rounded-lg bg-card shadow-lg max-h-60 overflow-auto">
            {searchResults.nodes.length === 0 ? (
              <p className="p-3 text-sm text-muted-foreground text-center">
                No nodes found
              </p>
            ) : (
              searchResults.nodes.map((node) => (
                <button
                  key={node.id}
                  className="w-full text-left px-3 py-2 hover:bg-accent/50 text-sm border-b last:border-b-0 transition-colors"
                  onClick={() => handleAddResult(node)}
                >
                  <span className="font-medium">{node.concept}</span>
                  {node.attractor && (
                    <span className="text-muted-foreground ml-2">
                      ({node.attractor})
                    </span>
                  )}
                </button>
              ))
            )}
          </div>
        )}
        {error && <p className="text-sm text-destructive">{error}</p>}
      </div>
    );
  }

  return (
    <div className="relative h-full">
      {/* Graph canvas */}
      <GraphView
        nodes={filteredNodes}
        edges={filteredEdges}
        selectedNodeId={selectedNodeId}
        onNodeSelect={selectNode}
        layoutName={layoutName}
        onNodeExpand={handleNodeExpand}
        onNodeNavigate={handleNodeNavigate}
        edgeForces={edgeForces}
        maxNodeSize={maxNodeSize}
        hiddenEdgeTypes={hiddenEdgeTypes}
        highlightQuery={graphSearchQuery}
        onCyReady={handleCyReady}
        pathNodeIds={compareMode.pathNodeIds}
        pathEdgeIds={compareMode.pathEdgeIds}
        compareSourceId={compareMode.sourceNodeId}
        compareTargetId={compareMode.targetNodeId}
      />

      {/* Search overlay */}
      <div ref={searchRef} className="absolute top-3 left-3 z-10 w-72">
        <div className="flex items-center gap-1">
          <div className="relative flex-1">
            <Search className="absolute left-2 top-1/2 -translate-y-1/2 size-3.5 text-muted-foreground" />
            <Input
              placeholder="Search and add nodes..."
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              onKeyDown={handleSearchKeyDown}
              className="pl-7 h-8 text-xs bg-card/90 backdrop-blur-sm"
            />
          </div>
          <Button
            variant="outline"
            size="icon-sm"
            onClick={handleSearch}
            disabled={isSearching}
            className="bg-card/90 backdrop-blur-sm"
          >
            {isSearching ? (
              <Loader2 className="size-3.5 animate-spin" />
            ) : (
              <Search className="size-3.5" />
            )}
          </Button>
          {selectedNodeId && !compareMode.isCompareActive && (
            <Button
              variant="outline"
              size="icon-sm"
              onClick={handleStartCompare}
              className="bg-card/90 backdrop-blur-sm"
              title="Compare paths"
            >
              <GitCompareArrows className="size-3.5" />
            </Button>
          )}
        </div>

        {/* Search results dropdown */}
        {searchResults.visible && (
          <div className="mt-1 border rounded-lg bg-card shadow-lg max-h-48 overflow-auto">
            {searchResults.nodes.length === 0 ? (
              <p className="p-2 text-xs text-muted-foreground text-center">
                No nodes found
              </p>
            ) : (
              searchResults.nodes.map((node) => (
                <button
                  key={node.id}
                  className="w-full text-left px-3 py-1.5 hover:bg-accent/50 text-xs border-b last:border-b-0 transition-colors"
                  onClick={() => handleAddResult(node)}
                >
                  <span className="font-medium">{node.concept}</span>
                </button>
              ))
            )}
          </div>
        )}
      </div>

      {/* Clear button */}
      <div className="absolute top-3 right-3 z-10">
        <Button
          variant="outline"
          size="sm"
          onClick={clearView}
          className="bg-card/90 backdrop-blur-sm gap-1"
        >
          <Trash2 className="size-3.5" />
          Clear
        </Button>
      </div>

      {/* Compare overlay */}
      {compareMode.isCompareActive && (
        <CompareOverlay
          sourceNodeId={compareMode.sourceNodeId!}
          sourceNodeConcept={sourceNodeConcept}
          targetNodeId={compareMode.targetNodeId}
          targetNodeConcept={targetNodeConcept}
          pathsData={compareMode.pathsData}
          isLoadingPaths={compareMode.isLoadingPaths}
          pathError={compareMode.pathError}
          activePathIndex={compareMode.activePathIndex}
          onSelectTarget={compareMode.selectTarget}
          onSetActivePathIndex={compareMode.setActivePathIndex}
          onClose={handleExitCompare}
        />
      )}

      {/* Loading indicator */}
      {isLoading && (
        <div className="absolute top-3 left-1/2 -translate-x-1/2 z-10">
          <div className="flex items-center gap-2 bg-card/90 backdrop-blur-sm rounded-lg border px-3 py-1.5">
            <Loader2 className="size-3.5 animate-spin" />
            <span className="text-xs">Loading...</span>
          </div>
        </div>
      )}

      {/* Error */}
      {error && (
        <div className="absolute top-3 left-1/2 -translate-x-1/2 z-10">
          <div className="flex items-center gap-2 bg-destructive/10 border-destructive/50 border rounded-lg px-3 py-1.5">
            <span className="text-xs text-destructive">{error}</span>
            <button onClick={() => {}} className="text-destructive">
              <X className="size-3" />
            </button>
          </div>
        </div>
      )}

      {/* Controls */}
      <div className="absolute bottom-3 left-3 z-10">
        <GraphControls
          onLayoutChange={setLayoutName}
          onZoomIn={handleZoomIn}
          onZoomOut={handleZoomOut}
          onFitGraph={handleFitGraph}
          onReorganize={handleReorganize}
          activeLayout={layoutName}
          onSearchGraph={setGraphSearchQuery}
          neighborDepth={neighborDepth}
          onNeighborDepthChange={setNeighborDepth}
          edgeForces={edgeForces}
          onEdgeForcesChange={setEdgeForces}
          maxNodeSize={maxNodeSize}
          onMaxNodeSizeChange={setMaxNodeSize}
        />
      </div>

      <div className="absolute bottom-3 right-3 z-10">
        <GraphLegend
          hiddenNodeTypes={hiddenNodeTypes}
          hiddenEdgeTypes={hiddenEdgeTypes}
          hideRootNodes={hideRootNodes}
          onToggleNodeType={handleToggleNodeType}
          onToggleEdgeType={handleToggleEdgeType}
          onShowAllNodeTypes={handleShowAllNodeTypes}
          onHideAllNodeTypes={handleHideAllNodeTypes}
          onShowAllEdgeTypes={handleShowAllEdgeTypes}
          onHideAllEdgeTypes={handleHideAllEdgeTypes}
          onToggleRootNodes={() => setHideRootNodes((prev) => !prev)}
        />
      </div>

      {/* Node detail panel */}
      <NodeDetailPanel
        nodeId={selectedNodeId}
        onClose={() => selectNode(null)}
        onNodeSelect={handleNodeNavigate}
      />
    </div>
  );
}
