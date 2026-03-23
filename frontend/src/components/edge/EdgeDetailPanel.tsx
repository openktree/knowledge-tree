"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Separator } from "@/components/ui/separator";
import { ArrowRight, X, Loader2 } from "lucide-react";
import { cn } from "@/lib/utils";
import { api } from "@/lib/api";
import type { EdgeDetailResponse } from "@/types";
import { JustificationText } from "@/components/shared/JustificationText";

export interface EdgeDetailPanelProps {
  edgeId: string | null;
  onClose: () => void;
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

const RELATIONSHIP_COLORS: Record<string, string> = {
  related: "bg-gray-100 text-gray-800 dark:bg-gray-900 dark:text-gray-200",
};

const FACT_TYPE_COLORS: Record<string, string> = {
  claim: "bg-orange-500/20 text-orange-400",
  account: "bg-purple-500/20 text-purple-400",
  measurement: "bg-cyan-500/20 text-cyan-400",
  formula: "bg-indigo-500/20 text-indigo-400",
  quote: "bg-pink-500/20 text-pink-400",
  procedure: "bg-blue-500/20 text-blue-400",
  reference: "bg-green-500/20 text-green-400",
  code: "bg-amber-500/20 text-amber-400",
  perspective: "bg-violet-500/20 text-violet-400",
};

export function EdgeDetailPanel({ edgeId, onClose }: EdgeDetailPanelProps) {
  const [edge, setEdge] = useState<EdgeDetailResponse | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const prevEdgeId = useRef<string | null>(null);

  const fetchEdge = useCallback(async (id: string) => {
    setIsLoading(true);
    setError(null);
    try {
      const result = await api.edges.get(id);
      setEdge(result);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load edge");
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => {
    if (edgeId && edgeId !== prevEdgeId.current) {
      prevEdgeId.current = edgeId;
      fetchEdge(edgeId);
    } else if (!edgeId) {
      prevEdgeId.current = null;
    }
  }, [edgeId, fetchEdge]);

  if (!edgeId) return null;

  return (
    <div
      className={cn(
        "fixed top-0 right-0 bottom-0 w-[32rem] max-w-full bg-background border-l shadow-xl z-50 overflow-hidden",
        "flex flex-col",
        "animate-in slide-in-from-right duration-200",
      )}
    >
      <div className="flex items-center justify-between px-4 py-3 border-b">
        <h2 className="text-lg font-semibold truncate pr-2">Edge Detail</h2>
        <Button variant="ghost" size="icon" onClick={onClose}>
          <X className="h-4 w-4" />
        </Button>
      </div>

      {isLoading && !edge && (
        <div className="flex-1 flex items-center justify-center">
          <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
        </div>
      )}

      {error && (
        <div className="px-4 py-3">
          <p className="text-sm text-destructive">{error}</p>
        </div>
      )}

      {edge && (
        <ScrollArea className="flex-1">
          <div className="px-4 py-3 space-y-4">
            {/* Type, strength, date */}
            <div className="flex items-center gap-2 flex-wrap">
              <Badge
                variant="secondary"
                className={cn(
                  "capitalize",
                  RELATIONSHIP_COLORS[edge.relationship_type] ?? "",
                )}
              >
                {edge.relationship_type.replace(/_/g, " ")}
              </Badge>
              <span className="text-xs text-muted-foreground">
                {edge.supporting_fact_ids.length} {edge.supporting_fact_ids.length === 1 ? "fact" : "facts"}
              </span>
              <span className="text-xs text-muted-foreground">
                {formatDate(edge.created_at)}
              </span>
            </div>

            {/* Strength indicator (log-scaled from fact count weight) */}
            {edge.weight > 0 && (
              <div className="space-y-1">
                <div className="flex items-center justify-between">
                  <span className="text-xs text-muted-foreground">Strength</span>
                  <span className="text-xs font-mono text-muted-foreground">
                    {Math.round(edge.weight)} shared facts
                  </span>
                </div>
                <div className="h-1.5 w-full rounded-full bg-muted overflow-hidden">
                  <div
                    className={cn(
                      "h-full rounded-full transition-all",
                      edge.relationship_type === "cross_type"
                        ? "bg-violet-500"
                        : "bg-green-500",
                    )}
                    style={{
                      width: `${Math.min(100, (Math.log10(Math.max(1, edge.weight)) / Math.log10(100)) * 100)}%`,
                    }}
                  />
                </div>
              </div>
            )}

            <Separator />

            {/* Source → Target */}
            <div className="space-y-2">
              <h3 className="text-sm font-medium text-muted-foreground">
                Connection
              </h3>
              <div className="flex items-center gap-2">
                <Link
                  href={`/nodes/${edge.source_node_id}`}
                  className="text-sm hover:underline text-blue-600 dark:text-blue-400 truncate"
                >
                  {edge.source_node_concept ?? edge.source_node_id.slice(0, 8) + "..."}
                </Link>
                <ArrowRight className="h-3 w-3 text-muted-foreground shrink-0" />
                <Link
                  href={`/nodes/${edge.target_node_id}`}
                  className="text-sm hover:underline text-blue-600 dark:text-blue-400 truncate"
                >
                  {edge.target_node_concept ?? edge.target_node_id.slice(0, 8) + "..."}
                </Link>
              </div>
              <div className="text-xs font-mono text-muted-foreground space-y-0.5">
                <p>Source: {edge.source_node_id}</p>
                <p>Target: {edge.target_node_id}</p>
              </div>
            </div>

            {/* Justification */}
            {edge.justification && (
              <>
                <Separator />
                <div className="space-y-1">
                  <h3 className="text-sm font-medium text-muted-foreground">
                    Justification
                  </h3>
                  <JustificationText
                    text={edge.justification}
                    className="text-sm whitespace-pre-wrap"
                  />
                </div>
              </>
            )}

            {/* Supporting Facts */}
            {edge.supporting_facts.length > 0 && (
              <>
                <Separator />
                <div className="space-y-2">
                  <h3 className="text-sm font-medium text-muted-foreground">
                    Supporting Facts ({edge.supporting_facts.length})
                  </h3>
                  {edge.supporting_facts.map((fact) => (
                    <Link
                      key={fact.id}
                      href={`/facts/${fact.id}`}
                      className="block rounded-md border p-2 space-y-1 hover:bg-accent/30 transition-colors"
                    >
                      <div className="flex items-center gap-2">
                        <Badge
                          variant="secondary"
                          className={cn(
                            "text-xs",
                            FACT_TYPE_COLORS[fact.fact_type] ?? "",
                          )}
                        >
                          {fact.fact_type}
                        </Badge>
                      </div>
                      <p className="text-xs line-clamp-3">{fact.content}</p>
                    </Link>
                  ))}
                </div>
              </>
            )}

            {/* Edge ID */}
            <Separator />
            <div className="space-y-1">
              <h3 className="text-sm font-medium text-muted-foreground">ID</h3>
              <p className="text-xs font-mono text-muted-foreground break-all">
                {edge.id}
              </p>
            </div>
          </div>
        </ScrollArea>
      )}
    </div>
  );
}
