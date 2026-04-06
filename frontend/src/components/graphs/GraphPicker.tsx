"use client";

import { Database } from "lucide-react";
import { useGraph } from "@/contexts/graph";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { cn } from "@/lib/utils";

export function GraphPicker({ collapsed }: { collapsed: boolean }) {
  const { activeGraph, graphs, setActiveGraph, loading } = useGraph();

  // Don't show picker if there's only one graph (or none)
  if (loading || graphs.length <= 1) {
    return null;
  }

  if (collapsed) {
    return (
      <Tooltip>
        <TooltipTrigger asChild>
          <button
            className="flex items-center justify-center rounded-md px-0 py-2 text-sm transition-colors text-muted-foreground hover:bg-accent/50 hover:text-foreground w-full"
          >
            <Database className="size-4" />
          </button>
        </TooltipTrigger>
        <TooltipContent side="right" className="p-0">
          <div className="flex flex-col py-1">
            {graphs.map((g) => (
              <button
                key={g.slug}
                onClick={() => setActiveGraph(g.slug)}
                className={cn(
                  "px-3 py-1.5 text-xs text-left hover:bg-accent transition-colors",
                  g.slug === activeGraph && "font-medium text-primary",
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
    <div className="px-2">
      <select
        value={activeGraph}
        onChange={(e) => setActiveGraph(e.target.value)}
        className="w-full rounded-md border border-border bg-background px-2 py-1.5 text-xs focus:outline-none focus:ring-2 focus:ring-ring truncate"
        title="Active graph"
      >
        {graphs.map((g) => (
          <option key={g.slug} value={g.slug}>
            {g.name}
          </option>
        ))}
      </select>
    </div>
  );
}
