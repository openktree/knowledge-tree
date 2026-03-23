"use client";

import { useState, useEffect, useCallback, useRef } from "react";
import type {
  NodeResponse,
  PathsResponse,
  PathStepResponse,
} from "@/types";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { ArrowRight, GitCompareArrows, Loader2, X } from "lucide-react";
import { cn } from "@/lib/utils";
import { api } from "@/lib/api";

interface CompareTabProps {
  currentNodeId: string;
  currentNodeConcept: string;
  onNodeSelect?: (nodeId: string) => void;
}

const relationshipColors: Record<string, string> = {
  related: "bg-gray-100 text-gray-800 dark:bg-gray-900 dark:text-gray-200",
};

export function CompareTab({
  currentNodeId,
  currentNodeConcept,
  onNodeSelect,
}: CompareTabProps) {
  const [searchQuery, setSearchQuery] = useState("");
  const [searchResults, setSearchResults] = useState<NodeResponse[]>([]);
  const [isSearching, setIsSearching] = useState(false);
  const [showDropdown, setShowDropdown] = useState(false);

  const [target, setTarget] = useState<{
    id: string;
    concept: string;
  } | null>(null);

  const [pathsData, setPathsData] = useState<PathsResponse | null>(null);
  const [isLoadingPaths, setIsLoadingPaths] = useState(false);
  const [pathError, setPathError] = useState<string | null>(null);

  const dropdownRef = useRef<HTMLDivElement>(null);
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Debounced search
  useEffect(() => {
    if (debounceRef.current) clearTimeout(debounceRef.current);

    if (!searchQuery.trim()) {
      setSearchResults([]);
      setShowDropdown(false);
      return;
    }

    debounceRef.current = setTimeout(async () => {
      setIsSearching(true);
      try {
        const result = await api.nodes.list({ search: searchQuery, limit: 10 });
        // Filter out the current node
        const filtered = result.items.filter((n) => n.id !== currentNodeId);
        setSearchResults(filtered);
        setShowDropdown(filtered.length > 0);
      } catch {
        setSearchResults([]);
      } finally {
        setIsSearching(false);
      }
    }, 300);

    return () => {
      if (debounceRef.current) clearTimeout(debounceRef.current);
    };
  }, [searchQuery, currentNodeId]);

  // Close dropdown on outside click
  useEffect(() => {
    function handleClick(e: MouseEvent) {
      if (
        dropdownRef.current &&
        !dropdownRef.current.contains(e.target as globalThis.Node)
      ) {
        setShowDropdown(false);
      }
    }
    document.addEventListener("mousedown", handleClick);
    return () => document.removeEventListener("mousedown", handleClick);
  }, []);

  // Fetch paths when target is selected
  const fetchPaths = useCallback(async () => {
    if (!target) return;
    setIsLoadingPaths(true);
    setPathError(null);
    try {
      const result = await api.graph.getPaths(currentNodeId, target.id);
      setPathsData(result);
    } catch (err) {
      setPathError(
        err instanceof Error ? err.message : "Failed to find paths",
      );
      setPathsData(null);
    } finally {
      setIsLoadingPaths(false);
    }
  }, [currentNodeId, target]);

  useEffect(() => {
    if (target) {
      fetchPaths();
    } else {
      setPathsData(null);
      setPathError(null);
    }
  }, [target, fetchPaths]);

  const selectTarget = (node: NodeResponse) => {
    setTarget({ id: node.id, concept: node.concept });
    setSearchQuery("");
    setShowDropdown(false);
    setSearchResults([]);
  };

  const clearTarget = () => {
    setTarget(null);
    setPathsData(null);
    setPathError(null);
  };

  if (!target) {
    return (
      <div className="space-y-4">
        <div className="relative" ref={dropdownRef}>
          <Input
            placeholder="Search for a node to compare..."
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            className="w-full"
          />
          {isSearching && (
            <Loader2 className="absolute right-3 top-2.5 h-4 w-4 animate-spin text-muted-foreground" />
          )}
          {showDropdown && searchResults.length > 0 && (
            <div className="absolute z-10 mt-1 w-full rounded-md border bg-popover shadow-md max-h-60 overflow-y-auto">
              {searchResults.map((node) => (
                <button
                  key={node.id}
                  className="w-full px-3 py-2 text-left text-sm hover:bg-accent flex items-center justify-between gap-2"
                  onClick={() => selectTarget(node)}
                >
                  <span className="truncate">{node.concept}</span>
                  <Badge variant="outline" className="text-xs shrink-0">
                    {node.node_type}
                  </Badge>
                </button>
              ))}
            </div>
          )}
        </div>

        {!searchQuery && (
          <div className="flex flex-col items-center justify-center py-12 text-muted-foreground">
            <GitCompareArrows className="h-10 w-10 mb-3 opacity-50" />
            <p>Search for a node to find paths from</p>
            <p className="text-xs mt-1">&quot;{currentNodeConcept}&quot;</p>
          </div>
        )}
      </div>
    );
  }

  return (
    <div className="space-y-4">
      {/* Selected target */}
      <Card>
        <CardContent className="py-3 flex items-center justify-between">
          <div className="min-w-0">
            <p className="text-xs text-muted-foreground">Comparing with</p>
            <p className="text-sm font-medium truncate">{target.concept}</p>
          </div>
          <Button variant="ghost" size="icon" onClick={clearTarget}>
            <X className="h-4 w-4" />
          </Button>
        </CardContent>
      </Card>

      {/* Loading state */}
      {isLoadingPaths && (
        <div className="flex items-center justify-center py-8">
          <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
        </div>
      )}

      {/* Error state */}
      {pathError && (
        <p className="text-sm text-destructive">{pathError}</p>
      )}

      {/* Results */}
      {pathsData && !isLoadingPaths && (
        <div className="space-y-3">
          <div className="flex items-center gap-2">
            <Badge variant="secondary">
              {pathsData.total_found} shortest path{pathsData.total_found !== 1 ? "s" : ""} found
            </Badge>
            {pathsData.truncated && (
              <Badge variant="outline" className="text-xs">
                More paths may exist
              </Badge>
            )}
          </div>

          {pathsData.paths.length === 0 && (
            <div className="flex flex-col items-center justify-center py-8 text-muted-foreground">
              <GitCompareArrows className="h-10 w-10 mb-3 opacity-50" />
              <p>No paths found between these nodes.</p>
            </div>
          )}

          {pathsData.paths.map((path, pathIdx) => (
            <Card key={pathIdx}>
              <CardContent className="py-3 space-y-1">
                <div className="flex items-center justify-between mb-2">
                  <span className="text-xs text-muted-foreground font-medium">
                    Path {pathIdx + 1}
                  </span>
                  <Badge variant="outline" className="text-xs">
                    {path.length} hop{path.length !== 1 ? "s" : ""}
                  </Badge>
                </div>

                {path.steps.map((step: PathStepResponse, stepIdx: number) => (
                  <div key={stepIdx}>
                    {/* Edge connector (skip for first step) */}
                    {step.edge && (
                      <div className="flex items-center gap-2 pl-4 py-1">
                        <div className="w-px h-4 bg-border" />
                        <ArrowRight className="h-3 w-3 text-muted-foreground shrink-0" />
                        <Badge
                          className={cn(
                            "text-xs capitalize",
                            relationshipColors[step.edge.relationship_type] ??
                              "",
                          )}
                          variant="secondary"
                        >
                          {step.edge.relationship_type.replace(/_/g, " ")}
                        </Badge>
                        <span className="text-xs text-muted-foreground">
                          {step.edge.supporting_fact_ids.length} {step.edge.supporting_fact_ids.length === 1 ? "fact" : "facts"}
                        </span>
                      </div>
                    )}

                    {/* Node */}
                    <Button
                      variant="ghost"
                      size="sm"
                      className="w-full justify-start text-sm h-auto py-1.5 px-2"
                      onClick={() => onNodeSelect?.(step.node_id)}
                      disabled={!onNodeSelect}
                    >
                      <span className="truncate">{step.node_concept}</span>
                      <Badge
                        variant="outline"
                        className="text-xs ml-auto shrink-0"
                      >
                        {step.node_type}
                      </Badge>
                    </Button>
                  </div>
                ))}

                {/* Edge justifications */}
                {path.steps.some((s) => s.edge?.justification) && (
                  <div className="pt-2 border-t mt-2 space-y-1">
                    {path.steps
                      .filter((s) => s.edge?.justification)
                      .map((s, i) => (
                        <p
                          key={i}
                          className="text-xs text-muted-foreground italic"
                        >
                          {s.edge!.justification}
                        </p>
                      ))}
                  </div>
                )}
              </CardContent>
            </Card>
          ))}
        </div>
      )}
    </div>
  );
}
