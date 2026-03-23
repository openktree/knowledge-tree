"use client";

import { useState } from "react";
import type { ActivityEntry } from "@/types";
import { cn } from "@/lib/utils";
import { ScrollArea } from "@/components/ui/scroll-area";
import {
  Search,
  BookOpen,
  Plus,
  Brain,
  Link,
  Compass,
  Loader2,
  ChevronDown,
  ChevronUp,
} from "lucide-react";

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

export interface ActivityLogProps {
  activities: ActivityEntry[];
  isActive: boolean;
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function ToolIcon({ tool, className }: { tool: string; className?: string }) {
  switch (tool) {
    case "search_nodes":
      return <Search className={className} />;
    case "explore_concept":
      return <Plus className={className} />;
    case "read_node":
      return <BookOpen className={className} />;
    case "synthesize_answer":
      return <Brain className={className} />;
    case "connect":
      return <Link className={className} />;
    default:
      return <Compass className={className} />;
  }
}

function relativeTime(timestamp: string): string {
  const diff = Date.now() - new Date(timestamp).getTime();
  const secs = Math.floor(diff / 1000);
  if (secs < 5) return "just now";
  if (secs < 60) return `${secs}s ago`;
  const mins = Math.floor(secs / 60);
  return `${mins}m ago`;
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function ActivityLog({ activities, isActive }: ActivityLogProps) {
  const [expanded, setExpanded] = useState(false);
  const latest = activities[activities.length - 1];
  const history = activities.slice(0, -1);

  if (activities.length === 0 && isActive) {
    return (
      <div className="flex items-center gap-2 text-sm text-muted-foreground">
        <Loader2 className="h-4 w-4 animate-spin" />
        <span>Starting...</span>
      </div>
    );
  }

  if (activities.length === 0) return null;

  return (
    <div className="flex flex-col gap-2">
      {/* Latest activity */}
      <div className="flex items-center gap-2">
        {isActive ? (
          <Loader2 className="h-4 w-4 shrink-0 animate-spin text-primary" />
        ) : (
          <ToolIcon tool={latest.tool} className="h-4 w-4 shrink-0 text-muted-foreground" />
        )}
        <span
          className={cn(
            "text-sm font-medium",
            isActive && "animate-pulse",
          )}
        >
          {latest.action}
        </span>
      </div>

      {/* Expand/collapse history */}
      {history.length > 0 && (
        <>
          <button
            type="button"
            onClick={() => setExpanded((v) => !v)}
            className="flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground transition-colors self-start"
          >
            {expanded ? (
              <ChevronUp className="h-3 w-3" />
            ) : (
              <ChevronDown className="h-3 w-3" />
            )}
            {history.length} previous step{history.length !== 1 ? "s" : ""}
          </button>

          {expanded && (
            <ScrollArea className="max-h-40">
              <ul className="flex flex-col gap-1 pr-3">
                {history.map((entry, i) => {
                  return (
                    <li
                      key={i}
                      className="flex items-center gap-2 text-xs text-muted-foreground"
                    >
                      <ToolIcon tool={entry.tool} className="h-3 w-3 shrink-0" />
                      <span className="flex-1 min-w-0">{entry.action}</span>
                      <span className="shrink-0 tabular-nums">
                        {relativeTime(entry.timestamp)}
                      </span>
                    </li>
                  );
                })}
              </ul>
            </ScrollArea>
          )}
        </>
      )}
    </div>
  );
}
