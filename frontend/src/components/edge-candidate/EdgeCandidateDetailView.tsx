"use client";

import Link from "next/link";
import { ArrowLeft, Loader2, GitPullRequestArrow } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Separator } from "@/components/ui/separator";
import { useEdgeCandidateDetail } from "@/hooks/useEdgeCandidates";

const STATUS_COLORS: Record<string, string> = {
  pending: "bg-yellow-500/15 text-yellow-700 dark:text-yellow-400",
  accepted: "bg-green-500/15 text-green-700 dark:text-green-400",
  rejected: "bg-red-500/15 text-red-700 dark:text-red-400",
};

export function EdgeCandidateDetailView({
  seedKeyA,
  seedKeyB,
}: {
  seedKeyA: string;
  seedKeyB: string;
}) {
  const { detail, isLoading, error } = useEdgeCandidateDetail(seedKeyA, seedKeyB);

  return (
    <div className="flex flex-col h-full">
      <div className="px-6 pt-6 pb-4">
        <div className="flex items-center gap-3 mb-2">
          <Button variant="ghost" size="icon" asChild>
            <Link href="/edge-candidates">
              <ArrowLeft className="size-4" />
            </Link>
          </Button>
          <GitPullRequestArrow className="size-5 text-primary" />
          <h1 className="text-2xl font-bold truncate">Candidate Pair</h1>
        </div>
      </div>

      {isLoading && !detail && (
        <div className="flex-1 flex items-center justify-center">
          <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
        </div>
      )}

      {error && (
        <div className="px-6 py-3">
          <p className="text-sm text-destructive">{error}</p>
        </div>
      )}

      {detail && (
        <div className="flex-1 flex flex-col min-h-0 px-6">
          <div className="flex items-center gap-3 pb-4 flex-wrap">
            <Link
              href={`/seeds/${encodeURIComponent(detail.seed_key_a)}`}
              className="text-primary hover:underline font-medium"
            >
              {detail.seed_name_a ?? detail.seed_key_a}
            </Link>
            <span className="text-muted-foreground">↔</span>
            <Link
              href={`/seeds/${encodeURIComponent(detail.seed_key_b)}`}
              className="text-primary hover:underline font-medium"
            >
              {detail.seed_name_b ?? detail.seed_key_b}
            </Link>

            <div className="flex items-center gap-2 ml-auto">
              {detail.pending_count > 0 && (
                <Badge variant="secondary" className={STATUS_COLORS.pending}>
                  {detail.pending_count} pending
                </Badge>
              )}
              {detail.accepted_count > 0 && (
                <Badge variant="secondary" className={STATUS_COLORS.accepted}>
                  {detail.accepted_count} accepted
                </Badge>
              )}
              {detail.rejected_count > 0 && (
                <Badge variant="secondary" className={STATUS_COLORS.rejected}>
                  {detail.rejected_count} rejected
                </Badge>
              )}
            </div>
          </div>

          <Separator />

          <div className="flex-1 overflow-y-auto pt-4 pb-6 space-y-2">
            {detail.facts.length === 0 && (
              <p className="text-sm text-muted-foreground py-4">
                No candidate facts found.
              </p>
            )}
            {detail.facts.map((f) => (
              <Card key={f.id}>
                <CardContent className="p-3">
                  <div className="flex items-center justify-between">
                    <div className="flex-1 min-w-0">
                      {f.fact_content && (
                        <p className="text-sm mb-1">{f.fact_content}</p>
                      )}
                      <Link
                        href={`/facts?selected=${encodeURIComponent(f.fact_id)}`}
                        className="font-mono text-xs text-primary hover:underline"
                      >
                        {f.fact_id}
                      </Link>
                    </div>
                    <div className="flex items-center gap-2 ml-3 shrink-0">
                      <Badge
                        variant="secondary"
                        className={STATUS_COLORS[f.status] ?? ""}
                      >
                        {f.status}
                      </Badge>
                      {f.discovery_strategy && (
                        <Badge variant="outline" className="text-xs">
                          {f.discovery_strategy}
                        </Badge>
                      )}
                      {f.last_evaluated_at && (
                        <span className="text-xs text-muted-foreground">
                          {new Date(f.last_evaluated_at).toLocaleDateString()}
                        </span>
                      )}
                      <span className="text-xs text-muted-foreground">
                        {new Date(f.created_at).toLocaleDateString()}
                      </span>
                    </div>
                  </div>
                  {f.evaluation_result && (
                    <pre className="text-xs text-muted-foreground mt-2 bg-muted/50 p-2 rounded overflow-x-auto">
                      {JSON.stringify(f.evaluation_result, null, 2)}
                    </pre>
                  )}
                </CardContent>
              </Card>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
