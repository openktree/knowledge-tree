"use client";

import { useEffect, useState, useCallback } from "react";
import { Plus, FileText, Loader2, Trash2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { listSyntheses, deleteSynthesis } from "@/lib/api";
import { CreateSynthesisDialog } from "@/components/synthesis/CreateSynthesisDialog";
import type { SynthesisListItem } from "@/types";
import { formatSynthesisConcept, formatModelName } from "@/components/synthesis/utils";

export default function SynthesesPage() {
  const [items, setItems] = useState<SynthesisListItem[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [createOpen, setCreateOpen] = useState(false);
  const [deleting, setDeleting] = useState<string | null>(null);

  const fetchSyntheses = useCallback(async () => {
    setLoading(true);
    try {
      const data = await listSyntheses(0, 50);
      setItems(data.items);
      setTotal(data.total);
    } catch (err) {
      console.error("Failed to load syntheses:", err);
    } finally {
      setLoading(false);
    }
  }, []);

  const handleDelete = useCallback(
    async (e: React.MouseEvent, id: string) => {
      e.preventDefault();
      e.stopPropagation();
      if (!confirm("Delete this synthesis? This cannot be undone.")) return;
      setDeleting(id);
      try {
        await deleteSynthesis(id);
        fetchSyntheses();
      } catch (err) {
        console.error("Failed to delete synthesis:", err);
      } finally {
        setDeleting(null);
      }
    },
    [fetchSyntheses]
  );

  useEffect(() => {
    fetchSyntheses();
  }, [fetchSyntheses]);

  return (
    <div className="mx-auto max-w-3xl py-12 px-4">
      {/* Header */}
      <div className="flex items-end justify-between mb-10">
        <div>
          <p className="text-[0.68rem] uppercase tracking-[0.12em] font-bold text-muted-foreground mb-1">
            Graph Investigation
          </p>
          <h1 className="text-[2rem] font-semibold text-foreground leading-tight">
            Investigate
          </h1>
          <p className="text-[0.85rem] text-muted-foreground mt-1">
            {total} document{total !== 1 ? "s" : ""}
          </p>
          <p className="text-[0.82rem] text-muted-foreground/70 mt-2 max-w-md">
            Synthesis agents investigate topics by navigating and integrating
            information across your knowledge graph into research documents.
          </p>
        </div>
        <Button
          onClick={() => setCreateOpen(true)}
          className="rounded-full px-5"
        >
          <Plus className="mr-2 size-4" />
          New Investigation
        </Button>
      </div>

      {/* Content */}
      {loading ? (
        <div className="flex items-center justify-center py-20">
          <Loader2 className="size-5 animate-spin text-muted-foreground" />
        </div>
      ) : items.length === 0 ? (
        <div className="flex flex-col items-center justify-center py-20 text-center">
          <div className="rounded-full bg-muted p-6 mb-5">
            <FileText className="size-8 text-muted-foreground" />
          </div>
          <h3 className="text-[1.1rem] font-medium text-foreground/80 mb-1">
            No investigations yet
          </h3>
          <p className="text-[0.85rem] text-muted-foreground mb-6 max-w-sm">
            Start an investigation to have a synthesis agent explore and
            integrate information across your knowledge graph.
          </p>
          <Button
            onClick={() => setCreateOpen(true)}
            className="rounded-full px-5"
          >
            <Plus className="mr-2 size-4" />
            Start First Investigation
          </Button>
        </div>
      ) : (
        <div className="space-y-4">
          {(() => {
            // Collect IDs that are children of a supersynthesis
            const childIds = new Set<string>();
            for (const item of items) {
              for (const sid of item.sub_synthesis_ids) {
                childIds.add(sid);
              }
            }
            // Build lookup
            const itemMap = new Map(items.map((i) => [i.id, i]));

            // Render top-level items (not children of any supersynthesis)
            return items
              .filter((item) => !childIds.has(item.id))
              .map((item) => {
                const children = item.sub_synthesis_ids
                  .map((sid) => itemMap.get(sid))
                  .filter(Boolean) as SynthesisListItem[];

                return (
                  <div key={item.id}>
                    <SynthesisCard
                      item={item}
                      deleting={deleting}
                      onDelete={handleDelete}
                    />
                    {/* Nested children */}
                    {children.length > 0 && (
                      <div className="ml-6 mt-1 space-y-1 border-l-2 border-border pl-4">
                        {children.map((child) => (
                          <SynthesisCard
                            key={child.id}
                            item={child}
                            deleting={deleting}
                            onDelete={handleDelete}
                            compact
                          />
                        ))}
                      </div>
                    )}
                  </div>
                );
              });
          })()}
        </div>
      )}

      <CreateSynthesisDialog
        open={createOpen}
        onOpenChange={setCreateOpen}
        onCreated={fetchSyntheses}
      />
    </div>
  );
}

// ── Card component ───────────────────────────────────────────────

function SynthesisCard({
  item,
  deleting,
  onDelete,
  compact = false,
}: {
  item: SynthesisListItem;
  deleting: string | null;
  onDelete: (e: React.MouseEvent, id: string) => void;
  compact?: boolean;
}) {
  const { title, date } = formatSynthesisConcept(item.concept);

  return (
    <a
      href={`/investigate/${item.id}`}
      className={`block rounded-lg border bg-card hover:border-ocean/30 hover:shadow-sm transition-all ${
        compact ? "px-3 py-2" : "px-5 py-4"
      }`}
    >
      <div className="flex items-start justify-between gap-4">
        <div className="min-w-0 flex-1">
          <h2
            className={`font-medium text-foreground/85 truncate ${
              compact ? "text-[0.88rem] mb-0.5" : "text-[1.05rem] mb-1.5"
            }`}
          >
            {title}
          </h2>
          <div className="flex items-center gap-2 flex-wrap">
            {!compact && (
              <Badge
                variant="outline"
                className="text-[0.6rem] uppercase tracking-wider font-semibold px-2 py-0 border-ocean/30 text-ocean dark:text-ocean-mid"
              >
                {item.node_type === "supersynthesis"
                  ? "Super-Synthesis"
                  : "Synthesis"}
              </Badge>
            )}
            <Badge
              variant={
                item.visibility === "public" ? "default" : "secondary"
              }
              className="text-[0.6rem] uppercase tracking-wider px-2 py-0"
            >
              {item.visibility}
            </Badge>
            <span className="text-[0.78rem] text-muted-foreground">
              {item.sentence_count} sentences
            </span>
            {formatModelName(item.model_id) && (
              <>
                <span className="text-border">·</span>
                <span className="text-[0.78rem] text-muted-foreground">
                  {formatModelName(item.model_id)}
                </span>
              </>
            )}
            {(date || item.created_at) && (
              <>
                <span className="text-border">·</span>
                <span className="text-[0.78rem] text-muted-foreground">
                  {date ??
                    (item.created_at &&
                      new Date(item.created_at).toLocaleDateString(
                        undefined,
                        { year: "numeric", month: "short", day: "numeric" }
                      ))}
                </span>
              </>
            )}
            {!compact && item.sub_synthesis_ids.length > 0 && (
              <>
                <span className="text-border">·</span>
                <span className="text-[0.78rem] text-muted-foreground">
                  {item.sub_synthesis_ids.length} sub-syntheses
                </span>
              </>
            )}
          </div>
        </div>
        <Button
          variant="ghost"
          size="icon"
          className={`shrink-0 text-muted-foreground hover:text-destructive ${
            compact ? "size-6" : "size-8 mt-0.5"
          }`}
          onClick={(e) => onDelete(e, item.id)}
          disabled={deleting === item.id}
        >
          {deleting === item.id ? (
            <Loader2 className="size-3.5 animate-spin" />
          ) : (
            <Trash2 className={compact ? "size-3" : "size-3.5"} />
          )}
        </Button>
      </div>
    </a>
  );
}
