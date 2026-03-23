"use client";

import { Card, CardContent } from "@/components/ui/card";
import { NODE_TYPE_ENTRIES, EDGE_TYPE_ENTRIES } from "@/lib/graph-utils";

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

interface GraphLegendProps {
  hiddenNodeTypes: ReadonlySet<string>;
  hiddenEdgeTypes: ReadonlySet<string>;
  hideRootNodes: boolean;
  onToggleNodeType: (key: string) => void;
  onToggleEdgeType: (key: string) => void;
  onShowAllNodeTypes: () => void;
  onHideAllNodeTypes: () => void;
  onShowAllEdgeTypes: () => void;
  onHideAllEdgeTypes: () => void;
  onToggleRootNodes: () => void;
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export default function GraphLegend({
  hiddenNodeTypes,
  hiddenEdgeTypes,
  hideRootNodes,
  onToggleNodeType,
  onToggleEdgeType,
  onShowAllNodeTypes,
  onHideAllNodeTypes,
  onShowAllEdgeTypes,
  onHideAllEdgeTypes,
  onToggleRootNodes,
}: GraphLegendProps) {
  return (
    <Card className="w-52 bg-card/75 py-3 backdrop-blur-sm">
      <CardContent className="space-y-3 px-3 py-0">
        {/* Node type filters */}
        <div>
          <div className="mb-1.5 flex items-center justify-between">
            <h4 className="text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
              Node Type
            </h4>
            <span className="flex gap-1 text-[10px]">
              <button
                className="text-muted-foreground hover:text-foreground transition-colors"
                onClick={onShowAllNodeTypes}
              >
                All
              </button>
              <span className="text-muted-foreground/50">/</span>
              <button
                className="text-muted-foreground hover:text-foreground transition-colors"
                onClick={onHideAllNodeTypes}
              >
                None
              </button>
            </span>
          </div>
          <div className="space-y-0.5">
            {NODE_TYPE_ENTRIES.map(({ key, label, color }) => {
              const hidden = hiddenNodeTypes.has(key);
              return (
                <button
                  key={key}
                  className="flex w-full items-center gap-2 rounded px-1 py-0.5 text-left transition-colors hover:bg-accent/50"
                  onClick={() => onToggleNodeType(key)}
                >
                  <span
                    className="inline-block size-3 shrink-0 rounded-full transition-opacity"
                    style={{
                      backgroundColor: color,
                      opacity: hidden ? 0.2 : 1,
                    }}
                  />
                  <span
                    className={`text-[11px] transition-colors ${
                      hidden
                        ? "text-muted-foreground/40 line-through"
                        : "text-muted-foreground"
                    }`}
                  >
                    {label}
                  </span>
                </button>
              );
            })}
            {/* Root nodes toggle */}
            <button
              className="flex w-full items-center gap-2 rounded px-1 py-0.5 text-left transition-colors hover:bg-accent/50"
              onClick={onToggleRootNodes}
            >
              <span
                className="inline-block size-3 shrink-0 rounded transition-opacity"
                style={{
                  border: "1.5px dashed #6b7280",
                  opacity: hideRootNodes ? 0.2 : 1,
                }}
              />
              <span
                className={`text-[11px] transition-colors ${
                  hideRootNodes
                    ? "text-muted-foreground/40 line-through"
                    : "text-muted-foreground"
                }`}
              >
                Root Nodes
              </span>
            </button>
          </div>
        </div>

        {/* Edge type filters */}
        <div>
          <div className="mb-1.5 flex items-center justify-between">
            <h4 className="text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
              Edge Types
            </h4>
            <span className="flex gap-1 text-[10px]">
              <button
                className="text-muted-foreground hover:text-foreground transition-colors"
                onClick={onShowAllEdgeTypes}
              >
                All
              </button>
              <span className="text-muted-foreground/50">/</span>
              <button
                className="text-muted-foreground hover:text-foreground transition-colors"
                onClick={onHideAllEdgeTypes}
              >
                None
              </button>
            </span>
          </div>
          <div className="space-y-0.5">
            {EDGE_TYPE_ENTRIES.map(({ key, label, color }) => {
              const hidden = hiddenEdgeTypes.has(key);
              const isDashed = key === "parent";
              const isContradicts = key === "contradicts";
              return (
                <button
                  key={key}
                  className="flex w-full items-center gap-2 rounded px-1 py-0.5 text-left transition-colors hover:bg-accent/50"
                  onClick={() => onToggleEdgeType(key)}
                >
                  <span
                    className="inline-block h-0.5 w-3 shrink-0 rounded-full transition-opacity"
                    style={{
                      backgroundColor: color,
                      opacity: hidden ? 0.2 : 1,
                      ...(isDashed ? { borderTop: `2px dashed ${color}`, backgroundColor: "transparent", height: 0 } : {}),
                      ...(isContradicts ? { height: 3, borderRadius: 2 } : {}),
                    }}
                  />
                  <span
                    className={`text-[11px] transition-colors ${
                      hidden
                        ? "text-muted-foreground/40 line-through"
                        : "text-muted-foreground"
                    }`}
                  >
                    {label}
                  </span>
                </button>
              );
            })}
          </div>
        </div>

        {/* Size legend — unchanged */}
        <div>
          <h4 className="mb-1.5 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
            Node Size
          </h4>
          <div className="flex items-end gap-2">
            <span className="inline-block size-3 rounded-full border border-muted-foreground/40" />
            <span className="inline-block size-4 rounded-full border border-muted-foreground/40" />
            <span className="inline-block size-5 rounded-full border border-muted-foreground/40" />
            <span className="ml-1 text-[11px] text-muted-foreground">
              = connection balance
            </span>
          </div>
        </div>
      </CardContent>
    </Card>
  );
}
