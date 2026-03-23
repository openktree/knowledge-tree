"use client";

import { useCallback, useState, useRef } from "react";
import { useSearchParams, useRouter, usePathname } from "next/navigation";
import { Pencil, Trash2, Loader2, Search, Network, Download, Upload, Filter, AlertTriangle } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { Progress } from "@/components/ui/progress";
import { ScrollArea } from "@/components/ui/scroll-area";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { useNodeList } from "@/hooks/useNodeList";
import { NodeEditDialog } from "@/components/node/NodeEditDialog";
import NodeDetailPanel from "@/components/node/NodeDetailPanel";
import { DeleteConfirmDialog } from "@/components/shared/DeleteConfirmDialog";
import { api } from "@/lib/api";
import { downloadJson } from "@/lib/download";
import type { NodeResponse, ImportProgress } from "@/types";

function formatDate(dateStr: string): string {
  return new Date(dateStr).toLocaleDateString("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
  });
}

const PAGE_SIZE = 20;

interface NodeListViewProps {
  onViewInGraph?: (nodeId: string) => void;
}

export function NodeListView({ onViewInGraph }: NodeListViewProps) {
  const {
    nodes,
    total,
    offset,
    search,
    nodeType,
    sort,
    isLoading,
    error,
    setSearch,
    setNodeType,
    setSort,
    setPage,
    refresh,
  } = useNodeList();

  const [editNode, setEditNode] = useState<NodeResponse | null>(null);
  const [deleteNode, setDeleteNode] = useState<NodeResponse | null>(null);
  const searchParams = useSearchParams();
  const router = useRouter();
  const pathname = usePathname();
  const selectedNodeId = searchParams.get("selected");

  const setSelectedNodeId = useCallback((id: string | null) => {
    const params = new URLSearchParams(searchParams.toString());
    if (id) {
      params.set("selected", id);
    } else {
      params.delete("selected");
    }
    const qs = params.toString();
    router.replace(qs ? `${pathname}?${qs}` : pathname, { scroll: false });
  }, [router, searchParams, pathname]);
  const [isExporting, setIsExporting] = useState(false);
  const [isImporting, setIsImporting] = useState(false);
  const [importProgress, setImportProgress] = useState<ImportProgress | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const handleImportNodes = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    setIsImporting(true);
    setImportProgress(null);
    try {
      const text = await file.text();
      const data = JSON.parse(text);
      await api.import.nodesStream(
        {
          nodes: data.nodes ?? [],
          edges: data.edges ?? [],
          facts: data.facts ?? [],
          node_fact_links: data.node_fact_links ?? [],
        },
        (progress) => setImportProgress(progress),
      );
      refresh();
    } finally {
      setIsImporting(false);
      setImportProgress(null);
      if (fileInputRef.current) fileInputRef.current.value = "";
    }
  };

  const handleExportNodes = async () => {
    setIsExporting(true);
    try {
      const data = await api.export.nodes();
      const date = new Date().toISOString().slice(0, 10);
      downloadJson(data, `knowledge-tree-nodes-${date}.json`);
    } finally {
      setIsExporting(false);
    }
  };

  const currentPage = Math.floor(offset / PAGE_SIZE);
  const totalPages = Math.ceil(total / PAGE_SIZE);
  const showingStart = total > 0 ? offset + 1 : 0;
  const showingEnd = Math.min(offset + PAGE_SIZE, total);

  return (
    <div className="flex flex-col h-full relative">
      {/* Search + count */}
      <div className="flex items-center gap-3 p-4 border-b">
        <div className="relative flex-1">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 size-4 text-muted-foreground" />
          <Input
            placeholder="Search nodes..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="pl-9"
          />
        </div>
        <Badge variant="secondary">{total} nodes</Badge>
        <input
          ref={fileInputRef}
          type="file"
          accept=".json"
          className="hidden"
          onChange={handleImportNodes}
        />
        <Button
          variant="outline"
          size="sm"
          disabled={isImporting}
          onClick={() => fileInputRef.current?.click()}
        >
          {isImporting ? (
            <Loader2 className="size-4 animate-spin" />
          ) : (
            <Upload className="size-4" />
          )}
          <span className="ml-1">Import JSON</span>
        </Button>
        <Button
          variant="outline"
          size="sm"
          disabled={isExporting}
          onClick={handleExportNodes}
        >
          {isExporting ? (
            <Loader2 className="size-4 animate-spin" />
          ) : (
            <Download className="size-4" />
          )}
          <span className="ml-1">Export JSON</span>
        </Button>
      </div>

      {/* Filters */}
      <div className="flex items-center gap-3 px-4 py-2 border-b">
        <Filter className="size-4 text-muted-foreground shrink-0" />
        <Select value={nodeType} onValueChange={setNodeType}>
          <SelectTrigger className="w-[150px] h-8 text-xs">
            <SelectValue placeholder="All types" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">All types</SelectItem>
            <SelectItem value="concept">Concept</SelectItem>
            <SelectItem value="entity">Entity</SelectItem>
            <SelectItem value="perspective">Perspective</SelectItem>
            <SelectItem value="event">Event</SelectItem>
          </SelectContent>
        </Select>
        <Select value={sort} onValueChange={setSort}>
          <SelectTrigger className="w-[160px] h-8 text-xs">
            <SelectValue placeholder="Sort by" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="updated_at">Recently updated</SelectItem>
            <SelectItem value="edge_count">Most connected</SelectItem>
            <SelectItem value="pending_facts">Pending facts</SelectItem>
          </SelectContent>
        </Select>
      </div>

      {/* Import progress */}
      {isImporting && importProgress && (
        <div className="px-4 py-3 border-b space-y-1.5">
          <div className="flex items-center justify-between text-xs text-muted-foreground">
            <span className="capitalize">Importing {importProgress.phase}...</span>
            <span>{importProgress.processed} / {importProgress.total}</span>
          </div>
          <Progress
            value={importProgress.total > 0
              ? (importProgress.processed / importProgress.total) * 100
              : 0}
          />
        </div>
      )}

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
          {!isLoading && nodes.length === 0 && (
            <p className="text-center text-sm text-muted-foreground py-8">
              No nodes found
            </p>
          )}
          {nodes.map((node) => (
            <Card
              key={node.id}
              className="p-4 cursor-pointer hover:bg-accent/30 transition-colors"
              onClick={() => setSelectedNodeId(node.id)}
            >
              <div className="flex items-start justify-between gap-3">
                <div className="flex-1 min-w-0">
                  <p className="font-medium truncate">{node.concept}</p>
                  {node.attractor && (
                    <p className="text-sm text-muted-foreground truncate">
                      Attractor: {node.attractor}
                    </p>
                  )}
                  <div className="flex items-center gap-3 mt-1 text-xs text-muted-foreground">
                    <span>Created {formatDate(node.created_at)}</span>
                    <span>Updated {formatDate(node.updated_at)}</span>
                    {node.seed_fact_count > 0 && (
                      <span>{node.fact_count}/{node.seed_fact_count} facts</span>
                    )}
                  </div>
                </div>
                <div className="flex items-center gap-1 shrink-0">
                  {node.pending_facts > 0 && (
                    <Badge variant="outline" className="text-xs text-amber-600 border-amber-400 gap-1">
                      <AlertTriangle className="size-3" />
                      {node.pending_facts} pending
                    </Badge>
                  )}
                  <Badge variant="secondary" className="text-xs capitalize">
                    {node.node_type}
                  </Badge>
                  {onViewInGraph && (
                    <Button
                      variant="ghost"
                      size="icon-sm"
                      title="View in graph"
                      onClick={(e) => { e.stopPropagation(); onViewInGraph(node.id); }}
                    >
                      <Network className="size-3.5" />
                    </Button>
                  )}
                  <Button
                    variant="ghost"
                    size="icon-sm"
                    onClick={(e) => { e.stopPropagation(); setEditNode(node); }}
                  >
                    <Pencil className="size-3.5" />
                  </Button>
                  <Button
                    variant="ghost"
                    size="icon-sm"
                    onClick={(e) => { e.stopPropagation(); setDeleteNode(node); }}
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

      {/* Edit dialog */}
      {editNode && (
        <NodeEditDialog
          open={!!editNode}
          node={editNode}
          onClose={() => setEditNode(null)}
          onSaved={refresh}
        />
      )}

      {/* Delete dialog */}
      {deleteNode && (
        <DeleteConfirmDialog
          open={!!deleteNode}
          title="Delete Node"
          description="This will remove the node, its edges, dimensions, and version history. Linked facts will NOT be deleted."
          itemId={deleteNode.id}
          onConfirm={async () => {
            await api.nodes.delete(deleteNode.id);
            setDeleteNode(null);
            refresh();
          }}
          onCancel={() => setDeleteNode(null)}
        />
      )}

      {/* Node detail panel */}
      <NodeDetailPanel
        nodeId={selectedNodeId}
        onClose={() => setSelectedNodeId(null)}
        onNodeSelect={(nodeId) => setSelectedNodeId(nodeId)}
      />
    </div>
  );
}
