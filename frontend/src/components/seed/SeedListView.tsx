"use client";

import { useState, useCallback } from "react";
import { useRouter } from "next/navigation";
import { Search, Loader2, ChevronLeft, ChevronRight, Sprout, Rocket, Check } from "lucide-react";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { ScrollArea } from "@/components/ui/scroll-area";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { useSeedList } from "@/hooks/useSeedList";
import { api } from "@/lib/api";

const PAGE_SIZE = 20;

const STATUS_COLORS: Record<string, string> = {
  pending: "bg-slate-500/15 text-slate-700 dark:text-slate-400",
  active: "bg-green-500/15 text-green-700 dark:text-green-400",
  promoted: "bg-blue-500/15 text-blue-700 dark:text-blue-400",
  merged: "bg-yellow-500/15 text-yellow-700 dark:text-yellow-400",
  ambiguous: "bg-purple-500/15 text-purple-700 dark:text-purple-400",
  garbage: "bg-red-500/15 text-red-700 dark:text-red-400",
};

const TYPE_COLORS: Record<string, string> = {
  entity: "bg-orange-500/15 text-orange-700 dark:text-orange-400",
  concept: "bg-sky-500/15 text-sky-700 dark:text-sky-400",
  event: "bg-rose-500/15 text-rose-700 dark:text-rose-400",
  perspective: "bg-violet-500/15 text-violet-700 dark:text-violet-400",
};

export function SeedListView() {
  const router = useRouter();
  const {
    seeds,
    total,
    offset,
    search,
    status,
    nodeType,
    promotionThreshold,
    isLoading,
    error,
    setSearch,
    setStatus,
    setNodeType,
    setPage,
  } = useSeedList();

  const [promoting, setPromoting] = useState(false);
  const [promoteResult, setPromoteResult] = useState<string | null>(null);

  const handleAutoBuild = useCallback(async () => {
    setPromoting(true);
    setPromoteResult(null);
    try {
      await api.graphBuilder.autoBuild();
      setPromoteResult("Auto-build started — seeds will be promoted to stub nodes.");
      // Refresh the list after a short delay to show updated statuses
      setTimeout(() => {
        setPage(0);
      }, 3000);
    } catch (err) {
      setPromoteResult(
        err instanceof Error ? err.message : "Failed to start auto-build",
      );
    } finally {
      setPromoting(false);
    }
  }, [setPage]);

  const currentPage = Math.floor(offset / PAGE_SIZE);
  const totalPages = Math.ceil(total / PAGE_SIZE);

  return (
    <div className="flex flex-col h-full px-6">
      {/* Toolbar */}
      <div className="flex items-center gap-3 pb-3">
        <div className="relative flex-1 max-w-sm">
          <Search className="absolute left-2.5 top-2.5 size-4 text-muted-foreground" />
          <Input
            placeholder="Search seeds..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="pl-9"
          />
        </div>

        {/* Status filter */}
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button variant="outline" size="sm">
              {status ?? "All statuses"}
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent>
            <DropdownMenuItem onClick={() => setStatus(null)}>
              All statuses
            </DropdownMenuItem>
            {["pending", "active", "promoted", "merged", "ambiguous", "garbage", "promotable"].map((s) => (
              <DropdownMenuItem key={s} onClick={() => setStatus(s)}>
                {s}
              </DropdownMenuItem>
            ))}
          </DropdownMenuContent>
        </DropdownMenu>

        {/* Node type filter */}
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button variant="outline" size="sm">
              {nodeType ?? "All types"}
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent>
            <DropdownMenuItem onClick={() => setNodeType(null)}>
              All types
            </DropdownMenuItem>
            {["entity", "concept", "event", "perspective"].map((t) => (
              <DropdownMenuItem key={t} onClick={() => setNodeType(t)}>
                {t}
              </DropdownMenuItem>
            ))}
          </DropdownMenuContent>
        </DropdownMenu>

        <Button
          variant="outline"
          size="sm"
          onClick={handleAutoBuild}
          disabled={promoting}
          className="ml-auto gap-1.5"
        >
          {promoting ? (
            <Loader2 className="size-3.5 animate-spin" />
          ) : (
            <Rocket className="size-3.5" />
          )}
          Promote Seeds
        </Button>

        <span className="text-xs text-muted-foreground">
          {total} seed{total !== 1 ? "s" : ""}
        </span>
      </div>

      {promoteResult && (
        <div className="pb-3">
          <p className="text-xs text-muted-foreground bg-muted rounded-md px-3 py-2">
            {promoteResult}
          </p>
        </div>
      )}

      {/* Loading / Error */}
      {isLoading && (
        <div className="flex items-center justify-center py-12">
          <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
        </div>
      )}
      {error && (
        <p className="text-sm text-destructive py-4">{error}</p>
      )}

      {/* List */}
      {!isLoading && !error && (
        <ScrollArea className="flex-1 min-h-0">
          {seeds.length === 0 ? (
            <div className="flex flex-col items-center justify-center py-16 text-muted-foreground">
              <Sprout className="size-10 mb-3 opacity-40" />
              <p className="text-sm">No seeds found</p>
            </div>
          ) : (
            <div className="space-y-2 pb-4">
              {seeds.map((seed) => {
                const isActive = seed.status === "active" || seed.status === "ambiguous";
                const isPromotable = isActive && seed.fact_count >= promotionThreshold;
                const progress = Math.min(100, Math.round((seed.fact_count / promotionThreshold) * 100));

                return (
                  <Card
                    key={seed.key}
                    className={`p-3 cursor-pointer hover:bg-accent/50 transition-colors ${
                      isPromotable ? "border-emerald-500/40" : ""
                    }`}
                    onClick={() =>
                      router.push(`/seeds/${encodeURIComponent(seed.key)}`)
                    }
                  >
                    <div className="flex items-center justify-between gap-3">
                      <div className="flex-1 min-w-0">
                        <div className="flex items-center gap-2">
                          <span className="font-medium truncate">
                            {seed.name}
                          </span>
                          {seed.aliases.length > 0 && (
                            <span className="text-xs text-muted-foreground truncate">
                              aka {seed.aliases.slice(0, 2).join(", ")}
                              {seed.aliases.length > 2 && "..."}
                            </span>
                          )}
                        </div>
                        <div className="text-xs text-muted-foreground mt-0.5 font-mono truncate">
                          {seed.key}
                        </div>
                        {/* Promotion progress bar */}
                        <div className="flex items-center gap-2 mt-1.5">
                          <div className="h-1.5 flex-1 max-w-[120px] rounded-full bg-muted overflow-hidden">
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
                          <span className="text-[10px] text-muted-foreground">
                            {seed.fact_count}/{promotionThreshold}
                            </span>
                          </div>
                      </div>
                      <div className="flex items-center gap-1.5 shrink-0">
                        {isPromotable && (
                          <Badge
                            variant="secondary"
                            className="bg-emerald-500/15 text-emerald-700 dark:text-emerald-400"
                          >
                            <Check className="size-3 mr-0.5" />
                            promotable
                          </Badge>
                        )}
                        <Badge
                          variant="secondary"
                          className={
                            TYPE_COLORS[seed.node_type] ?? ""
                          }
                        >
                          {seed.node_type}
                        </Badge>
                        <Badge
                          variant="secondary"
                          className={
                            STATUS_COLORS[seed.status] ?? ""
                          }
                        >
                          {seed.status}
                        </Badge>
                        <Badge variant="outline">
                          {seed.fact_count} fact{seed.fact_count !== 1 ? "s" : ""}
                          {seed.source_fact_count > 0 && (
                            <span className="ml-1 text-muted-foreground">
                              ({seed.fact_count - seed.source_fact_count}
                              <span className="text-sky-600 dark:text-sky-400" title="Facts about this entity"> about</span>
                              {" / "}
                              {seed.source_fact_count}
                              <span className="text-amber-600 dark:text-amber-400" title="Facts authored by this entity"> authored</span>)
                            </span>
                          )}
                        </Badge>
                      </div>
                    </div>
                  </Card>
                );
              })}
            </div>
          )}
        </ScrollArea>
      )}

      {/* Pagination */}
      {totalPages > 1 && (
        <div className="flex items-center justify-between border-t pt-3 pb-2">
          <Button
            variant="outline"
            size="sm"
            disabled={currentPage === 0}
            onClick={() => setPage(currentPage - 1)}
          >
            <ChevronLeft className="size-4 mr-1" />
            Previous
          </Button>
          <span className="text-xs text-muted-foreground">
            Page {currentPage + 1} of {totalPages}
          </span>
          <Button
            variant="outline"
            size="sm"
            disabled={currentPage >= totalPages - 1}
            onClick={() => setPage(currentPage + 1)}
          >
            Next
            <ChevronRight className="size-4 ml-1" />
          </Button>
        </div>
      )}
    </div>
  );
}
