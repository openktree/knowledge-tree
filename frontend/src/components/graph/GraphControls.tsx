"use client";

import { useState } from "react";
import { ZoomIn, ZoomOut, Maximize2, LayoutGrid, Search, Shuffle, Settings2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Slider } from "@/components/ui/slider";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { layoutLabels, type EdgeForceSettings, defaultEdgeForces, getEdgeColor, DEFAULT_MAX_NODE_SIZE } from "@/lib/graph-utils";

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

export interface GraphControlsProps {
  onLayoutChange: (layout: string) => void;
  onZoomIn: () => void;
  onZoomOut: () => void;
  onFitGraph: () => void;
  /** Called when the user clicks the reorganize button to re-run the layout. */
  onReorganize: () => void;
  /** Currently active layout name. Used to highlight the active button. */
  activeLayout?: string;
  /** Called when the user types in the graph search field. */
  onSearchGraph?: (query: string) => void;
  /** Current neighbor depth (0–5). */
  neighborDepth?: number;
  /** Called when the user changes the neighbor depth slider. */
  onNeighborDepthChange?: (depth: number) => void;
  /** Current edge force settings. */
  edgeForces?: EdgeForceSettings;
  /** Called when the user adjusts an edge force slider. */
  onEdgeForcesChange?: (forces: EdgeForceSettings) => void;
  /** Current max node size in pixels. */
  maxNodeSize?: number;
  /** Called when the user adjusts the max node size slider. */
  onMaxNodeSizeChange?: (size: number) => void;
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

const forceLabels: Record<keyof EdgeForceSettings, string> = {
  related: "Related",
  cross_type: "Cross-Type",
  contradicts: "Contradicts",
  parent: "Parent",
};

export default function GraphControls({
  onLayoutChange,
  onZoomIn,
  onZoomOut,
  onFitGraph,
  onReorganize,
  activeLayout = "fcose",
  onSearchGraph,
  neighborDepth,
  onNeighborDepthChange,
  edgeForces,
  onEdgeForcesChange,
  maxNodeSize,
  onMaxNodeSizeChange,
}: GraphControlsProps) {
  const [forcesOpen, setForcesOpen] = useState(false);

  return (
    <TooltipProvider>
      <div className="flex flex-col gap-1">
      {/* Force tuning panel (collapsible, above the toolbar) */}
      {forcesOpen && edgeForces && onEdgeForcesChange && (
        <div className="rounded-lg border bg-card/90 backdrop-blur-sm p-3 w-56">
          <p className="text-xs font-medium text-muted-foreground mb-2">Edge Forces</p>
          <div className="space-y-2.5">
            {(Object.keys(forceLabels) as (keyof EdgeForceSettings)[]).map((key) => (
              <div key={key} className="space-y-1">
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-1.5">
                    <span
                      className="inline-block size-2 rounded-full"
                      style={{ backgroundColor: getEdgeColor(key) }}
                    />
                    <span className="text-xs">{forceLabels[key]}</span>
                  </div>
                  <input
                    type="number"
                    min={1}
                    max={1000}
                    value={edgeForces[key]}
                    onChange={(e) => {
                      const v = Math.max(1, Math.min(1000, Number(e.target.value) || 1));
                      onEdgeForcesChange({ ...edgeForces, [key]: v });
                    }}
                    className="w-12 h-5 text-[10px] tabular-nums text-right bg-transparent border rounded px-1 focus:outline-none focus:ring-1 focus:ring-ring"
                    aria-label={`${forceLabels[key]} exact value`}
                  />
                </div>
                <Slider
                  min={1}
                  max={1000}
                  step={10}
                  value={[edgeForces[key]]}
                  onValueChange={([v]) =>
                    onEdgeForcesChange({ ...edgeForces, [key]: v })
                  }
                  className="w-full"
                  aria-label={`${forceLabels[key]} edge force`}
                />
              </div>
            ))}
          </div>
          {/* Max node size slider */}
          {onMaxNodeSizeChange && maxNodeSize !== undefined && (
            <div className="mt-3 pt-2.5 border-t space-y-1">
              <div className="flex items-center justify-between">
                <span className="text-xs">Max node size</span>
                <input
                  type="number"
                  min={10}
                  max={200}
                  value={maxNodeSize}
                  onChange={(e) => {
                    const v = Math.max(10, Math.min(200, Number(e.target.value) || 10));
                    onMaxNodeSizeChange(v);
                  }}
                  className="w-12 h-5 text-[10px] tabular-nums text-right bg-transparent border rounded px-1 focus:outline-none focus:ring-1 focus:ring-ring"
                  aria-label="Max node size exact value"
                />
              </div>
              <Slider
                min={10}
                max={200}
                step={5}
                value={[maxNodeSize]}
                onValueChange={([v]) => onMaxNodeSizeChange(v)}
                className="w-full"
                aria-label="Max node size"
              />
            </div>
          )}

          <Button
            variant="ghost"
            size="sm"
            className="w-full mt-2 h-6 text-[10px]"
            onClick={() => {
              onEdgeForcesChange({ ...defaultEdgeForces });
              onMaxNodeSizeChange?.(DEFAULT_MAX_NODE_SIZE);
            }}
          >
            Reset defaults
          </Button>
        </div>
      )}
      <div className="flex items-center gap-1 rounded-lg border bg-card/80 p-1 backdrop-blur-sm">
        {/* Search graph */}
        {onSearchGraph && (
          <>
            <div className="relative flex items-center">
              <Search className="absolute left-2 size-3.5 text-muted-foreground pointer-events-none" />
              <Input
                type="text"
                placeholder="Search graph..."
                className="h-7 w-32 pl-7 text-xs"
                onChange={(e) => onSearchGraph(e.target.value)}
              />
            </div>
            <div className="mx-0.5 h-5 w-px bg-border" />
          </>
        )}

        {/* Layout buttons */}
        {Object.entries(layoutLabels).map(([key, label]) => (
          <Tooltip key={key}>
            <TooltipTrigger asChild>
              <Button
                variant={activeLayout === key ? "default" : "ghost"}
                size="icon-sm"
                onClick={() => onLayoutChange(key)}
                aria-label={`Layout: ${label}`}
              >
                <LayoutGrid className="size-4" />
              </Button>
            </TooltipTrigger>
            <TooltipContent side="bottom">{label}</TooltipContent>
          </Tooltip>
        ))}

        {/* Reorganize */}
        <Tooltip>
          <TooltipTrigger asChild>
            <Button
              variant="ghost"
              size="icon-sm"
              onClick={onReorganize}
              aria-label="Reorganize layout"
            >
              <Shuffle className="size-4" />
            </Button>
          </TooltipTrigger>
          <TooltipContent side="bottom">Reorganize</TooltipContent>
        </Tooltip>

        {/* Separator */}
        <div className="mx-0.5 h-5 w-px bg-border" />

        {/* Neighbor depth slider */}
        {onNeighborDepthChange && neighborDepth !== undefined && (
          <>
            <div className="flex items-center gap-2 px-1">
              <span className="text-xs text-muted-foreground whitespace-nowrap">
                Depth: {neighborDepth}
              </span>
              <Slider
                min={0}
                max={5}
                step={1}
                value={[neighborDepth]}
                onValueChange={([v]) => onNeighborDepthChange(v)}
                className="w-20"
                aria-label="Neighbor depth"
              />
            </div>
            <div className="mx-0.5 h-5 w-px bg-border" />
          </>
        )}

        {/* Zoom in */}
        <Tooltip>
          <TooltipTrigger asChild>
            <Button
              variant="ghost"
              size="icon-sm"
              onClick={onZoomIn}
              aria-label="Zoom in"
            >
              <ZoomIn className="size-4" />
            </Button>
          </TooltipTrigger>
          <TooltipContent side="bottom">Zoom in</TooltipContent>
        </Tooltip>

        {/* Zoom out */}
        <Tooltip>
          <TooltipTrigger asChild>
            <Button
              variant="ghost"
              size="icon-sm"
              onClick={onZoomOut}
              aria-label="Zoom out"
            >
              <ZoomOut className="size-4" />
            </Button>
          </TooltipTrigger>
          <TooltipContent side="bottom">Zoom out</TooltipContent>
        </Tooltip>

        {/* Fit to screen */}
        <Tooltip>
          <TooltipTrigger asChild>
            <Button
              variant="ghost"
              size="icon-sm"
              onClick={onFitGraph}
              aria-label="Fit graph to screen"
            >
              <Maximize2 className="size-4" />
            </Button>
          </TooltipTrigger>
          <TooltipContent side="bottom">Fit to screen</TooltipContent>
        </Tooltip>

        {/* Edge forces toggle */}
        {onEdgeForcesChange && (
          <>
            <div className="mx-0.5 h-5 w-px bg-border" />
            <Tooltip>
              <TooltipTrigger asChild>
                <Button
                  variant={forcesOpen ? "default" : "ghost"}
                  size="icon-sm"
                  onClick={() => setForcesOpen((o) => !o)}
                  aria-label="Edge force settings"
                >
                  <Settings2 className="size-4" />
                </Button>
              </TooltipTrigger>
              <TooltipContent side="bottom">Edge forces</TooltipContent>
            </Tooltip>
          </>
        )}
      </div>
      </div>
    </TooltipProvider>
  );
}
