"use client";

import { use, useState, useCallback } from "react";
import { useSourceDetail } from "@/hooks/useSourceDetail";
import { api } from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import { Separator } from "@/components/ui/separator";
import {
  Tabs,
  TabsList,
  TabsTrigger,
  TabsContent,
} from "@/components/ui/tabs";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import {
  Loader2,
  ExternalLink,
  ArrowLeft,
  FileText,
  Globe,
  User,
  RefreshCw,
  AlertTriangle,
  ChevronDown,
  ChevronUp,
} from "lucide-react";
import Link from "next/link";
import type { FactResponse, ProhibitedChunkResponse, SourceLinkedNode } from "@/types";

const PROVIDER_COLORS: Record<string, string> = {
  serper: "bg-blue-500/20 text-blue-400",
  brave: "bg-orange-500/20 text-orange-400",
  upload: "bg-green-500/20 text-green-400",
  url_fetch: "bg-purple-500/20 text-purple-400",
};

function formatDate(dateStr: string): string {
  return new Date(dateStr).toLocaleDateString("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function FactsList({ facts }: { facts: FactResponse[] }) {
  if (facts.length === 0) {
    return (
      <div className="text-center py-8 text-sm text-muted-foreground">
        No facts extracted from this source.
        <p className="mt-2 text-xs">
          This can happen when the source content couldn&apos;t be fetched,
          was too short, or all extracted facts were deduplicated against existing ones.
        </p>
      </div>
    );
  }

  return (
    <div className="space-y-3">
      {facts.map((fact) => (
        <Card key={fact.id} className="p-4">
          <p className="text-sm">{fact.content}</p>
          <div className="flex items-center gap-2 mt-2">
            <Badge variant="outline" className="text-xs">
              {fact.fact_type}
            </Badge>
            <span className="text-xs text-muted-foreground">
              {formatDate(fact.created_at)}
            </span>
          </div>
          {fact.sources.length > 0 && (
            <div className="mt-2 space-y-1">
              {fact.sources.map((s, i) => (
                <div key={i} className="text-xs text-muted-foreground">
                  {s.context_snippet && (
                    <p className="italic border-l-2 border-muted pl-2 mt-1">
                      &ldquo;{s.context_snippet}&rdquo;
                    </p>
                  )}
                  {s.attribution && (
                    <p className="mt-0.5">Attribution: {s.attribution}</p>
                  )}
                </div>
              ))}
            </div>
          )}
        </Card>
      ))}
    </div>
  );
}

function LinkedNodesList({ nodes }: { nodes: SourceLinkedNode[] }) {
  if (nodes.length === 0) {
    return (
      <div className="text-center py-8 text-sm text-muted-foreground">
        No nodes linked to this source yet.
      </div>
    );
  }

  return (
    <div className="space-y-2">
      {nodes.map((node) => (
        <Link
          key={node.node_id}
          href={`/nodes/${node.node_id}`}
          className="block"
        >
          <Card className="p-3 hover:bg-accent/30 transition-colors cursor-pointer">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2">
                <span className="text-sm font-medium">{node.concept}</span>
                <Badge variant="secondary" className="text-xs">
                  {node.node_type}
                </Badge>
              </div>
              <Badge variant="outline" className="text-xs">
                {node.fact_count} fact{node.fact_count !== 1 ? "s" : ""}
              </Badge>
            </div>
          </Card>
        </Link>
      ))}
    </div>
  );
}

function ProhibitedChunksList({ chunks }: { chunks: ProhibitedChunkResponse[] }) {
  const [expanded, setExpanded] = useState<Set<string>>(new Set());

  if (chunks.length === 0) {
    return (
      <div className="text-center py-8 text-sm text-muted-foreground">
        No prohibited chunks for this source.
      </div>
    );
  }

  const toggle = (id: string) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  return (
    <div className="space-y-3">
      {chunks.map((chunk) => (
        <Card key={chunk.id} className="p-4">
          <div className="flex items-start justify-between gap-2">
            <div className="flex-1 min-w-0">
              <div className="flex items-center gap-2 mb-2">
                <Badge variant="outline" className="text-xs text-amber-400 border-amber-400/30">
                  {chunk.model_id}
                </Badge>
                {chunk.fallback_model_id && (
                  <Badge variant="outline" className="text-xs text-amber-400 border-amber-400/30">
                    fallback: {chunk.fallback_model_id}
                  </Badge>
                )}
                <span className="text-xs text-muted-foreground">
                  {formatDate(chunk.created_at)}
                </span>
              </div>
              <p className="text-xs text-destructive mb-2">{chunk.error_message}</p>
              <Button
                variant="ghost"
                size="sm"
                className="gap-1 text-xs"
                onClick={() => toggle(chunk.id)}
              >
                {expanded.has(chunk.id) ? (
                  <ChevronUp className="size-3" />
                ) : (
                  <ChevronDown className="size-3" />
                )}
                {expanded.has(chunk.id) ? "Hide" : "Show"} chunk text
              </Button>
              {expanded.has(chunk.id) && (
                <pre className="text-xs whitespace-pre-wrap break-words bg-muted/30 rounded-md p-3 mt-2 max-h-[300px] overflow-y-auto font-mono">
                  {chunk.chunk_text}
                </pre>
              )}
            </div>
          </div>
        </Card>
      ))}
    </div>
  );
}

function ContentPreview({ content, isFullText }: { content: string | null; isFullText: boolean }) {
  if (!content) {
    return (
      <div className="text-center py-8 text-sm text-muted-foreground">
        No content stored for this source.
        <p className="mt-2 text-xs">
          The source URL may not have been fetchable, or content was too large to store.
        </p>
      </div>
    );
  }

  return (
    <div>
      {!isFullText && (
        <div className="mb-3 text-xs text-amber-500">
          Partial content only (snippet from search result)
        </div>
      )}
      <pre className="text-sm whitespace-pre-wrap break-words bg-muted/30 rounded-md p-4 max-h-[600px] overflow-y-auto font-mono">
        {content}
      </pre>
    </div>
  );
}

export default function SourceDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = use(params);
  const { source, isLoading, error, refetch } = useSourceDetail(id);
  const [isReingesting, setIsReingesting] = useState(false);
  const [reingestMessage, setReingestMessage] = useState<string | null>(null);
  const [reingestError, setReingestError] = useState<string | null>(null);
  const handleReingest = useCallback(async () => {
    if (!source) return;
    setIsReingesting(true);
    setReingestMessage(null);
    setReingestError(null);
    try {
      const result = await api.sources.reingest(source.id);
      setReingestMessage(result.message);
      refetch();
    } catch (err) {
      setReingestError(
        err instanceof Error ? err.message : "Reingest failed",
      );
    } finally {
      setIsReingesting(false);
    }
  }, [source, refetch]);

  return (
    <div className="flex flex-col h-full">
      <div className="px-6 pt-6 pb-4">
        <div className="flex items-center gap-3 mb-2">
          <Button variant="ghost" size="icon-sm" asChild>
            <Link href="/sources">
              <ArrowLeft className="size-4" />
            </Link>
          </Button>
          <h1 className="text-2xl font-bold tracking-tight truncate flex-1">
            {isLoading && !source
              ? "Loading..."
              : source?.title || source?.uri || "Source Not Found"}
          </h1>
        </div>
        <p className="text-sm text-muted-foreground ml-10">Source Detail</p>
      </div>

      {isLoading && !source && (
        <div className="flex-1 flex items-center justify-center">
          <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
        </div>
      )}

      {error && (
        <div className="px-6 py-3">
          <p className="text-sm text-destructive">{error}</p>
        </div>
      )}

      {source && (
        <div className="flex-1 flex flex-col min-h-0 px-6">
          {/* Metadata badges */}
          <div className="flex flex-wrap items-center gap-2 pb-4">
            <Badge
              variant="secondary"
              className={PROVIDER_COLORS[source.provider_id] ?? ""}
            >
              {source.provider_id}
            </Badge>
            <Badge variant={source.fact_count > 0 ? "default" : "destructive"}>
              {source.fact_count} fact{source.fact_count !== 1 ? "s" : ""}
            </Badge>
            {source.prohibited_chunk_count > 0 && (
              <Badge variant="outline" className="text-amber-400 border-amber-400/30">
                <AlertTriangle className="size-3 mr-1" />
                {source.prohibited_chunk_count} prohibited
              </Badge>
            )}
            <Badge variant="outline">
              {source.linked_nodes.length} node{source.linked_nodes.length !== 1 ? "s" : ""}
            </Badge>
            {source.is_full_text ? (
              <Badge variant="secondary">
                <FileText className="size-3 mr-1" />
                Full text
              </Badge>
            ) : source.fetch_error ? (
              <Badge variant="outline" className="text-red-400 border-red-400/30" title={source.fetch_error}>
                <AlertTriangle className="size-3 mr-1" />
                Fetch failed
              </Badge>
            ) : (
              <Badge variant="outline" className="text-amber-500 border-amber-500/30">
                Snippet only
              </Badge>
            )}
            {source.content_type && (
              <Badge variant="outline">{source.content_type}</Badge>
            )}
            <span className="text-xs text-muted-foreground">
              Retrieved: {formatDate(source.retrieved_at)}
            </span>
            <div className="ml-auto flex items-center gap-2">
              <Button
                variant={source.fact_count === 0 ? "default" : "outline"}
                size="sm"
                onClick={handleReingest}
                disabled={isReingesting}
              >
                {isReingesting ? (
                  <Loader2 className="size-3.5 mr-1 animate-spin" />
                ) : (
                  <RefreshCw className="size-3.5 mr-1" />
                )}
                {isReingesting ? "Reingesting..." : "Reingest"}
              </Button>
              <Button variant="ghost" size="sm" asChild>
                <a href={source.uri} target="_blank" rel="noopener noreferrer">
                  <Globe className="size-3.5 mr-1" />
                  Open source
                  <ExternalLink className="size-3 ml-1" />
                </a>
              </Button>
            </div>
          </div>

          {/* Reingest feedback */}
          {reingestMessage && (
            <div className="mb-3 rounded-md bg-green-500/10 border border-green-500/20 px-4 py-2.5 text-sm text-green-400">
              {reingestMessage}
            </div>
          )}
          {reingestError && (
            <div className="mb-3 rounded-md bg-destructive/10 border border-destructive/20 px-4 py-2.5 text-sm text-destructive">
              {reingestError}
            </div>
          )}

          {/* Fetch error detail */}
          {source.fetch_error && (
            <div className="mb-3 rounded-md bg-red-500/10 border border-red-500/20 px-4 py-2.5 text-sm text-red-400">
              <span className="font-medium">Scraping blocked:</span> {source.fetch_error}
            </div>
          )}

          {/* Fetcher strategy audit — shows which providers were tried and who won */}
          {source.fetcher && source.fetcher.attempts.length > 0 && (
            <div className="mb-3 rounded-md bg-muted/40 border border-border px-4 py-2.5 text-xs">
              <div className="mb-1 font-medium text-muted-foreground">
                {source.fetcher.winner ? (
                  <>Fetched via <span className="text-foreground">{source.fetcher.winner}</span></>
                ) : (
                  <>All fetchers failed</>
                )}
                {source.fetcher.attempts.length > 1 && (
                  <span className="text-muted-foreground">
                    {" "}
                    (tried {source.fetcher.attempts.map((a) => a.provider_id).join(" → ")})
                  </span>
                )}
              </div>
              <ul className="space-y-0.5 font-mono">
                {source.fetcher.attempts.map((attempt, idx) => (
                  <li
                    key={`${attempt.provider_id}-${idx}`}
                    className={attempt.success ? "text-green-500" : "text-muted-foreground"}
                  >
                    {attempt.success ? "✓" : "✗"} {attempt.provider_id} — {attempt.elapsed_ms}ms
                    {attempt.error && <span className="text-red-400/80"> {attempt.error}</span>}
                  </li>
                ))}
              </ul>
            </div>
          )}

          {/* URI display */}
          <div className="text-xs text-muted-foreground truncate pb-3" title={source.uri}>
            {source.uri}
          </div>

          {/* Authors */}
          {(() => {
            const authors = source.facts
              .flatMap((f) => f.sources)
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
              <div className="space-y-3 pb-4">
                <h2 className="text-sm font-medium text-muted-foreground uppercase tracking-wide">
                  Authors ({unique.length})
                </h2>
                <div className="flex flex-wrap gap-3">
                  {unique.map((author) => (
                    <div
                      key={author.key}
                      className="flex items-center gap-2 rounded-lg border bg-muted/30 px-4 py-2.5"
                    >
                      <User className="h-5 w-5 shrink-0 text-muted-foreground" />
                      <div className="flex flex-col">
                        {author.person && (
                          <span className="text-sm font-medium">
                            {author.person}
                          </span>
                        )}
                        {author.org && (
                          <span className="text-xs text-muted-foreground">
                            {author.org}
                          </span>
                        )}
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            );
          })()}

          <Separator />

          <Tabs defaultValue="facts" className="flex-1 flex flex-col min-h-0 pt-4">
            <TabsList>
              <TabsTrigger value="facts">
                Facts ({source.facts.length})
              </TabsTrigger>
              <TabsTrigger value="nodes">
                Linked Nodes ({source.linked_nodes.length})
              </TabsTrigger>
              <TabsTrigger value="content">Content</TabsTrigger>
              {source.prohibited_chunk_count > 0 && (
                <TabsTrigger value="prohibited">
                  <AlertTriangle className="size-3 mr-1" />
                  Prohibited ({source.prohibited_chunk_count})
                </TabsTrigger>
              )}
            </TabsList>

            <div className="flex-1 overflow-y-auto pt-4 pb-6">
              <TabsContent value="facts" className="mt-0">
                <FactsList facts={source.facts} />
              </TabsContent>
              <TabsContent value="nodes" className="mt-0">
                <LinkedNodesList nodes={source.linked_nodes} />
              </TabsContent>
              <TabsContent value="content" className="mt-0">
                <ContentPreview
                  content={source.content_preview}
                  isFullText={source.is_full_text}
                />
              </TabsContent>
              {source.prohibited_chunk_count > 0 && (
                <TabsContent value="prohibited" className="mt-0">
                  <ProhibitedChunksList chunks={source.prohibited_chunks} />
                </TabsContent>
              )}
            </div>
          </Tabs>
        </div>
      )}
    </div>
  );
}
