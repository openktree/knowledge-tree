"use client";

import { useNodeDetail } from "@/hooks/useNodeDetail";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Separator } from "@/components/ui/separator";
import {
  Tabs,
  TabsList,
  TabsTrigger,
  TabsContent,
} from "@/components/ui/tabs";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { X, Loader2, RefreshCw, ArrowLeftRight, GitBranch, Sparkles, ChevronDown } from "lucide-react";
import { cn } from "@/lib/utils";
import { DimensionsTab } from "@/components/node/DimensionsTab";
import { FactsTab } from "@/components/node/FactsTab";
import { HistoryTab } from "@/components/node/HistoryTab";
import { NeighborsTab } from "@/components/node/NeighborsTab";
import { PerspectivesTab } from "@/components/node/PerspectivesTab";
import { PerspectiveSeedsTab } from "@/components/node/PerspectiveSeedsTab";
import { SeedAmbiguityBadge } from "@/components/node/SeedAmbiguityBadge";


export interface NodeDetailPanelProps {
  nodeId: string | null;
  onClose: () => void;
  onNodeSelect?: (nodeId: string) => void;
}

function formatDate(dateStr: string): string {
  return new Date(dateStr).toLocaleDateString("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export default function NodeDetailPanel({
  nodeId,
  onClose,
  onNodeSelect,
}: NodeDetailPanelProps) {
  const {
    node,
    dimensions,
    facts,
    edges,
    history,
    perspectives,
    isLoading,
    error,
    rebuildNode,
    isRebuilding,
    refreshPerspectives,
  } = useNodeDetail(nodeId);

  if (!nodeId) return null;

  return (
    <div
      className={cn(
        "fixed inset-0 md:left-auto w-full md:w-[32rem] bg-background border-l shadow-xl z-50 overflow-hidden",
        "flex flex-col",
        "animate-in slide-in-from-right duration-200"
      )}
    >
      <div className="flex items-center justify-between px-4 py-3 border-b">
        <h2 className="text-lg font-semibold truncate pr-2">
          {isLoading && !node ? "Loading..." : node?.concept ?? "Unknown Node"}
        </h2>
        <Button variant="ghost" size="icon" onClick={onClose}>
          <X className="h-4 w-4" />
        </Button>
      </div>

      {isLoading && !node && (
        <div className="flex-1 flex items-center justify-center">
          <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
        </div>
      )}

      {error && (
        <div className="px-4 py-3">
          <p className="text-sm text-destructive">{error}</p>
        </div>
      )}

      {node && (
        <ScrollArea className="flex-1 min-h-0">
          <div className="px-4 py-3 space-y-1">
              {/* ── Also known as ── */}
              {(() => {
                const aliases = (node.metadata?.aliases as string[] | undefined) ?? [];
                const mergedFrom = (node.metadata?.merged_from as string[] | undefined) ?? [];
                const allNames = [...new Set([...aliases, ...mergedFrom])];
                return allNames.length > 0 ? (
                  <p className="text-xs text-muted-foreground italic">
                    Also known as: {allNames.join(", ")}
                  </p>
                ) : null;
              })()}

              {/* ── Disambiguation notice ── */}
              {(() => {
                const amb = node.metadata?.seed_ambiguity as {
                  is_disambiguated?: boolean;
                  ambiguity_type?: string;
                  parent_name?: string;
                  sibling_names?: string[];
                } | undefined;
                if (!amb?.is_disambiguated) return null;
                return (
                  <div className="flex items-start gap-2 rounded-md border border-purple-200 bg-purple-50 px-3 py-2 text-xs dark:border-purple-800 dark:bg-purple-950">
                    <GitBranch className="h-3.5 w-3.5 text-purple-600 dark:text-purple-400 flex-shrink-0 mt-0.5" />
                    <div>
                      <span className="font-medium text-purple-800 dark:text-purple-200">
                        Disambiguated{amb.parent_name ? ` from "${amb.parent_name}"` : ""}
                      </span>
                      {amb.sibling_names && amb.sibling_names.length > 0 && (
                        <span className="text-purple-600 dark:text-purple-400">
                          {" "}— this term can also refer to: {amb.sibling_names.join(", ")}
                        </span>
                      )}
                    </div>
                  </div>
                );
              })()}

              <div className="flex flex-wrap items-center gap-2">
                {node.metadata?.dialectic_role === "thesis" && (
                  <Badge className="bg-blue-100 text-blue-800 dark:bg-blue-900 dark:text-blue-200">
                    Thesis
                  </Badge>
                )}
                {node.metadata?.dialectic_role === "antithesis" && (
                  <Badge className="bg-red-100 text-red-800 dark:bg-red-900 dark:text-red-200">
                    Antithesis
                  </Badge>
                )}
                <SeedAmbiguityBadge node={node} />
                <Badge variant="secondary">
                  Richness: {node.richness?.toFixed(2) ?? "N/A"}
                </Badge>
                <Badge variant="outline">
                  Accessed: {node.access_count ?? 0}
                </Badge>
                <Badge variant="outline">
                  Updates: {node.update_count ?? 0}
                </Badge>
                <DropdownMenu>
                  <DropdownMenuTrigger asChild>
                    <Button
                      variant="outline"
                      size="sm"
                      className="h-6 text-xs gap-1"
                      disabled={isRebuilding}
                    >
                      <RefreshCw className={cn("h-3 w-3", isRebuilding && "animate-spin")} />
                      Rebuild
                      <ChevronDown className="h-3 w-3" />
                    </Button>
                  </DropdownMenuTrigger>
                  <DropdownMenuContent align="start">
                    <DropdownMenuItem onClick={() => rebuildNode("incremental", "all")}>
                      Incremental Refresh
                    </DropdownMenuItem>
                    <DropdownMenuItem onClick={() => rebuildNode("full", "all")}>
                      Full Rebuild
                    </DropdownMenuItem>
                    <DropdownMenuItem onClick={() => rebuildNode("full", "dimensions")}>
                      Rebuild Dimensions
                    </DropdownMenuItem>
                    <DropdownMenuItem onClick={() => rebuildNode("full", "edges")}>
                      Rebuild Edges
                    </DropdownMenuItem>
                  </DropdownMenuContent>
                </DropdownMenu>
              </div>
              {!!node.metadata?.dialectic_pair_id && (
                <Button
                  variant="outline"
                  size="sm"
                  className="w-full h-7 text-xs gap-1.5 border-amber-300 text-amber-700 hover:bg-amber-50 dark:border-amber-700 dark:text-amber-300 dark:hover:bg-amber-950"
                  onClick={() =>
                    onNodeSelect?.(node.metadata!.dialectic_pair_id as string)
                  }
                >
                  <ArrowLeftRight className="h-3 w-3" />
                  View {String(node.metadata.dialectic_role) === "thesis" ? "Antithesis" : "Thesis"} →
                </Button>
              )}
              <div className="text-xs text-muted-foreground space-y-0.5 pt-1">
                {node.parent_id && node.parent_concept && (
                  <p>
                    Parent:{" "}
                    <button
                      className="text-blue-500 hover:underline cursor-pointer"
                      onClick={() => onNodeSelect?.(node.parent_id!)}
                    >
                      {node.parent_concept}
                    </button>
                  </p>
                )}
                {node.created_at && <p>Created: {formatDate(node.created_at)}</p>}
                {node.updated_at && <p>Updated: {formatDate(node.updated_at)}</p>}
              </div>
            </div>

            {node.definition && (
              <div className="mx-4 my-2 rounded-md border bg-muted/30 px-3 py-2">
                <p className="text-xs font-medium text-muted-foreground mb-1">Definition</p>
                <p className="text-sm leading-relaxed whitespace-pre-wrap">{node.definition}</p>
              </div>
            )}

            {(node.enrichment_status === "stub" || node.enrichment_status === "partial") && (
              <div className="mx-4 my-2 flex items-start gap-2 rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-xs dark:border-amber-800 dark:bg-amber-950">
                <Sparkles className="h-3.5 w-3.5 text-amber-600 dark:text-amber-400 flex-shrink-0 mt-0.5" />
                <div className="flex-1">
                  <span className="font-medium text-amber-800 dark:text-amber-200">
                    Stub node — dimensions not yet generated.
                  </span>
                  <p className="text-amber-600 dark:text-amber-400 mt-0.5">
                    This node was auto-promoted from a seed. Facts and edges are available.
                    {node.enrichment_status === "partial" && " Not enough facts yet for dimension generation."}
                  </p>
                  <Button
                    variant="outline"
                    size="sm"
                    className="mt-1.5 h-6 text-xs gap-1 border-amber-300 text-amber-700 hover:bg-amber-100 dark:border-amber-700 dark:text-amber-300"
                    onClick={() => rebuildNode("incremental", "all")}
                    disabled={isRebuilding}
                  >
                    <Sparkles className={cn("h-3 w-3", isRebuilding && "animate-spin")} />
                    {isRebuilding ? "Building..." : "Build Node"}
                  </Button>
                </div>
              </div>
            )}

            {isRebuilding && (
              <div className="mx-4 my-2 flex items-center gap-2 rounded-md border border-blue-200 bg-blue-50 px-3 py-2 text-xs text-blue-700 dark:border-blue-800 dark:bg-blue-950 dark:text-blue-300">
                <Loader2 className="h-3 w-3 animate-spin flex-shrink-0" />
                <span>
                  Rebuilding node — this may take a minute.
                  Results will appear automatically.
                </span>
              </div>
            )}

            <Separator />

            <Tabs defaultValue="dimensions" className="w-full">
              <div className="px-4 pt-2">
                <TabsList className="w-full">
                  <TabsTrigger value="dimensions" className="flex-1 text-xs px-1.5">
                    Dimensions
                  </TabsTrigger>
                  <TabsTrigger value="facts" className="flex-1 text-xs px-1.5">
                    Facts
                  </TabsTrigger>
                  <TabsTrigger value="history" className="flex-1 text-xs px-1.5">
                    History
                  </TabsTrigger>
                  <TabsTrigger value="neighbors" className="flex-1 text-xs px-1.5">
                    Neighbors
                  </TabsTrigger>
                  {node.node_type !== "perspective" && (
                    <TabsTrigger value="seeds" className="flex-1 text-xs px-1.5">
                      Seeds
                    </TabsTrigger>
                  )}
                  {node.node_type !== "perspective" && (
                    <TabsTrigger value="perspectives" className="flex-1 text-xs px-1.5">
                      Perspectives
                    </TabsTrigger>
                  )}
                </TabsList>
              </div>

              <div className="px-4 py-3">
                <TabsContent value="dimensions" className="mt-0">
                  <DimensionsTab
                    dimensions={dimensions}
                    onConceptClick={onNodeSelect}
                  />
                </TabsContent>
                <TabsContent value="facts" className="mt-0">
                  <FactsTab facts={facts} />
                </TabsContent>
                <TabsContent value="history" className="mt-0">
                  <HistoryTab history={history} />
                </TabsContent>
                <TabsContent value="neighbors" className="mt-0">
                  <NeighborsTab
                    edges={edges}
                    currentNodeId={nodeId}
                    onNodeSelect={onNodeSelect}
                  />
                </TabsContent>
                {node.node_type !== "perspective" && (
                  <TabsContent value="seeds" className="mt-0">
                    <PerspectiveSeedsTab
                      sourceNodeId={nodeId}
                      onSynthesized={refreshPerspectives}
                    />
                  </TabsContent>
                )}
                {node.node_type !== "perspective" && (
                  <TabsContent value="perspectives" className="mt-0">
                    <PerspectivesTab
                      pairs={perspectives}
                      parentNode={node}
                      onNodeSelect={onNodeSelect}
                      onCreated={refreshPerspectives}
                    />
                  </TabsContent>
                )}
              </div>
            </Tabs>
        </ScrollArea>
      )}

    </div>
  );
}
