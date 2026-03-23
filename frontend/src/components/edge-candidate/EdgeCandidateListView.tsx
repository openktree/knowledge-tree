"use client";

import { useState } from "react";
import Link from "next/link";
import { Loader2, Search, GitPullRequestArrow } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { useEdgeCandidateList } from "@/hooks/useEdgeCandidates";
import type { EdgeCandidatePairSummary } from "@/types";

function PairCard({ pair }: { pair: EdgeCandidatePairSummary }) {
  return (
    <Link
      href={`/edge-candidates/${encodeURIComponent(pair.seed_key_a)}/${encodeURIComponent(pair.seed_key_b)}`}
    >
      <Card className="hover:bg-accent/50 transition-colors cursor-pointer">
        <CardContent className="p-4">
          <div className="flex items-center justify-between mb-2">
            <div className="flex items-center gap-2 min-w-0 flex-1">
              <GitPullRequestArrow className="size-4 text-muted-foreground shrink-0" />
              <span className="font-medium truncate">
                {pair.seed_name_a ?? pair.seed_key_a}
              </span>
              <span className="text-muted-foreground shrink-0">↔</span>
              <span className="font-medium truncate">
                {pair.seed_name_b ?? pair.seed_key_b}
              </span>
            </div>
            <Badge variant="outline" className="shrink-0 ml-2">
              {pair.total_count} fact{pair.total_count !== 1 ? "s" : ""}
            </Badge>
          </div>
          <div className="flex items-center gap-2">
            {pair.pending_count > 0 && (
              <Badge variant="secondary" className="bg-yellow-500/15 text-yellow-700 dark:text-yellow-400">
                {pair.pending_count} pending
              </Badge>
            )}
            {pair.accepted_count > 0 && (
              <Badge variant="secondary" className="bg-green-500/15 text-green-700 dark:text-green-400">
                {pair.accepted_count} accepted
              </Badge>
            )}
            {pair.rejected_count > 0 && (
              <Badge variant="secondary" className="bg-red-500/15 text-red-700 dark:text-red-400">
                {pair.rejected_count} rejected
              </Badge>
            )}
            {pair.latest_evaluated_at && (
              <span className="text-xs text-muted-foreground ml-auto">
                Evaluated {new Date(pair.latest_evaluated_at).toLocaleDateString()}
              </span>
            )}
          </div>
        </CardContent>
      </Card>
    </Link>
  );
}

export function EdgeCandidateListView() {
  const [page, setPage] = useState(0);
  const [search, setSearch] = useState("");
  const [statusFilter, setStatusFilter] = useState<string | undefined>(undefined);
  const limit = 20;

  const { items, total, isLoading, error } = useEdgeCandidateList({
    offset: page * limit,
    limit,
    search: search || undefined,
    status: statusFilter,
  });

  const totalPages = Math.ceil(total / limit);

  return (
    <div className="flex flex-col h-full">
      <div className="px-6 pt-6 pb-4">
        <div className="flex items-center gap-3 mb-4">
          <GitPullRequestArrow className="size-5 text-primary" />
          <h1 className="text-2xl font-bold">Edge Candidates</h1>
          <span className="text-sm text-muted-foreground">({total})</span>
        </div>

        <div className="flex items-center gap-3">
          <div className="relative flex-1 max-w-sm">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 size-4 text-muted-foreground" />
            <Input
              placeholder="Search by seed name..."
              value={search}
              onChange={(e) => {
                setSearch(e.target.value);
                setPage(0);
              }}
              className="pl-9"
            />
          </div>
          <Select
            value={statusFilter ?? "all"}
            onValueChange={(val) => {
              setStatusFilter(val === "all" ? undefined : val);
              setPage(0);
            }}
          >
            <SelectTrigger className="w-[140px]">
              <SelectValue placeholder="All statuses" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">All statuses</SelectItem>
              <SelectItem value="pending">Pending</SelectItem>
              <SelectItem value="accepted">Accepted</SelectItem>
              <SelectItem value="rejected">Rejected</SelectItem>
            </SelectContent>
          </Select>
        </div>
      </div>

      <div className="flex-1 overflow-y-auto px-6 pb-6">
        {isLoading && items.length === 0 && (
          <div className="flex items-center justify-center py-12">
            <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
          </div>
        )}

        {error && (
          <p className="text-sm text-destructive py-4">{error}</p>
        )}

        {!isLoading && items.length === 0 && !error && (
          <p className="text-sm text-muted-foreground py-12 text-center">
            No edge candidates found.
          </p>
        )}

        <div className="space-y-2">
          {items.map((pair) => (
            <PairCard
              key={`${pair.seed_key_a}:${pair.seed_key_b}`}
              pair={pair}
            />
          ))}
        </div>

        {totalPages > 1 && (
          <div className="flex items-center justify-center gap-2 pt-4">
            <Button
              variant="outline"
              size="sm"
              disabled={page === 0}
              onClick={() => setPage(page - 1)}
            >
              Previous
            </Button>
            <span className="text-sm text-muted-foreground">
              Page {page + 1} of {totalPages}
            </span>
            <Button
              variant="outline"
              size="sm"
              disabled={page >= totalPages - 1}
              onClick={() => setPage(page + 1)}
            >
              Next
            </Button>
          </div>
        )}
      </div>
    </div>
  );
}
