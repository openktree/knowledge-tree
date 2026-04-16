"use client";

import { useState, useEffect, useCallback } from "react";
import {
  Clock,
  ChevronRight,
  CheckCircle2,
  Loader2,
  XCircle,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { api } from "@/lib/api";
import { useGraph } from "@/contexts/graph";
import type { ConversationListItem } from "@/types";

interface IngestHistoryProps {
  onView: (conversationId: string) => void;
}

type IngestPhase = "running" | "completed" | "failed";

function getPhase(item: ConversationListItem): IngestPhase {
  if (item.latest_status === "completed") return "completed";
  if (item.latest_status === "failed") return "failed";
  return "running";
}

function phaseBadge(phase: IngestPhase) {
  switch (phase) {
    case "running":
      return (
        <Badge variant="outline" className="gap-1 text-xs">
          <Loader2 className="size-3 animate-spin" />
          Running
        </Badge>
      );
    case "completed":
      return (
        <Badge
          variant="outline"
          className="gap-1 text-xs border-green-500 text-green-700 dark:text-green-400"
        >
          <CheckCircle2 className="size-3" />
          Complete
        </Badge>
      );
    case "failed":
      return (
        <Badge
          variant="outline"
          className="gap-1 text-xs border-red-500 text-red-700 dark:text-red-400"
        >
          <XCircle className="size-3" />
          Failed
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

export function IngestHistory({ onView }: IngestHistoryProps) {
  const { switchGeneration } = useGraph();
  const [items, setItems] = useState<ConversationListItem[]>([]);
  const [loading, setLoading] = useState(true);

  const fetchHistory = useCallback(async () => {
    try {
      const res = await api.conversations.list({
        mode: "ingest",
        limit: 10,
      });
      setItems(res.items);
    } catch {
      // Silently ignore — history is non-critical
    } finally {
      setLoading(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [switchGeneration]);

  useEffect(() => {
    fetchHistory();
  }, [fetchHistory]);

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
        Recent Ingestions
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
              <Button
                variant={phase === "completed" ? "outline" : "ghost"}
                size="sm"
                className="gap-1 shrink-0"
                onClick={() => onView(item.id)}
              >
                {phase === "running" ? "View Progress" : "View Build"}
                <ChevronRight className="size-3.5" />
              </Button>
            </div>
          );
        })}
      </div>
    </div>
  );
}
