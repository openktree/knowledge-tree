"use client";

import { useState, useEffect, useRef } from "react";
import type { NodeResponse, PathsResponse } from "@/types";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { ArrowRight, GitCompareArrows, Loader2, X } from "lucide-react";
import { api } from "@/lib/api";

interface CompareOverlayProps {
  sourceNodeId: string;
  sourceNodeConcept: string;
  targetNodeId: string | null;
  targetNodeConcept: string | null;
  pathsData: PathsResponse | null;
  isLoadingPaths: boolean;
  pathError: string | null;
  activePathIndex: number | null;
  onSelectTarget: (targetId: string) => void;
  onSetActivePathIndex: (index: number | null) => void;
  onClose: () => void;
}

export function CompareOverlay({
  sourceNodeId,
  sourceNodeConcept,
  targetNodeId,
  targetNodeConcept,
  pathsData,
  isLoadingPaths,
  pathError,
  activePathIndex,
  onSelectTarget,
  onSetActivePathIndex,
  onClose,
}: CompareOverlayProps) {
  const [searchQuery, setSearchQuery] = useState("");
  const [searchResults, setSearchResults] = useState<NodeResponse[]>([]);
  const [isSearching, setIsSearching] = useState(false);
  const [showDropdown, setShowDropdown] = useState(false);
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
        const result = await api.nodes.list({
          search: searchQuery,
          limit: 10,
        });
        const filtered = result.items.filter((n) => n.id !== sourceNodeId);
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
  }, [searchQuery, sourceNodeId]);

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

  const handleSelectTarget = (node: NodeResponse) => {
    setSearchQuery("");
    setShowDropdown(false);
    setSearchResults([]);
    onSelectTarget(node.id);
  };

  // ---- State 1: Searching for target ----
  if (!targetNodeId) {
    return (
      <div className="absolute top-12 left-1/2 -translate-x-1/2 z-20 w-80">
        <div className="bg-card/95 backdrop-blur-sm border rounded-lg shadow-lg p-3 space-y-2">
          <div className="flex items-center justify-between gap-2">
            <div className="flex items-center gap-2 min-w-0">
              <GitCompareArrows className="size-4 text-cyan-400 shrink-0" />
              <span className="text-xs font-medium truncate">
                Compare from: {sourceNodeConcept}
              </span>
            </div>
            <Button
              variant="ghost"
              size="icon"
              className="size-6 shrink-0"
              onClick={onClose}
            >
              <X className="size-3" />
            </Button>
          </div>

          <div className="relative" ref={dropdownRef}>
            <Input
              placeholder="Search for target node..."
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              className="h-8 text-xs"
              autoFocus
            />
            {isSearching && (
              <Loader2 className="absolute right-2.5 top-2 size-3.5 animate-spin text-muted-foreground" />
            )}
            {showDropdown && searchResults.length > 0 && (
              <div className="absolute z-10 mt-1 w-full rounded-md border bg-popover shadow-md max-h-48 overflow-y-auto">
                {searchResults.map((node) => (
                  <button
                    key={node.id}
                    className="w-full px-3 py-1.5 text-left text-xs hover:bg-accent flex items-center justify-between gap-2"
                    onClick={() => handleSelectTarget(node)}
                  >
                    <span className="truncate">{node.concept}</span>
                    <Badge variant="outline" className="text-[10px] shrink-0">
                      {node.node_type}
                    </Badge>
                  </button>
                ))}
              </div>
            )}
          </div>
        </div>
      </div>
    );
  }

  // ---- State 2: Slim status bar (target selected) ----
  return (
    <div className="absolute top-3 left-1/2 -translate-x-1/2 z-20">
      <div className="bg-card/95 backdrop-blur-sm border rounded-lg shadow-lg px-3 py-1.5 flex items-center gap-2">
        <GitCompareArrows className="size-3.5 text-cyan-400 shrink-0" />

        {isLoadingPaths ? (
          <>
            <span className="text-xs text-muted-foreground">
              Finding paths...
            </span>
            <Loader2 className="size-3.5 animate-spin text-muted-foreground" />
          </>
        ) : pathError ? (
          <span className="text-xs text-destructive">{pathError}</span>
        ) : pathsData && pathsData.paths.length === 0 ? (
          <span className="text-xs text-muted-foreground">
            No paths found between{" "}
            <span className="text-cyan-400 font-medium">
              {sourceNodeConcept}
            </span>
            {" and "}
            <span className="text-violet-400 font-medium">
              {targetNodeConcept ?? "target"}
            </span>
          </span>
        ) : (
          <>
            <span className="text-xs">
              <span className="text-cyan-400 font-medium">
                {sourceNodeConcept}
              </span>
              <ArrowRight className="size-3 inline mx-1 text-muted-foreground" />
              <span className="text-violet-400 font-medium">
                {targetNodeConcept ?? "target"}
              </span>
            </span>
            {pathsData && pathsData.paths.length > 1 && (
              <div className="flex items-center gap-0.5 border-l pl-2 ml-1">
                <Button
                  variant={activePathIndex === null ? "secondary" : "ghost"}
                  size="sm"
                  className="h-5 text-[10px] px-1.5"
                  onClick={() => onSetActivePathIndex(null)}
                >
                  All
                </Button>
                {pathsData.paths.map((_, idx) => (
                  <Button
                    key={idx}
                    variant={activePathIndex === idx ? "secondary" : "ghost"}
                    size="sm"
                    className="h-5 text-[10px] px-1.5"
                    onClick={() => onSetActivePathIndex(idx)}
                  >
                    {idx + 1}
                  </Button>
                ))}
              </div>
            )}
          </>
        )}

        <Button
          variant="ghost"
          size="sm"
          className="h-6 text-xs px-2 ml-1 gap-1"
          onClick={onClose}
        >
          <X className="size-3" />
          Exit
        </Button>
      </div>
    </div>
  );
}
