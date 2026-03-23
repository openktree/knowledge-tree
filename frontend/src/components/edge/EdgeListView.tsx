"use client";

import { useCallback, useState } from "react";
import { useSearchParams, useRouter, usePathname } from "next/navigation";
import { Trash2, Loader2, Search, Check, ChevronsUpDown, ArrowRight, FileText } from "lucide-react";
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
import { useEdgeList } from "@/hooks/useEdgeList";
import { EdgeDetailPanel } from "@/components/edge/EdgeDetailPanel";
import { DeleteConfirmDialog } from "@/components/shared/DeleteConfirmDialog";
import { api } from "@/lib/api";
import { EdgeType } from "@/types";
import type { EdgeResponse } from "@/types";
import { cn } from "@/lib/utils";

const EDGE_TYPES = Object.values(EdgeType);

function formatDate(dateStr: string): string {
  return new Date(dateStr).toLocaleDateString("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
  });
}

const RELATIONSHIP_COLORS: Record<string, string> = {
  related: "bg-gray-100 text-gray-800 dark:bg-gray-900 dark:text-gray-200",
};

function truncateId(id: string): string {
  return id.length > 8 ? `${id.slice(0, 8)}...` : id;
}

const PAGE_SIZE = 20;

export function EdgeListView() {
  const {
    edges,
    total,
    offset,
    search,
    relationshipType,
    isLoading,
    error,
    setSearch,
    setRelationshipType,
    setPage,
    refresh,
  } = useEdgeList();

  const [deleteEdge, setDeleteEdge] = useState<EdgeResponse | null>(null);
  const searchParams = useSearchParams();
  const router = useRouter();
  const pathname = usePathname();
  const selectedEdgeId = searchParams.get("selected");

  const setSelectedEdgeId = useCallback((id: string | null) => {
    const params = new URLSearchParams(searchParams.toString());
    if (id) {
      params.set("selected", id);
    } else {
      params.delete("selected");
    }
    const qs = params.toString();
    router.replace(qs ? `${pathname}?${qs}` : pathname, { scroll: false });
  }, [router, searchParams, pathname]);

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
            placeholder="Search by justification..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="pl-9"
          />
        </div>
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button variant="outline" className="gap-1 shrink-0">
              {relationshipType?.replace(/_/g, " ") ?? "All types"}
              <ChevronsUpDown className="size-3.5 opacity-50" />
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end" className="max-h-64 overflow-y-auto">
            <DropdownMenuItem onClick={() => setRelationshipType(null)}>
              {!relationshipType && <Check className="mr-2 size-4" />}
              <span className={relationshipType ? "ml-6" : ""}>All types</span>
            </DropdownMenuItem>
            {EDGE_TYPES.map((type) => (
              <DropdownMenuItem
                key={type}
                onClick={() => setRelationshipType(type)}
              >
                {relationshipType === type && <Check className="mr-2 size-4" />}
                <span className={relationshipType !== type ? "ml-6" : ""}>
                  {type.replace(/_/g, " ")}
                </span>
              </DropdownMenuItem>
            ))}
          </DropdownMenuContent>
        </DropdownMenu>
        <Badge variant="secondary">{total} edges</Badge>
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
          {!isLoading && edges.length === 0 && (
            <p className="text-center text-sm text-muted-foreground py-8">
              No edges found
            </p>
          )}
          {edges.map((edge) => (
            <Card
              key={edge.id}
              className="p-4 cursor-pointer hover:bg-accent/30 transition-colors"
              onClick={() => setSelectedEdgeId(edge.id)}
            >
              <div className="flex items-start justify-between gap-3">
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2 mb-1.5">
                    <Badge
                      variant="secondary"
                      className={cn(
                        "text-xs capitalize",
                        RELATIONSHIP_COLORS[edge.relationship_type] ?? "",
                      )}
                    >
                      {edge.relationship_type.replace(/_/g, " ")}
                    </Badge>
                    <span className="text-xs text-muted-foreground">
                      {edge.supporting_fact_ids.length} {edge.supporting_fact_ids.length === 1 ? "fact" : "facts"}
                    </span>
                  </div>
                  <div className="flex items-center gap-1.5 text-sm">
                    <span className="truncate">
                      {edge.source_node_concept ?? truncateId(edge.source_node_id)}
                    </span>
                    <ArrowRight className="h-3 w-3 text-muted-foreground shrink-0" />
                    <span className="truncate">
                      {edge.target_node_concept ?? truncateId(edge.target_node_id)}
                    </span>
                  </div>
                  {edge.justification && (
                    <p className="text-xs text-muted-foreground line-clamp-2 mt-1">
                      {edge.justification}
                    </p>
                  )}
                  <div className="flex items-center gap-2 mt-1">
                    <span className="text-xs text-muted-foreground">
                      {formatDate(edge.created_at)}
                    </span>
                    {edge.supporting_fact_ids.length > 0 && (
                      <span className="inline-flex items-center gap-1 text-xs text-muted-foreground">
                        <FileText className="size-3" />
                        {edge.supporting_fact_ids.length} fact{edge.supporting_fact_ids.length !== 1 ? "s" : ""}
                      </span>
                    )}
                  </div>
                </div>
                <div className="flex items-center gap-1 shrink-0">
                  <Button
                    variant="ghost"
                    size="icon-sm"
                    onClick={(e) => {
                      e.stopPropagation();
                      setDeleteEdge(edge);
                    }}
                  >
                    <Trash2 className="size-3.5 text-destructive" />
                  </Button>
                </div>
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

      {/* Delete dialog */}
      {deleteEdge && (
        <DeleteConfirmDialog
          open={!!deleteEdge}
          title="Delete Edge"
          description="This will permanently remove this edge from the graph."
          itemId={deleteEdge.id}
          onConfirm={async () => {
            await api.edges.delete(deleteEdge.id);
            setDeleteEdge(null);
            refresh();
          }}
          onCancel={() => setDeleteEdge(null)}
        />
      )}

      {/* Edge detail panel */}
      <EdgeDetailPanel
        edgeId={selectedEdgeId}
        onClose={() => setSelectedEdgeId(null)}
      />
    </div>
  );
}
