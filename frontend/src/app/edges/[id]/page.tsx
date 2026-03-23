"use client";

import { use, useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { Badge } from "@/components/ui/badge";
import { Separator } from "@/components/ui/separator";
import { ArrowRight, Loader2 } from "lucide-react";
import { api } from "@/lib/api";
import type { EdgeDetailResponse } from "@/types";
import { cn } from "@/lib/utils";
import { JustificationText } from "@/components/shared/JustificationText";

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

export default function EdgeDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = use(params);
  const [edge, setEdge] = useState<EdgeDetailResponse | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const fetched = useRef(false);

  const fetchEdge = useCallback(async (edgeId: string) => {
    setIsLoading(true);
    setError(null);
    try {
      const result = await api.edges.get(edgeId);
      setEdge(result);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load edge");
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => {
    if (!fetched.current) {
      fetched.current = true;
      fetchEdge(id);
    }
  }, [id, fetchEdge]);

  return (
    <div className="flex flex-col h-full">
      <div className="px-6 pt-6 pb-4">
        <h1 className="text-2xl font-bold tracking-tight">Edge Detail</h1>
        <p className="text-sm text-muted-foreground mt-1">
          Detailed view of a single edge with connected nodes and supporting
          facts
        </p>
      </div>

      {isLoading && !edge && (
        <div className="flex-1 flex items-center justify-center">
          <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
        </div>
      )}

      {error && (
        <div className="px-6 py-3">
          <p className="text-sm text-destructive">{error}</p>
        </div>
      )}

      {edge && (
        <div className="flex-1 overflow-y-auto px-6 pb-6">
          <div className="max-w-3xl space-y-6">
            {/* Type, strength, date */}
            <div className="flex items-center gap-3 flex-wrap">
              <Badge
                variant="secondary"
                className={cn(
                  "capitalize",
                  RELATIONSHIP_COLORS[edge.relationship_type] ?? "",
                )}
              >
                {edge.relationship_type.replace(/_/g, " ")}
              </Badge>
              <span className="text-sm text-muted-foreground">
                {Math.round(edge.weight)} shared {Math.round(edge.weight) === 1 ? "fact" : "facts"}
              </span>
              <span className="text-sm text-muted-foreground">
                {formatDate(edge.created_at)}
              </span>
            </div>

            <Separator />

            {/* Source → Target */}
            <div className="space-y-3">
              <h2 className="text-sm font-medium text-muted-foreground uppercase tracking-wide">
                Connection
              </h2>
              <div className="flex items-center gap-3">
                <Link
                  href={`/nodes/${edge.source_node_id}`}
                  className="text-base hover:underline text-blue-600 dark:text-blue-400"
                >
                  {edge.source_node_concept ?? "Unknown node"}
                </Link>
                <ArrowRight className="h-4 w-4 text-muted-foreground shrink-0" />
                <Link
                  href={`/nodes/${edge.target_node_id}`}
                  className="text-base hover:underline text-blue-600 dark:text-blue-400"
                >
                  {edge.target_node_concept ?? "Unknown node"}
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
                <div className="space-y-2">
                  <h2 className="text-sm font-medium text-muted-foreground uppercase tracking-wide">
                    Justification
                  </h2>
                  <JustificationText
                    text={edge.justification}
                    className="text-base whitespace-pre-wrap leading-relaxed"
                  />
                </div>
              </>
            )}

            {/* Supporting Facts */}
            {edge.supporting_facts.length > 0 && (
              <>
                <Separator />
                <div className="space-y-3">
                  <h2 className="text-sm font-medium text-muted-foreground uppercase tracking-wide">
                    Supporting Facts ({edge.supporting_facts.length})
                  </h2>
                  {edge.supporting_facts.map((fact) => (
                    <Link
                      key={fact.id}
                      href={`/facts/${fact.id}`}
                      className="block rounded-md border p-4 space-y-2 hover:bg-accent/30 transition-colors"
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
                        <span className="text-xs text-muted-foreground">
                          {formatDate(fact.created_at)}
                        </span>
                      </div>
                      <p className="text-sm">{fact.content}</p>
                      {fact.sources.length > 0 && (
                        <p className="text-xs text-muted-foreground">
                          {fact.sources.length} source
                          {fact.sources.length !== 1 ? "s" : ""}
                        </p>
                      )}
                    </Link>
                  ))}
                </div>
              </>
            )}

            {/* Edge ID */}
            <Separator />
            <div className="space-y-1">
              <h2 className="text-sm font-medium text-muted-foreground uppercase tracking-wide">
                Edge ID
              </h2>
              <p className="text-xs font-mono text-muted-foreground break-all">
                {edge.id}
              </p>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
