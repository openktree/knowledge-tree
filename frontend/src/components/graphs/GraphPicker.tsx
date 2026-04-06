"use client";

import { Database, Loader2 } from "lucide-react";
import { useGraph } from "@/contexts/graph";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { cn } from "@/lib/utils";

export function GraphPicker({ collapsed }: { collapsed: boolean }) {
  const { activeGraph, graphs, setActiveGraph, loading, switching, activeGraphInfo } = useGraph();

  // Don't show picker if there's only one graph (or none) AND it's the default
  if (!loading && graphs.length <= 1 && activeGraph === "default") {
    return null;
  }

  // Show spinner while loading graphs
  if (loading) {
    return (
      <div className="flex items-center justify-center px-2 py-2">
        <Loader2 className="size-4 animate-spin text-muted-foreground" />
      </div>
    );
  }

  if (collapsed) {
    return (
      <Tooltip>
        <TooltipTrigger asChild>
          <button
            className="flex items-center justify-center rounded-md px-0 py-2 text-sm transition-colors text-muted-foreground hover:bg-accent/50 hover:text-foreground w-full"
          >
            <Database className={cn("size-4", activeGraph !== "default" && "text-primary")} />
          </button>
        </TooltipTrigger>
        <TooltipContent side="right" className="p-0">
          <div className="flex flex-col py-1">
            {graphs.map((g) => (
              <button
                key={g.slug}
                onClick={() => setActiveGraph(g.slug)}
                disabled={switching}
                className={cn(
                  "px-3 py-1.5 text-xs text-left hover:bg-accent transition-colors",
                  g.slug === activeGraph && "font-medium text-primary",
                  switching && "opacity-50 cursor-wait",
                )}
              >
                {g.name}
              </button>
            ))}
          </div>
        </TooltipContent>
      </Tooltip>
    );
  }

  return (
    <div className="px-2 space-y-1">
      <select
        value={activeGraph}
        onChange={(e) => setActiveGraph(e.target.value)}
        disabled={switching}
        className={cn(
          "w-full rounded-md border border-border bg-background px-2 py-1.5 text-xs focus:outline-none focus:ring-2 focus:ring-ring truncate",
          switching && "opacity-50 cursor-wait",
        )}
        title="Active graph"
      >
        {graphs.map((g) => (
          <option key={g.slug} value={g.slug}>
            {g.name}
          </option>
        ))}
      </select>
      {activeGraphInfo && activeGraph !== "default" && (
        <div className="flex items-center gap-1 px-1">
          <span className="inline-block size-1.5 rounded-full bg-primary" />
          <span className="text-[10px] text-muted-foreground truncate">
            {activeGraphInfo.name}
          </span>
        </div>
      )}
    </div>
  );
}
