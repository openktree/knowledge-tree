"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Separator } from "@/components/ui/separator";
import { ExternalLink, X, Loader2, User } from "lucide-react";
import { cn } from "@/lib/utils";
import { api } from "@/lib/api";
import type { FactResponse, FactNodeInfo } from "@/types";

export interface FactDetailPanelProps {
  factId: string | null;
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

const TYPE_COLORS: Record<string, string> = {
  experiment: "bg-blue-500/20 text-blue-400",
  observation: "bg-green-500/20 text-green-400",
  measurement: "bg-cyan-500/20 text-cyan-400",
  opinion: "bg-yellow-500/20 text-yellow-400",
  claim: "bg-orange-500/20 text-orange-400",
  testimony: "bg-pink-500/20 text-pink-400",
  story: "bg-purple-500/20 text-purple-400",
  definition: "bg-indigo-500/20 text-indigo-400",
  historical_event: "bg-amber-500/20 text-amber-400",
  perspective: "bg-violet-500/20 text-violet-400",
  consensus: "bg-emerald-500/20 text-emerald-400",
};

const NODE_TYPE_COLORS: Record<string, string> = {
  concept: "bg-blue-500/20 text-blue-400",
  entity: "bg-emerald-500/20 text-emerald-400",
  perspective: "bg-pink-500/20 text-pink-400",
  event: "bg-orange-500/20 text-orange-400",
  location: "bg-cyan-500/20 text-cyan-400",
  synthesis: "bg-pink-500/20 text-pink-400",
};

export function FactDetailPanel({ factId, onClose }: FactDetailPanelProps) {
  const [fact, setFact] = useState<FactResponse | null>(null);
  const [nodes, setNodes] = useState<FactNodeInfo[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const prevFactId = useRef<string | null>(null);

  const fetchFact = useCallback(async (id: string) => {
    setIsLoading(true);
    setError(null);
    try {
      const [factResult, nodesResult] = await Promise.all([
        api.facts.get(id),
        api.facts.getNodes(id),
      ]);
      setFact(factResult);
      setNodes(nodesResult);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load fact");
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => {
    if (factId && factId !== prevFactId.current) {
      prevFactId.current = factId;
      fetchFact(factId);
    } else if (!factId) {
      prevFactId.current = null;
    }
  }, [factId, fetchFact]);

  if (!factId) return null;

  return (
    <div
      className={cn(
        "fixed top-0 right-0 bottom-0 w-[32rem] max-w-full bg-background border-l shadow-xl z-50 overflow-hidden",
        "flex flex-col",
        "animate-in slide-in-from-right duration-200",
      )}
    >
      <div className="flex items-center justify-between px-4 py-3 border-b">
        <h2 className="text-lg font-semibold truncate pr-2">Fact Detail</h2>
        <Button variant="ghost" size="icon" onClick={onClose}>
          <X className="h-4 w-4" />
        </Button>
      </div>

      {isLoading && !fact && (
        <div className="flex-1 flex items-center justify-center">
          <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
        </div>
      )}

      {error && (
        <div className="px-4 py-3">
          <p className="text-sm text-destructive">{error}</p>
        </div>
      )}

      {fact && (
        <ScrollArea className="flex-1">
          <div className="px-4 py-3 space-y-4">
            {/* Type and date */}
            <div className="flex items-center gap-2">
              <Badge
                variant="secondary"
                className={TYPE_COLORS[fact.fact_type] ?? ""}
              >
                {fact.fact_type}
              </Badge>
              <span className="text-xs text-muted-foreground">
                {formatDate(fact.created_at)}
              </span>
            </div>

            <Separator />

            {/* Content */}
            <div className="space-y-1">
              <h3 className="text-sm font-medium text-muted-foreground">
                Content
              </h3>
              <p className="text-sm whitespace-pre-wrap">{fact.content}</p>
            </div>

            {/* Metadata */}
            {fact.metadata && Object.keys(fact.metadata).length > 0 && (
              <>
                <Separator />
                <div className="space-y-1">
                  <h3 className="text-sm font-medium text-muted-foreground">
                    Metadata
                  </h3>
                  <pre className="text-xs bg-muted/50 rounded-md p-3 overflow-auto">
                    {JSON.stringify(fact.metadata, null, 2)}
                  </pre>
                </div>
              </>
            )}

            {/* Authors */}
            {(() => {
              const authors = fact.sources
                .filter((s) => s.author_person || s.author_org)
                .map((s) => ({
                  person: s.author_person,
                  org: s.author_org,
                  key: `${s.author_person ?? ""}-${s.author_org ?? ""}`,
                }));
              const unique = authors.filter(
                (a, i, arr) => arr.findIndex((b) => b.key === a.key) === i,
              );
              if (unique.length === 0) return null;
              return (
                <>
                  <Separator />
                  <div className="space-y-2">
                    <h3 className="text-sm font-medium text-muted-foreground">
                      Authors ({unique.length})
                    </h3>
                    <div className="flex flex-wrap gap-2">
                      {unique.map((author) => (
                        <div
                          key={author.key}
                          className="flex items-center gap-1.5 rounded-lg border bg-muted/30 px-3 py-2"
                        >
                          <User className="h-4 w-4 shrink-0 text-muted-foreground" />
                          <div className="flex flex-col">
                            {author.person && (
                              <span className="text-sm font-medium">
                                {author.person}
                              </span>
                            )}
                            {author.org && (
                              <span className="text-[11px] text-muted-foreground">
                                {author.org}
                              </span>
                            )}
                          </div>
                        </div>
                      ))}
                    </div>
                  </div>
                </>
              );
            })()}

            {/* Sources */}
            {fact.sources.length > 0 && (
              <>
                <Separator />
                <div className="space-y-2">
                  <h3 className="text-sm font-medium text-muted-foreground">
                    Sources ({fact.sources.length})
                  </h3>
                  {fact.sources.map((source) => (
                    <div
                      key={source.source_id}
                      className="rounded-md border p-2 space-y-1"
                    >
                      <div className="flex items-center gap-1">
                        <ExternalLink className="h-3 w-3 shrink-0 text-muted-foreground" />
                        <a
                          href={source.uri}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="text-xs truncate hover:underline text-blue-600 dark:text-blue-400"
                        >
                          {source.title ?? source.uri}
                        </a>
                      </div>
                      <div className="text-xs text-muted-foreground">
                        {source.provider_id} &middot;{" "}
                        {formatDate(source.retrieved_at)}
                      </div>
                      {source.context_snippet && (
                        <p className="text-xs text-muted-foreground italic">
                          &ldquo;{source.context_snippet}&rdquo;
                        </p>
                      )}
                      {source.attribution && (
                        <p className="text-xs text-muted-foreground">
                          {source.attribution}
                        </p>
                      )}
                    </div>
                  ))}
                </div>
              </>
            )}

            {/* Connected Nodes */}
            {nodes.length > 0 && (
              <>
                <Separator />
                <div className="space-y-2">
                  <h3 className="text-sm font-medium text-muted-foreground">
                    Connected Nodes ({nodes.length})
                  </h3>
                  {nodes.map((node) => (
                    <Link
                      key={node.node_id}
                      href={`/nodes/${node.node_id}`}
                      className="flex items-center gap-1.5 rounded-md border p-2 hover:bg-muted/50 transition-colors"
                    >
                      <Badge
                        variant="secondary"
                        className={cn(
                          "text-[10px] px-1.5 py-0",
                          NODE_TYPE_COLORS[node.node_type] ?? "",
                        )}
                      >
                        {node.node_type}
                      </Badge>
                      {node.stance && (
                        <Badge
                          variant="outline"
                          className="text-[10px] px-1.5 py-0"
                        >
                          {node.stance}
                        </Badge>
                      )}
                      <span className="text-xs font-medium truncate">
                        {node.concept}
                      </span>
                    </Link>
                  ))}
                </div>
              </>
            )}

            {/* ID */}
            <Separator />
            <div className="space-y-1">
              <h3 className="text-sm font-medium text-muted-foreground">ID</h3>
              <p className="text-xs font-mono text-muted-foreground break-all">
                {fact.id}
              </p>
            </div>
          </div>
        </ScrollArea>
      )}
    </div>
  );
}
