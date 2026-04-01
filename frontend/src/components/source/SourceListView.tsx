"use client";

import { Loader2, Search, ExternalLink, Check, ChevronsUpDown, AlertTriangle, ArrowUpDown, FileWarning } from "lucide-react";
import { useRouter } from "next/navigation";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { ScrollArea } from "@/components/ui/scroll-area";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { useSourceList } from "@/hooks/useSourceList";
import type { SourceResponse } from "@/types";

const KNOWN_PROVIDERS = ["serper", "brave", "upload", "url_fetch"];

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
  });
}

function truncateUri(uri: string, maxLen = 80): string {
  if (uri.length <= maxLen) return uri;
  return uri.slice(0, maxLen) + "...";
}

const PAGE_SIZE = 20;

export function SourceListView() {
  const {
    sources,
    total,
    offset,
    search,
    providerId,
    sortBy,
    hasProhibited,
    isSuperSource,
    fetchStatus,
    isLoading,
    error,
    setSearch,
    setProviderId,
    setSortBy,
    setHasProhibited,
    setIsSuperSource,
    setFetchStatus,
    setPage,
  } = useSourceList();

  const router = useRouter();

  const currentPage = Math.floor(offset / PAGE_SIZE);
  const totalPages = Math.ceil(total / PAGE_SIZE);
  const showingStart = total > 0 ? offset + 1 : 0;
  const showingEnd = Math.min(offset + PAGE_SIZE, total);

  return (
    <div className="flex flex-col h-full relative">
      {/* Search + filter + count */}
      <div className="flex items-center gap-3 p-4 border-b">
        <div className="relative flex-1">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 size-4 text-muted-foreground" />
          <Input
            placeholder="Search by title or URL..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="pl-9"
          />
        </div>
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button variant="outline" className="gap-1 shrink-0">
              {providerId ?? "All providers"}
              <ChevronsUpDown className="size-3.5 opacity-50" />
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end">
            <DropdownMenuItem onClick={() => setProviderId(null)}>
              {!providerId && <Check className="mr-2 size-4" />}
              <span className={providerId ? "ml-6" : ""}>All providers</span>
            </DropdownMenuItem>
            {KNOWN_PROVIDERS.map((p) => (
              <DropdownMenuItem key={p} onClick={() => setProviderId(p)}>
                {providerId === p && <Check className="mr-2 size-4" />}
                <span className={providerId !== p ? "ml-6" : ""}>{p}</span>
              </DropdownMenuItem>
            ))}
          </DropdownMenuContent>
        </DropdownMenu>
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button variant="outline" className="gap-1 shrink-0">
              {fetchStatus === "full_text"
                ? "Full text"
                : fetchStatus === "fetch_failed"
                  ? "Fetch failed"
                  : fetchStatus === "snippet"
                    ? "Snippet only"
                    : "All statuses"}
              <ChevronsUpDown className="size-3.5 opacity-50" />
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end">
            <DropdownMenuItem onClick={() => setFetchStatus(null)}>
              {!fetchStatus && <Check className="mr-2 size-4" />}
              <span className={fetchStatus ? "ml-6" : ""}>All statuses</span>
            </DropdownMenuItem>
            <DropdownMenuItem onClick={() => setFetchStatus("full_text")}>
              {fetchStatus === "full_text" && <Check className="mr-2 size-4" />}
              <span className={fetchStatus !== "full_text" ? "ml-6" : ""}>Full text</span>
            </DropdownMenuItem>
            <DropdownMenuItem onClick={() => setFetchStatus("fetch_failed")}>
              {fetchStatus === "fetch_failed" && <Check className="mr-2 size-4" />}
              <span className={fetchStatus !== "fetch_failed" ? "ml-6" : ""}>Fetch failed</span>
            </DropdownMenuItem>
            <DropdownMenuItem onClick={() => setFetchStatus("snippet")}>
              {fetchStatus === "snippet" && <Check className="mr-2 size-4" />}
              <span className={fetchStatus !== "snippet" ? "ml-6" : ""}>Snippet only</span>
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button variant="outline" className="gap-1 shrink-0">
              <ArrowUpDown className="size-3.5 opacity-50" />
              {sortBy === "fact_count"
                ? "Most facts"
                : sortBy === "prohibited_chunks"
                  ? "Most prohibited"
                  : "Most recent"}
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end">
            <DropdownMenuItem onClick={() => setSortBy(null)}>
              {!sortBy && <Check className="mr-2 size-4" />}
              <span className={sortBy ? "ml-6" : ""}>Most recent</span>
            </DropdownMenuItem>
            <DropdownMenuItem onClick={() => setSortBy("fact_count")}>
              {sortBy === "fact_count" && <Check className="mr-2 size-4" />}
              <span className={sortBy !== "fact_count" ? "ml-6" : ""}>Most facts</span>
            </DropdownMenuItem>
            <DropdownMenuItem onClick={() => setSortBy("prohibited_chunks")}>
              {sortBy === "prohibited_chunks" && <Check className="mr-2 size-4" />}
              <span className={sortBy !== "prohibited_chunks" ? "ml-6" : ""}>Most prohibited</span>
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
        <Button
          variant={hasProhibited ? "default" : "outline"}
          size="sm"
          className="gap-1 shrink-0"
          onClick={() => setHasProhibited(hasProhibited ? null : true)}
        >
          <AlertTriangle className="size-3.5" />
          Prohibited
        </Button>
        <Button
          variant={isSuperSource ? "default" : "outline"}
          size="sm"
          className="gap-1 shrink-0"
          onClick={() => setIsSuperSource(isSuperSource ? null : true)}
        >
          <FileWarning className="size-3.5" />
          Super
        </Button>
        <Badge variant="secondary">{total} sources</Badge>
      </div>

      {/* Error */}
      {error && (
        <div className="px-4 py-2 text-sm text-destructive">{error}</div>
      )}

      {/* Loading */}
      {isLoading && (
        <div className="flex items-center justify-center py-8">
          <Loader2 className="size-6 animate-spin text-muted-foreground" />
        </div>
      )}

      {/* List */}
      <ScrollArea className="flex-1">
        <div className="p-4 space-y-2">
          {!isLoading && sources.length === 0 && (
            <p className="text-center text-sm text-muted-foreground py-8">
              No sources found
            </p>
          )}
          {sources.map((source: SourceResponse) => (
            <Card
              key={source.id}
              className="p-4 cursor-pointer hover:bg-accent/30 transition-colors"
              onClick={() => router.push(`/sources/${source.id}`)}
            >
              <div className="flex items-start justify-between gap-3">
                <div className="flex-1 min-w-0">
                  <p className="text-sm font-medium truncate">
                    {source.title || truncateUri(source.uri)}
                  </p>
                  {source.title && (
                    <p className="text-xs text-muted-foreground truncate mt-0.5">
                      {truncateUri(source.uri)}
                    </p>
                  )}
                  <div className="flex items-center gap-2 mt-2">
                    <Badge
                      variant="secondary"
                      className={
                        PROVIDER_COLORS[source.provider_id] ?? ""
                      }
                    >
                      {source.provider_id}
                    </Badge>
                    <span className="text-xs text-muted-foreground">
                      {formatDate(source.retrieved_at)}
                    </span>
                    <Badge
                      variant="outline"
                      className={`text-xs ${source.fact_count === 0 ? "text-red-400 border-red-400/30" : ""}`}
                    >
                      {source.fact_count} fact{source.fact_count !== 1 ? "s" : ""}
                    </Badge>
                    {source.prohibited_chunk_count > 0 && (
                      <Badge
                        variant="outline"
                        className="text-xs text-amber-400 border-amber-400/30"
                      >
                        <AlertTriangle className="size-3 mr-1" />
                        {source.prohibited_chunk_count} prohibited
                      </Badge>
                    )}
                    {source.is_super_source && (
                      <Badge
                        variant="outline"
                        className="text-xs text-orange-400 border-orange-400/30"
                      >
                        <FileWarning className="size-3 mr-1" />
                        super source
                      </Badge>
                    )}
                    {source.is_full_text ? (
                      <Badge
                        variant="outline"
                        className="text-xs text-green-400 border-green-400/30"
                      >
                        full text
                      </Badge>
                    ) : source.fetch_attempted ? (
                      <Badge
                        variant="outline"
                        className="text-xs text-red-400 border-red-400/30"
                        title={source.fetch_error ?? "Content could not be fetched"}
                      >
                        fetch failed
                      </Badge>
                    ) : (
                      <Badge
                        variant="outline"
                        className="text-xs text-zinc-500 border-zinc-500/30"
                      >
                        snippet
                      </Badge>
                    )}
                  </div>
                </div>
                <Button
                  variant="ghost"
                  size="icon-sm"
                  asChild
                  onClick={(e: React.MouseEvent) => e.stopPropagation()}
                >
                  <a
                    href={source.uri}
                    target="_blank"
                    rel="noopener noreferrer"
                  >
                    <ExternalLink className="size-3.5" />
                  </a>
                </Button>
              </div>
            </Card>
          ))}
        </div>
      </ScrollArea>

      {/* Pagination */}
      {totalPages > 1 && (
        <div className="flex items-center justify-between px-4 py-3 border-t">
          <span className="text-xs text-muted-foreground">
            Showing {showingStart}-{showingEnd} of {total}
          </span>
          <div className="flex items-center gap-2">
            <Button
              variant="outline"
              size="sm"
              disabled={currentPage === 0}
              onClick={() => setPage(currentPage - 1)}
            >
              Previous
            </Button>
            <Button
              variant="outline"
              size="sm"
              disabled={currentPage >= totalPages - 1}
              onClick={() => setPage(currentPage + 1)}
            >
              Next
            </Button>
          </div>
        </div>
      )}
    </div>
  );
}
