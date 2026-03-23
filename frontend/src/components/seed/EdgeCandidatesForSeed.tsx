"use client";

import Link from "next/link";
import { Loader2, GitPullRequestArrow } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
import { useSeedEdgeCandidates } from "@/hooks/useEdgeCandidates";
import type { EdgeCandidatePairSummary } from "@/types";

function PartnerName({
  pair,
  seedKey,
}: {
  pair: EdgeCandidatePairSummary;
  seedKey: string;
}) {
  const isA = pair.seed_key_a === seedKey;
  const partnerKey = isA ? pair.seed_key_b : pair.seed_key_a;
  const partnerName = isA ? pair.seed_name_b : pair.seed_name_a;
  return (
    <span className="font-medium truncate">{partnerName ?? partnerKey}</span>
  );
}

export function EdgeCandidatesForSeed({ seedKey }: { seedKey: string }) {
  const { items, total, isLoading, error } = useSeedEdgeCandidates(seedKey);

  if (isLoading) {
    return (
      <div className="flex items-center justify-center py-8">
        <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
      </div>
    );
  }

  if (error) {
    return <p className="text-sm text-destructive py-4">{error}</p>;
  }

  if (items.length === 0) {
    return (
      <p className="text-sm text-muted-foreground py-4">
        No edge candidates for this seed.
      </p>
    );
  }

  return (
    <div className="space-y-2">
      <p className="text-sm text-muted-foreground mb-3">
        {total} candidate pair{total !== 1 ? "s" : ""} found
      </p>
      {items.map((pair) => (
        <Link
          key={`${pair.seed_key_a}:${pair.seed_key_b}`}
          href={`/edge-candidates/${encodeURIComponent(pair.seed_key_a)}/${encodeURIComponent(pair.seed_key_b)}`}
        >
          <Card className="hover:bg-accent/50 transition-colors cursor-pointer">
            <CardContent className="p-3">
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2 min-w-0 flex-1">
                  <GitPullRequestArrow className="size-4 text-muted-foreground shrink-0" />
                  <PartnerName pair={pair} seedKey={seedKey} />
                </div>
                <div className="flex items-center gap-1.5 ml-2 shrink-0">
                  {pair.pending_count > 0 && (
                    <Badge variant="secondary" className="bg-yellow-500/15 text-yellow-700 dark:text-yellow-400">
                      {pair.pending_count}
                    </Badge>
                  )}
                  {pair.accepted_count > 0 && (
                    <Badge variant="secondary" className="bg-green-500/15 text-green-700 dark:text-green-400">
                      {pair.accepted_count}
                    </Badge>
                  )}
                  {pair.rejected_count > 0 && (
                    <Badge variant="secondary" className="bg-red-500/15 text-red-700 dark:text-red-400">
                      {pair.rejected_count}
                    </Badge>
                  )}
                  <Badge variant="outline">
                    {pair.total_count}
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
