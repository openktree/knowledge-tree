"use client";

import { useCallback, useState, useRef } from "react";
import { useSearchParams, useRouter, usePathname } from "next/navigation";
import { Pencil, Trash2, Loader2, Search, Check, ChevronsUpDown, Download, Upload, User } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { Progress } from "@/components/ui/progress";
import { ScrollArea } from "@/components/ui/scroll-area";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { useFactList } from "@/hooks/useFactList";
import { FactEditDialog } from "@/components/fact/FactEditDialog";
import { FactDetailPanel } from "@/components/fact/FactDetailPanel";
import { DeleteConfirmDialog } from "@/components/shared/DeleteConfirmDialog";
import { api } from "@/lib/api";
import { downloadJson } from "@/lib/download";
import { FactType } from "@/types";
import type { FactResponse, ImportProgress } from "@/types";

const FACT_TYPES = Object.values(FactType);

function formatDate(dateStr: string): string {
  return new Date(dateStr).toLocaleDateString("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
  });
}

const TYPE_COLORS: Record<string, string> = {
  claim: "bg-orange-500/20 text-orange-400",
  account: "bg-purple-500/20 text-purple-400",
  measurement: "bg-cyan-500/20 text-cyan-400",
  formula: "bg-indigo-500/20 text-indigo-400",
  quote: "bg-pink-500/20 text-pink-400",
  procedure: "bg-blue-500/20 text-blue-400",
  reference: "bg-green-500/20 text-green-400",
  code: "bg-amber-500/20 text-amber-400",
  perspective: "bg-violet-500/20 text-violet-400",
};

const PAGE_SIZE = 20;

export function FactListView() {
  const {
    facts,
    total,
    offset,
    search,
    factType,
    isLoading,
    error,
    setSearch,
    setFactType,
    setPage,
    refresh,
  } = useFactList();

  const [editFact, setEditFact] = useState<FactResponse | null>(null);
  const [deleteFact, setDeleteFact] = useState<FactResponse | null>(null);
  const searchParams = useSearchParams();
  const router = useRouter();
  const pathname = usePathname();
  const selectedFactId = searchParams.get("selected");

  const setSelectedFactId = useCallback((id: string | null) => {
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

  const handleImportFacts = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    setIsImporting(true);
    setImportProgress(null);
    try {
      const text = await file.text();
      const data = JSON.parse(text);
      await api.import.factsStream(
        { facts: data.facts ?? [] },
        (progress) => setImportProgress(progress),
      );
      refresh();
    } finally {
      setIsImporting(false);
      setImportProgress(null);
      if (fileInputRef.current) fileInputRef.current.value = "";
    }
  };

  const handleExportFacts = async () => {
    setIsExporting(true);
    try {
      const data = await api.export.facts();
      const date = new Date().toISOString().slice(0, 10);
      downloadJson(data, `knowledge-tree-facts-${date}.json`);
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
      {/* Search + filter + count */}
      <div className="flex items-center gap-3 p-4 border-b">
        <div className="relative flex-1">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 size-4 text-muted-foreground" />
          <Input
            placeholder="Search facts..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="pl-9"
          />
        </div>
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button variant="outline" className="gap-1 shrink-0">
              {factType ?? "All types"}
              <ChevronsUpDown className="size-3.5 opacity-50" />
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end">
            <DropdownMenuItem onClick={() => setFactType(null)}>
              {!factType && <Check className="mr-2 size-4" />}
              <span className={factType ? "ml-6" : ""}>All types</span>
            </DropdownMenuItem>
            {FACT_TYPES.map((type) => (
              <DropdownMenuItem
                key={type}
                onClick={() => setFactType(type)}
              >
                {factType === type && <Check className="mr-2 size-4" />}
                <span className={factType !== type ? "ml-6" : ""}>
                  {type}
                </span>
              </DropdownMenuItem>
            ))}
          </DropdownMenuContent>
        </DropdownMenu>
        <Badge variant="secondary">{total} facts</Badge>
        <input
          ref={fileInputRef}
          type="file"
          accept=".json"
          className="hidden"
          onChange={handleImportFacts}
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
          onClick={handleExportFacts}
        >
          {isExporting ? (
            <Loader2 className="size-4 animate-spin" />
          ) : (
            <Download className="size-4" />
          )}
          <span className="ml-1">Export JSON</span>
        </Button>
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
          {!isLoading && facts.length === 0 && (
            <p className="text-center text-sm text-muted-foreground py-8">
              No facts found
            </p>
          )}
          {facts.map((fact) => (
            <Card
              key={fact.id}
              className="p-4 cursor-pointer hover:bg-accent/30 transition-colors"
              onClick={() => setSelectedFactId(fact.id)}
            >
              <div className="flex items-start justify-between gap-3">
                <div className="flex-1 min-w-0">
                  <p className="text-sm line-clamp-2">{fact.content}</p>
                  <div className="flex items-center gap-2 mt-2 flex-wrap">
                    <Badge
                      variant="secondary"
                      className={TYPE_COLORS[fact.fact_type] ?? ""}
                    >
                      {fact.fact_type}
                    </Badge>
                    <span className="text-xs text-muted-foreground">
                      {formatDate(fact.created_at)}
                    </span>
                    {(() => {
                      const authors = [...new Set(
                        fact.sources
                          .map((s) => s.author_person || s.author_org)
                          .filter(Boolean),
                      )];
                      if (authors.length === 0) return null;
                      return (
                        <span className="flex items-center gap-1 text-xs text-muted-foreground">
                          <User className="size-3 shrink-0" />
                          {authors.slice(0, 2).join(", ")}
                          {authors.length > 2 && ` +${authors.length - 2}`}
                        </span>
                      );
                    })()}
                  </div>
                </div>
                <div className="flex items-center gap-1 shrink-0">
                  <Button
                    variant="ghost"
                    size="icon-sm"
                    onClick={(e) => { e.stopPropagation(); setEditFact(fact); }}
                  >
                    <Pencil className="size-3.5" />
                  </Button>
                  <Button
                    variant="ghost"
                    size="icon-sm"
                    onClick={(e) => { e.stopPropagation(); setDeleteFact(fact); }}
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
      {editFact && (
        <FactEditDialog
          open={!!editFact}
          fact={editFact}
          onClose={() => setEditFact(null)}
          onSaved={refresh}
        />
      )}

      {/* Delete dialog */}
      {deleteFact && (
        <DeleteConfirmDialog
          open={!!deleteFact}
          title="Delete Fact"
          description="This will remove the fact and unlink it from all nodes."
          itemId={deleteFact.id}
          onConfirm={async () => {
            await api.facts.delete(deleteFact.id);
            setDeleteFact(null);
            refresh();
          }}
          onCancel={() => setDeleteFact(null)}
        />
      )}

      {/* Fact detail panel */}
      <FactDetailPanel
        factId={selectedFactId}
        onClose={() => setSelectedFactId(null)}
      />
    </div>
  );
}
