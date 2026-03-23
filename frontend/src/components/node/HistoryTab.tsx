"use client";

import { useMemo } from "react";
import type { NodeVersionResponse } from "@/types";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { History } from "lucide-react";

interface HistoryTabProps {
  history: NodeVersionResponse[];
}

function formatDate(iso: string): string {
  const date = new Date(iso);
  const now = new Date();
  const diffMs = now.getTime() - date.getTime();
  const diffMins = Math.floor(diffMs / 60000);
  const diffHours = Math.floor(diffMins / 60);
  const diffDays = Math.floor(diffHours / 24);

  if (diffMins < 1) return "just now";
  if (diffMins < 60) return `${diffMins}m ago`;
  if (diffHours < 24) return `${diffHours}h ago`;
  if (diffDays < 7) return `${diffDays}d ago`;
  return date.toLocaleDateString("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
}

export function HistoryTab({ history }: HistoryTabProps) {
  const sortedHistory = useMemo(
    () => [...history].sort((a, b) => b.version_number - a.version_number),
    [history]
  );

  if (history.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center py-12 text-muted-foreground">
        <History className="h-10 w-10 mb-3 opacity-50" />
        <p>No version history.</p>
      </div>
    );
  }

  return (
    <div className="space-y-2">
      {sortedHistory.map((version, index) => (
        <Card key={version.id}>
          <CardContent className="py-3 flex items-center justify-between">
            <div className="flex items-center gap-3">
              <Badge
                variant={index === 0 ? "default" : "secondary"}
                className="tabular-nums"
              >
                v{version.version_number}
              </Badge>
              <span className="text-sm">
                Version {version.version_number}
              </span>
            </div>
            <span className="text-xs text-muted-foreground">
              {formatDate(version.created_at)}
            </span>
          </CardContent>
        </Card>
      ))}
    </div>
  );
}
