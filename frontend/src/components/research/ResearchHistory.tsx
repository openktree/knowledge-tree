"use client";

import { useState, useEffect, useCallback } from "react";
import { Clock, ChevronRight, CheckCircle2, Loader2 } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { api } from "@/lib/api";
import { useGraph } from "@/contexts/graph";
import type { ConversationListItem } from "@/types";

interface ResearchHistoryProps {
  /** Called when user clicks "View" on a completed research. */
  onResume: (conversationId: string) => void;
  /** Called when user clicks "View" on a completed build. */
  onView: (conversationId: string) => void;
}

type ResearchPhase = "gathering" | "completed";

function getPhase(item: ConversationListItem): ResearchPhase {
  if (item.latest_status === "completed") {
    return "completed";
  }
  return "gathering";
}

function phaseBadge(phase: ResearchPhase) {
  switch (phase) {
    case "gathering":
      return (
        <Badge variant="outline" className="gap-1 text-xs">
          <Loader2 className="size-3 animate-spin" />
          Gathering
        </Badge>
      );
    case "completed":
      return (
        <Badge variant="outline" className="gap-1 text-xs border-green-500 text-green-700 dark:text-green-400">
          <CheckCircle2 className="size-3" />
          Complete
        </Badge>
      );
  }
}

function formatRelativeTime(isoDate: string): string {
  const now = Date.now();
  const then = new Date(isoDate).getTime();
  const diffMs = now - then;
  const minutes = Math.floor(diffMs / 60_000);
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  if (days < 7) return `${days}d ago`;
  return new Date(isoDate).toLocaleDateString();
}

export function ResearchHistory({ onResume, onView }: ResearchHistoryProps) {
  const { switchGeneration } = useGraph();
  const [items, setItems] = useState<ConversationListItem[]>([]);
  const [loading, setLoading] = useState(true);

  const fetchHistory = useCallback(async () => {
    try {
      const res = await api.conversations.list({
        mode: "bottom_up_ingest",
        limit: 10,
      });
      setItems(res.items);
    } catch {
      // Silently ignore — history is non-critical
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchHistory();
  }, [fetchHistory]);

  // Refetch when the active graph changes
  useEffect(() => {
    setLoading(true);
    fetchHistory();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [switchGeneration]);

  if (loading) {
    return (
      <div className="flex items-center gap-2 text-sm text-muted-foreground py-4">
        <Loader2 className="size-4 animate-spin" />
        Loading history...
      </div>
    );
  }

  if (items.length === 0) {
    return null;
  }

  return (
    <div className="space-y-3">
      <h3 className="text-sm font-medium text-muted-foreground flex items-center gap-1.5">
        <Clock className="size-3.5" />
        Recent Web Research
      </h3>
      <div className="space-y-1.5">
        {items.map((item) => {
          const phase = getPhase(item);
          return (
            <div
              key={item.id}
              className="flex items-center gap-3 rounded-lg border px-3 py-2.5 text-sm"
            >
              <div className="flex-1 min-w-0">
                <p className="truncate font-medium">
                  {item.title || "Untitled"}
                </p>
                <p className="text-xs text-muted-foreground">
                  {formatRelativeTime(item.updated_at)}
                </p>
              </div>
              {phaseBadge(phase)}
              {phase === "completed" ? (
                <Button
                  variant="outline"
                  size="sm"
                  className="gap-1 shrink-0"
                  onClick={() => onResume(item.id)}
                >
                  View Summary
                  <ChevronRight className="size-3.5" />
                </Button>
              ) : (
                <Button
                  variant="ghost"
                  size="sm"
                  className="gap-1 shrink-0"
                  onClick={() => onView(item.id)}
                  disabled
                >
                  In Progress
                </Button>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
