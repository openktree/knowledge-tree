"use client";

import { use, useState, useMemo, useEffect, useRef, useCallback } from "react";
import dynamic from "next/dynamic";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useConversation } from "@/hooks/useConversation";
import { ChatPanel } from "@/components/chat/ChatPanel";
import GraphControls from "@/components/graph/GraphControls";
import GraphLegend from "@/components/graph/GraphLegend";
import NodeDetailPanel from "@/components/node/NodeDetailPanel";
import { Loader2, ArrowLeft, Pencil, Check, X, Download, Square, Trash2, RefreshCw } from "lucide-react";
import { Button } from "@/components/ui/button";
import { api } from "@/lib/api";
import { downloadJson } from "@/lib/download";
import { getLayoutOptions, defaultEdgeForces, DEFAULT_MAX_NODE_SIZE, filterGraphByVisibility, NODE_TYPE_ENTRIES, EDGE_TYPE_ENTRIES, type EdgeForceSettings } from "@/lib/graph-utils";
import type cytoscape from "cytoscape";
import type { NodeResponse, EdgeResponse } from "@/types";

const GraphView = dynamic(() => import("@/components/graph/GraphView"), {
  ssr: false,
});

interface ConversationPageProps {
  params: Promise<{ id: string }>;
}

export default function ConversationPage({ params }: ConversationPageProps) {
  const { id } = use(params);
  const router = useRouter();
  const {
    conversation,
    conversationMode,
    messages,
    activeTurnPhase,
    activeTurnAnswer,
    nodes: streamNodes,
    edges: streamEdges,
    isLoading,
    isTurnActive,
    isStoppingTurn,
    error,
    sendMessage,
    resynthesizeMessage,
    stopTurn,
    updateTitle,
    hideNode,
    refreshProgress,
  } = useConversation(id);

  const [isEditingTitle, setIsEditingTitle] = useState(false);
  const [editedTitle, setEditedTitle] = useState("");
  const titleInputRef = useRef<HTMLInputElement>(null);

  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);
  const [isExporting, setIsExporting] = useState(false);
  const [isDeleting, setIsDeleting] = useState(false);
  const [layoutName, setLayoutName] = useState("cose");
  const [graphSearchQuery, setGraphSearchQuery] = useState("");
  const [neighborDepth, setNeighborDepth] = useState(1);
  const [edgeForces, setEdgeForces] = useState<EdgeForceSettings>({ ...defaultEdgeForces });
  const [maxNodeSize, setMaxNodeSize] = useState(DEFAULT_MAX_NODE_SIZE);
  const [hiddenNodeTypes, setHiddenNodeTypes] = useState<Set<string>>(new Set());
  const [hiddenEdgeTypes, setHiddenEdgeTypes] = useState<Set<string>>(new Set());
  const [hideRootNodes, setHideRootNodes] = useState(false);
  const [isRefreshing, setIsRefreshing] = useState(false);

  const cyRef = useRef<cytoscape.Core | null>(null);

  const handleCyReady = useCallback((cy: cytoscape.Core) => {
    cyRef.current = cy;
  }, []);

  const handleZoomIn = useCallback(() => {
    const cy = cyRef.current;
    if (!cy) return;
    cy.zoom({ level: cy.zoom() * 1.3, renderedPosition: { x: cy.width() / 2, y: cy.height() / 2 } });
  }, []);

  const handleZoomOut = useCallback(() => {
    const cy = cyRef.current;
    if (!cy) return;
    cy.zoom({ level: cy.zoom() / 1.3, renderedPosition: { x: cy.width() / 2, y: cy.height() / 2 } });
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

  // Local graph state for depth-slider refetches
  const [depthNodes, setDepthNodes] = useState<NodeResponse[] | null>(null);
  const [depthEdges, setDepthEdges] = useState<EdgeResponse[] | null>(null);

  const nodes = depthNodes ?? streamNodes;
  const edges = depthEdges ?? streamEdges;

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
      setSelectedNodeId(null);
    }
  }, [hiddenNodeTypes, selectedNodeId, nodes]);

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

  // Track visited node IDs for depth queries
  const visitedNodeIds = useRef<string[]>([]);
  useEffect(() => {
    const ids = streamNodes.map((n) => n.id);
    if (ids.length > 0) {
      visitedNodeIds.current = ids;
    }
  }, [streamNodes]);

  const handleNeighborDepthChange = useCallback(
    async (depth: number) => {
      setNeighborDepth(depth);
      const ids = visitedNodeIds.current;
      if (ids.length === 0) return;
      try {
        const result = await api.graph.getSubgraph(ids, depth);
        setDepthNodes(result.nodes);
        setDepthEdges(result.edges);
      } catch {
        setDepthNodes(null);
        setDepthEdges(null);
      }
    },
    [],
  );

  const handleStartEditTitle = useCallback(() => {
    setEditedTitle(conversation?.title ?? "");
    setIsEditingTitle(true);
    setTimeout(() => titleInputRef.current?.focus(), 0);
  }, [conversation?.title]);

  const handleSaveTitle = useCallback(async () => {
    const trimmed = editedTitle.trim();
    if (trimmed && trimmed !== conversation?.title) {
      await updateTitle(trimmed);
    }
    setIsEditingTitle(false);
  }, [editedTitle, conversation?.title, updateTitle]);

  const handleCancelEditTitle = useCallback(() => {
    setIsEditingTitle(false);
  }, []);

  const handleExportConversation = useCallback(async () => {
    setIsExporting(true);
    try {
      const data = await api.export.conversation(id);
      const slug = (conversation?.title ?? "conversation")
        .toLowerCase()
        .replace(/[^a-z0-9]+/g, "-")
        .replace(/^-|-$/g, "")
        .slice(0, 50);
      const date = new Date().toISOString().slice(0, 10);
      downloadJson(data, `${slug}-${date}.json`);
    } finally {
      setIsExporting(false);
    }
  }, [id, conversation?.title]);

  const handleDeleteConversation = useCallback(async () => {
    if (!window.confirm("Delete this conversation? Knowledge graph data will be preserved.")) return;
    setIsDeleting(true);
    try {
      await api.conversations.delete(id);
      router.push("/");
    } catch {
      setIsDeleting(false);
    }
  }, [id, router]);

  const handleSendMessage = useCallback(
    (message: string, navBudget: number, exploreBudget: number, waveCount?: number) => {
      sendMessage(message, navBudget, exploreBudget, waveCount);
    },
    [sendMessage],
  );

  const handleRefresh = useCallback(async () => {
    setIsRefreshing(true);
    try {
      await refreshProgress();
    } finally {
      setIsRefreshing(false);
    }
  }, [refreshProgress]);

  if (isLoading) {
    return (
      <div className="h-full flex items-center justify-center">
        <div className="flex flex-col items-center gap-3">
          <Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
          <p className="text-sm text-muted-foreground">
            Loading conversation...
          </p>
        </div>
      </div>
    );
  }

  if (error && !conversation) {
    return (
      <div className="h-full flex items-center justify-center">
        <div className="flex flex-col items-center gap-3 max-w-md text-center">
          <p className="text-sm text-destructive">{error}</p>
          <Link
            href="/"
            className="text-sm text-muted-foreground underline hover:text-foreground"
          >
            Back to home
          </Link>
        </div>
      </div>
    );
  }

  return (
    <div className="h-full flex flex-col overflow-hidden">
      {/* Header */}
      <div className="group flex items-center gap-4 px-4 py-2 border-b bg-background shrink-0">
        <Link
          href={conversationMode === "query" ? "/" : "/research"}
          className="flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground transition-colors"
        >
          <ArrowLeft className="h-4 w-4" />
          <span>{conversationMode === "query" ? "Home" : "Research"}</span>
        </Link>

        <div className="flex-1 min-w-0 flex items-center gap-1">
          {isEditingTitle ? (
            <>
              <input
                ref={titleInputRef}
                type="text"
                value={editedTitle}
                onChange={(e) => setEditedTitle(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") handleSaveTitle();
                  if (e.key === "Escape") handleCancelEditTitle();
                }}
                className="text-sm font-medium bg-transparent border-b border-foreground/30 outline-none flex-1 min-w-0 py-0.5"
              />
              <button
                onClick={handleSaveTitle}
                className="p-0.5 text-muted-foreground hover:text-foreground"
                aria-label="Save title"
              >
                <Check className="h-3.5 w-3.5" />
              </button>
              <button
                onClick={handleCancelEditTitle}
                className="p-0.5 text-muted-foreground hover:text-foreground"
                aria-label="Cancel editing"
              >
                <X className="h-3.5 w-3.5" />
              </button>
            </>
          ) : (
            <>
              <p className="text-sm font-medium truncate">
                {conversation?.title ?? "Conversation"}
              </p>
              <button
                onClick={handleStartEditTitle}
                className="p-0.5 text-muted-foreground hover:text-foreground opacity-0 group-hover:opacity-100 transition-opacity"
                aria-label="Edit query"
              >
                <Pencil className="h-3.5 w-3.5" />
              </button>
            </>
          )}
        </div>

        <div className="flex items-center gap-3 shrink-0">
          {isTurnActive && (
            <>
              <Button
                variant="outline"
                size="sm"
                disabled={isRefreshing}
                onClick={handleRefresh}
              >
                <RefreshCw className={`size-4 ${isRefreshing ? "animate-spin" : ""}`} />
                <span className="ml-1">Refresh</span>
              </Button>
              <Button
                variant="destructive"
                size="sm"
                disabled={isStoppingTurn}
                onClick={stopTurn}
              >
                {isStoppingTurn ? (
                  <Loader2 className="size-4 animate-spin" />
                ) : (
                  <Square className="size-4" />
                )}
                <span className="ml-1">Stop</span>
              </Button>
            </>
          )}
          <Button
            variant="outline"
            size="sm"
            disabled={isExporting}
            onClick={handleExportConversation}
          >
            {isExporting ? (
              <Loader2 className="size-4 animate-spin" />
            ) : (
              <Download className="size-4" />
            )}
            <span className="ml-1">Export</span>
          </Button>
          <Button
            variant="outline"
            size="sm"
            disabled={isDeleting || isTurnActive}
            onClick={handleDeleteConversation}
          >
            {isDeleting ? (
              <Loader2 className="size-4 animate-spin" />
            ) : (
              <Trash2 className="size-4" />
            )}
            <span className="ml-1">Delete</span>
          </Button>
        </div>
      </div>

      {/* Main content */}
      <div className="flex-1 flex min-h-0 relative">
        {/* Graph panel (60%) */}
        <div className="w-[60%] relative border-r">
          <GraphView
            nodes={filteredNodes}
            edges={filteredEdges}
            selectedNodeId={selectedNodeId}
            onNodeSelect={setSelectedNodeId}
            layoutName={layoutName}
            onNodeExpand={setSelectedNodeId}
            edgeForces={edgeForces}
            maxNodeSize={maxNodeSize}
            hiddenEdgeTypes={hiddenEdgeTypes}
            highlightQuery={graphSearchQuery}
            onCyReady={handleCyReady}
            onNodeHide={hideNode}
          />

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
              onNeighborDepthChange={handleNeighborDepthChange}
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
        </div>

        {/* Chat panel (40%) */}
        <div className="w-[40%] flex flex-col min-h-0">
          <ChatPanel
            messages={messages}
            activeTurnAnswer={activeTurnAnswer}
            activeTurnPhase={activeTurnPhase}
            isTurnActive={isTurnActive}
            nodeCount={nodes.length}
            onSendMessage={handleSendMessage}
            onResynthesize={resynthesizeMessage}
            mode={conversationMode}
            conversationId={id}
          />
        </div>

        {/* Node detail panel */}
        <NodeDetailPanel
          nodeId={selectedNodeId}
          onClose={() => setSelectedNodeId(null)}
          onNodeSelect={setSelectedNodeId}
        />
      </div>

      {/* Error bar */}
      {error && conversation && (
        <div className="px-4 py-2 border-t bg-destructive/10 shrink-0">
          <p className="text-sm text-destructive">{error}</p>
        </div>
      )}
    </div>
  );
}
