"use client";

import { use, useState, useEffect, useCallback } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import dynamic from "next/dynamic";
import {
  ArrowLeft,
  Loader2,
  GitBranch,
  Merge,
  ArrowUpRight,
  Sprout,
  Network,
  Rocket,
  Activity,
  Check,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Separator } from "@/components/ui/separator";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useSeedDetail } from "@/hooks/useSeedDetail";
import { api } from "@/lib/api";
import type {
  SeedRouteResponse,
  SeedMergeResponse,
  SeedFactResponse,
  SeedDivergenceResponse,
} from "@/types";
import { EdgeCandidatesForSeed } from "@/components/seed/EdgeCandidatesForSeed";

const SeedTreeView = dynamic(
  () =>
    import("@/components/seed/SeedTreeView").then((m) => ({
      default: m.SeedTreeView,
    })),
  { ssr: false },
);

const STATUS_COLORS: Record<string, string> = {
  active: "bg-green-500/15 text-green-700 dark:text-green-400",
  promoted: "bg-blue-500/15 text-blue-700 dark:text-blue-400",
  merged: "bg-yellow-500/15 text-yellow-700 dark:text-yellow-400",
  ambiguous: "bg-purple-500/15 text-purple-700 dark:text-purple-400",
};

const TYPE_COLORS: Record<string, string> = {
  entity: "bg-orange-500/15 text-orange-700 dark:text-orange-400",
  concept: "bg-sky-500/15 text-sky-700 dark:text-sky-400",
  event: "bg-rose-500/15 text-rose-700 dark:text-rose-400",
  perspective: "bg-violet-500/15 text-violet-700 dark:text-violet-400",
};

function DisambiguationRoutes({ routes }: { routes: SeedRouteResponse[] }) {
  if (routes.length === 0) return null;
  return (
    <div className="space-y-2">
      {routes.map((route) => (
        <Link
          key={route.child_key}
          href={`/seeds/${encodeURIComponent(route.child_key)}`}
        >
          <Card className="hover:bg-accent/50 transition-colors cursor-pointer">
            <CardContent className="p-3">
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <GitBranch className="size-4 text-purple-500" />
                  <span className="font-medium">{route.child_name}</span>
                  <span className="text-xs text-muted-foreground">
                    ({route.label})
                  </span>
                </div>
                <div className="flex items-center gap-1.5">
                  <Badge
                    variant="secondary"
                    className={STATUS_COLORS[route.child_status] ?? ""}
                  >
                    {route.child_status}
                  </Badge>
                  <Badge variant="outline">
                    {route.child_fact_count} fact
                    {route.child_fact_count !== 1 ? "s" : ""}
                  </Badge>
                </div>
              </div>
            </CardContent>
          </Card>
        </Link>
      ))}
    </div>
  );
}

function MergeHistory({ merges }: { merges: SeedMergeResponse[] }) {
  if (merges.length === 0) {
    return (
      <p className="text-sm text-muted-foreground py-4">
        No merge or split history.
      </p>
    );
  }
  return (
    <div className="space-y-2">
      {merges.map((m, i) => (
        <Card key={i}>
          <CardContent className="p-3">
            <div className="flex items-center gap-2 mb-1">
              {m.operation === "merge" ? (
                <Merge className="size-4 text-yellow-500" />
              ) : (
                <GitBranch className="size-4 text-purple-500" />
              )}
              <Badge variant="secondary">
                {m.operation}
              </Badge>
              <span className="text-xs text-muted-foreground">
                {new Date(m.created_at).toLocaleString()}
              </span>
            </div>
            <div className="text-sm space-y-0.5 ml-6">
              <div>
                <span className="text-muted-foreground">From: </span>
                <Link
                  href={`/seeds/${encodeURIComponent(m.source_seed_key)}`}
                  className="text-primary hover:underline font-mono text-xs"
                >
                  {m.source_seed_key}
                </Link>
              </div>
              <div>
                <span className="text-muted-foreground">To: </span>
                <Link
                  href={`/seeds/${encodeURIComponent(m.target_seed_key)}`}
                  className="text-primary hover:underline font-mono text-xs"
                >
                  {m.target_seed_key}
                </Link>
              </div>
              {m.reason && (
                <div className="text-muted-foreground text-xs mt-1">
                  {m.reason}
                </div>
              )}
              <div className="text-xs text-muted-foreground">
                {m.fact_count_moved} fact{m.fact_count_moved !== 1 ? "s" : ""}{" "}
                moved
              </div>
            </div>
          </CardContent>
        </Card>
      ))}
    </div>
  );
}

function DivergenceCard({ seedKey }: { seedKey: string }) {
  const [data, setData] = useState<SeedDivergenceResponse | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    api.seeds
      .getDivergence(seedKey)
      .then((d) => {
        if (!cancelled) setData(d);
      })
      .catch(() => {
        // Silently fail — metric is informational
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [seedKey]);

  if (loading) {
    return (
      <Card className="mb-4">
        <CardContent className="p-3 flex items-center gap-2 text-xs text-muted-foreground">
          <Loader2 className="size-3 animate-spin" />
          Computing fact divergence...
        </CardContent>
      </Card>
    );
  }

  if (!data || data.vectors_found < 2 || data.mean_pairwise_distance === null) {
    return null;
  }

  const mean = data.mean_pairwise_distance;
  // Color: green (low divergence) → yellow → red (high divergence)
  const divergenceLevel =
    mean < 0.2 ? "low" : mean < 0.4 ? "moderate" : "high";
  const colorClass =
    divergenceLevel === "low"
      ? "border-green-500/30 bg-green-50 dark:bg-green-950/30"
      : divergenceLevel === "moderate"
        ? "border-yellow-500/30 bg-yellow-50 dark:bg-yellow-950/30"
        : "border-red-500/30 bg-red-50 dark:bg-red-950/30";
  const textClass =
    divergenceLevel === "low"
      ? "text-green-700 dark:text-green-400"
      : divergenceLevel === "moderate"
        ? "text-yellow-700 dark:text-yellow-400"
        : "text-red-700 dark:text-red-400";

  return (
    <Card className={`mb-4 ${colorClass}`}>
      <CardContent className="p-3">
        <div className="flex items-center gap-2 mb-2">
          <Activity className={`size-4 ${textClass}`} />
          <span className={`text-sm font-medium ${textClass}`}>
            Fact Divergence: {divergenceLevel}
          </span>
          {data.cluster_estimate > 1 && (
            <Badge variant="outline" className="text-xs">
              ~{data.cluster_estimate} clusters
            </Badge>
          )}
        </div>
        <div className="grid grid-cols-4 gap-3 text-xs">
          <div>
            <div className="text-muted-foreground">Mean distance</div>
            <div className="font-mono font-medium">{mean.toFixed(4)}</div>
          </div>
          <div>
            <div className="text-muted-foreground">Std deviation</div>
            <div className="font-mono font-medium">
              {data.std_pairwise_distance?.toFixed(4) ?? "—"}
            </div>
          </div>
          <div>
            <div className="text-muted-foreground">Min / Max</div>
            <div className="font-mono font-medium">
              {data.min_pairwise_distance?.toFixed(3)} / {data.max_pairwise_distance?.toFixed(3)}
            </div>
          </div>
          <div>
            <div className="text-muted-foreground">Vectors</div>
            <div className="font-mono font-medium">
              {data.vectors_found} / {data.fact_count}
            </div>
          </div>
        </div>
      </CardContent>
    </Card>
  );
}

function FactsList({ facts }: { facts: SeedFactResponse[] }) {
  if (facts.length === 0) {
    return (
      <p className="text-sm text-muted-foreground py-4">No linked facts.</p>
    );
  }

  const mentionedFacts = facts.filter(
    (f) => f.extraction_role !== "source_attribution",
  );
  const sourceFacts = facts.filter(
    (f) => f.extraction_role === "source_attribution",
  );

  return (
    <div className="space-y-4">
      {/* Role summary */}
      <div className="flex items-center gap-3">
        <Badge variant="secondary" className="bg-sky-500/15 text-sky-700 dark:text-sky-400">
          {mentionedFacts.length} about
        </Badge>
        <Badge variant="secondary" className="bg-amber-500/15 text-amber-700 dark:text-amber-400">
          {sourceFacts.length} authored
        </Badge>
      </div>

      {/* About facts */}
      {mentionedFacts.length > 0 && (
        <div className="space-y-1.5">
          {sourceFacts.length > 0 && (
            <h3 className="text-xs font-medium text-muted-foreground uppercase tracking-wide">
              About this entity
            </h3>
          )}
          {mentionedFacts.map((f) => (
            <FactCard key={f.fact_id} fact={f} />
          ))}
        </div>
      )}

      {/* Source attribution facts */}
      {sourceFacts.length > 0 && (
        <div className="space-y-1.5">
          <h3 className="text-xs font-medium text-muted-foreground uppercase tracking-wide">
            Authored / published by this entity
          </h3>
          {sourceFacts.map((f) => (
            <FactCard key={f.fact_id} fact={f} />
          ))}
        </div>
      )}
    </div>
  );
}

function FactCard({ fact: f }: { fact: SeedFactResponse }) {
  return (
    <Card>
      <CardContent className="p-3">
        {f.fact_content && (
          <p className="text-sm mb-1.5">{f.fact_content}</p>
        )}
        <div className="flex items-center justify-between">
          <Link
            href={`/facts?selected=${encodeURIComponent(f.fact_id)}`}
            className="font-mono text-xs text-primary hover:underline truncate flex-1"
          >
            {f.fact_id}
          </Link>
          <div className="flex items-center gap-2 ml-3 shrink-0">
            <Badge variant="outline">
              {(f.confidence * 100).toFixed(0)}%
            </Badge>
            {f.extraction_context && (
              <span className="text-xs text-muted-foreground max-w-[200px] truncate">
                {f.extraction_context}
              </span>
            )}
          </div>
        </div>
      </CardContent>
    </Card>
  );
}

export default function SeedDetailPage({
  params,
}: {
  params: Promise<{ key: string }>;
}) {
  const { key } = use(params);
  const decodedKey = decodeURIComponent(key);
  const { seed, isLoading, error, refetch } = useSeedDetail(decodedKey);
  const router = useRouter();
  const [promoting, setPromoting] = useState(false);
  const [promoteError, setPromoteError] = useState<string | null>(null);

  const handlePromote = useCallback(async () => {
    setPromoting(true);
    setPromoteError(null);
    try {
      const result = await api.seeds.promote(decodedKey);
      if (result.status === "already_promoted" && result.node_id) {
        router.push(`/nodes/${encodeURIComponent(result.node_id)}`);
      } else {
        // Pipeline started — refetch to show updated status
        await refetch();
      }
    } catch (err) {
      setPromoteError(err instanceof Error ? err.message : "Failed to promote seed");
    } finally {
      setPromoting(false);
    }
  }, [decodedKey, router, refetch]);

  return (
    <div className="flex flex-col h-full">
      {/* Header */}
      <div className="px-6 pt-6 pb-4">
        <div className="flex items-center gap-3 mb-2">
          <Button variant="ghost" size="icon" asChild>
            <Link href="/seeds">
              <ArrowLeft className="size-4" />
            </Link>
          </Button>
          <Sprout className="size-5 text-green-600" />
          <h1 className="text-2xl font-bold truncate flex-1">
            {seed?.name ?? decodedKey}
          </h1>
        </div>
      </div>

      {/* Loading */}
      {isLoading && !seed && (
        <div className="flex-1 flex items-center justify-center">
          <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
        </div>
      )}

      {/* Error */}
      {error && (
        <div className="px-6 py-3">
          <p className="text-sm text-destructive">{error}</p>
        </div>
      )}

      {/* Content */}
      {seed && (
        <div className="flex-1 flex flex-col min-h-0 px-6">
          {/* Metadata badges */}
          {(() => {
            const isActive = seed.status === "active" || seed.status === "ambiguous";
            const threshold = seed.promotion_threshold;
            const isPromotable = isActive && seed.fact_count >= threshold;
            const progress = Math.min(100, Math.round((seed.fact_count / threshold) * 100));

            return (
              <div className="flex flex-wrap items-center gap-2 pb-4">
                <Badge
                  variant="secondary"
                  className={TYPE_COLORS[seed.node_type] ?? ""}
                >
                  {seed.node_type}
                  {seed.entity_subtype ? ` / ${seed.entity_subtype}` : ""}
                </Badge>
                <Badge
                  variant="secondary"
                  className={STATUS_COLORS[seed.status] ?? ""}
                >
                  {seed.status}
                </Badge>
                {isPromotable && (
                  <Badge
                    variant="secondary"
                    className="bg-emerald-500/15 text-emerald-700 dark:text-emerald-400"
                  >
                    <Check className="size-3 mr-0.5" />
                    promotable
                  </Badge>
                )}
                <Badge variant="outline">
                  {seed.fact_count} fact{seed.fact_count !== 1 ? "s" : ""}
                </Badge>
                {/* Promotion progress */}
                <div className="flex items-center gap-2">
                  <div className="h-1.5 w-24 rounded-full bg-muted overflow-hidden">
                    <div
                      className={`h-full rounded-full transition-all ${
                        seed.status === "promoted"
                          ? "bg-blue-500"
                          : isPromotable
                            ? "bg-emerald-500"
                            : "bg-muted-foreground/30"
                      }`}
                      style={{ width: `${progress}%` }}
                    />
                  </div>
                  <span className="text-xs text-muted-foreground">
                    {seed.fact_count}/{threshold} to promote
                  </span>
                </div>
                {seed.phonetic_code && (
                  <Badge variant="outline" className="font-mono">
                    {seed.phonetic_code}
                  </Badge>
                )}
                {seed.aliases.length > 0 && (
                  <span className="text-xs text-muted-foreground">
                    Aliases: {seed.aliases.join(", ")}
                  </span>
                )}

                {/* Links to related entities */}
                <div className="flex items-center gap-2 ml-auto">
                  {seed.promoted_node_key && (
                    <Button variant="outline" size="sm" asChild>
                      <Link
                        href={`/nodes/${encodeURIComponent(seed.promoted_node_key)}`}
                      >
                        <ArrowUpRight className="size-3.5 mr-1" />
                        View Node
                      </Link>
                    </Button>
                  )}
                  {!seed.promoted_node_key && isPromotable && (
                    <Button
                      variant="default"
                      size="sm"
                      onClick={handlePromote}
                      disabled={promoting}
                    >
                      {promoting ? (
                        <Loader2 className="size-3.5 mr-1 animate-spin" />
                      ) : (
                        <Rocket className="size-3.5 mr-1" />
                      )}
                      Promote to Node
                    </Button>
                  )}
                  {seed.merged_into_key && (
                    <Button variant="outline" size="sm" asChild>
                      <Link
                        href={`/seeds/${encodeURIComponent(seed.merged_into_key)}`}
                      >
                        <Merge className="size-3.5 mr-1" />
                        Merged into
                      </Link>
                    </Button>
                  )}
                </div>
              </div>
            );
          })()}

          {/* Promote error */}
          {promoteError && (
            <p className="text-sm text-destructive pb-2">{promoteError}</p>
          )}

          {/* Parent seed banner */}
          {seed.parent_seed && (
            <Card className="mb-4 border-purple-500/30">
              <CardContent className="p-3 flex items-center gap-3">
                <GitBranch className="size-4 text-purple-500 shrink-0" />
                <span className="text-sm text-muted-foreground">
                  Disambiguated from:
                </span>
                <Link
                  href={`/seeds/${encodeURIComponent(seed.parent_seed.key)}`}
                  className="text-primary hover:underline font-medium"
                >
                  {seed.parent_seed.name}
                </Link>
                <Badge
                  variant="secondary"
                  className={STATUS_COLORS[seed.parent_seed.status] ?? ""}
                >
                  {seed.parent_seed.status}
                </Badge>
              </CardContent>
            </Card>
          )}

          <Separator />

          {/* Tabs */}
          <Tabs
            defaultValue={seed.routes.length > 0 ? "routes" : "facts"}
            className="flex-1 flex flex-col min-h-0 pt-4"
          >
            <TabsList>
              {seed.routes.length > 0 && (
                <TabsTrigger value="routes">
                  Disambiguation ({seed.routes.length})
                </TabsTrigger>
              )}
              <TabsTrigger value="facts">
                Facts ({seed.facts.length})
                {seed.facts.some((f) => f.extraction_role === "source_attribution") && (
                  <span className="ml-1 text-xs text-muted-foreground">
                    {seed.facts.filter((f) => f.extraction_role !== "source_attribution").length}/
                    {seed.facts.filter((f) => f.extraction_role === "source_attribution").length}
                  </span>
                )}
              </TabsTrigger>
              <TabsTrigger value="history">
                History ({seed.merges.length})
              </TabsTrigger>
              <TabsTrigger value="tree">
                <Network className="size-3.5 mr-1" />
                Tree
              </TabsTrigger>
              <TabsTrigger value="candidates">Candidates</TabsTrigger>
              <TabsTrigger value="info">Info</TabsTrigger>
            </TabsList>

            <div className="flex-1 overflow-y-auto pt-4 pb-6">
              {seed.routes.length > 0 && (
                <TabsContent value="routes">
                  <Card className="mb-4">
                    <CardHeader className="pb-2">
                      <CardTitle className="text-sm font-medium flex items-center gap-2">
                        <GitBranch className="size-4 text-purple-500" />
                        Disambiguation Paths
                      </CardTitle>
                    </CardHeader>
                    <CardContent className="pt-0">
                      <p className="text-xs text-muted-foreground mb-3">
                        This seed was found to be ambiguous. New mentions are
                        routed to the correct disambiguated child based on
                        contextual similarity.
                      </p>
                      <DisambiguationRoutes routes={seed.routes} />
                    </CardContent>
                  </Card>
                </TabsContent>
              )}

              <TabsContent value="facts">
                <DivergenceCard seedKey={decodedKey} />
                <FactsList facts={seed.facts} />
              </TabsContent>

              <TabsContent value="history">
                <MergeHistory merges={seed.merges} />
              </TabsContent>

              <TabsContent value="tree">
                <SeedTreeView seedKey={decodedKey} />
              </TabsContent>

              <TabsContent value="candidates">
                <EdgeCandidatesForSeed seedKey={decodedKey} />
              </TabsContent>

              <TabsContent value="info">
                <Card>
                  <CardContent className="p-4 space-y-2 text-sm">
                    <div className="grid grid-cols-[140px_1fr] gap-y-2">
                      <span className="text-muted-foreground">Key</span>
                      <span className="font-mono text-xs">{seed.key}</span>

                      <span className="text-muted-foreground">UUID</span>
                      <span className="font-mono text-xs">
                        {seed.seed_uuid}
                      </span>

                      <span className="text-muted-foreground">Node Type</span>
                      <span>{seed.node_type}</span>

                      {seed.entity_subtype && (
                        <>
                          <span className="text-muted-foreground">
                            Entity Subtype
                          </span>
                          <span>{seed.entity_subtype}</span>
                        </>
                      )}

                      <span className="text-muted-foreground">Status</span>
                      <span>{seed.status}</span>

                      <span className="text-muted-foreground">Fact Count</span>
                      <span>{seed.fact_count}</span>

                      {seed.phonetic_code && (
                        <>
                          <span className="text-muted-foreground">
                            Phonetic Code
                          </span>
                          <span className="font-mono">
                            {seed.phonetic_code}
                          </span>
                        </>
                      )}

                      {seed.aliases.length > 0 && (
                        <>
                          <span className="text-muted-foreground">Aliases</span>
                          <span>{seed.aliases.join(", ")}</span>
                        </>
                      )}

                      <span className="text-muted-foreground">Created</span>
                      <span>
                        {new Date(seed.created_at).toLocaleString()}
                      </span>

                      <span className="text-muted-foreground">Updated</span>
                      <span>
                        {new Date(seed.updated_at).toLocaleString()}
                      </span>
                    </div>
                  </CardContent>
                </Card>
              </TabsContent>
            </div>
          </Tabs>
        </div>
      )}
    </div>
  );
}
