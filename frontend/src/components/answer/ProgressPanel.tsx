"use client";

import { useState } from "react";
import { ChevronDown, ChevronUp, Check, Loader2, Circle } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import type { WaveProgressState, ScopePhase } from "@/types";

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

interface ProgressPanelProps {
  waves: WaveProgressState[];
}

// ---------------------------------------------------------------------------
// Phase pipeline display order
// ---------------------------------------------------------------------------

const PHASE_ORDER: ScopePhase[] = [
  "gathering",
  "searching",
  "enriching",
  "building",
  "classifying",
  "creating",
  "dimensions",
  "definitions",
  "edges",
  "parents",
  "complete",
];

function phaseIndex(phase: ScopePhase): number {
  const idx = PHASE_ORDER.indexOf(phase);
  return idx >= 0 ? idx : 0;
}

function phaseLabel(phase: ScopePhase): string {
  const labels: Record<ScopePhase, string> = {
    processing: "Processing",
    decomposition: "Decomposition",
    scout: "Scout",
    planning: "Planning",
    gathering: "Gathering",
    search_task: "Search Task",
    searching: "Searching",
    decompose_page: "Decompose Page",
    decompose_chunk: "Decompose Chunk",
    enriching: "Enriching Nodes",
    building: "Building",
    classifying: "Classifying",
    creating: "Creating",
    node_task: "Node Task",
    perspective_task: "Perspective Task",
    dimensions: "Dimensions",
    definitions: "Definitions",
    edges: "Edges",
    parents: "Parents",
    synthesis: "Synthesis",
    complete: "Done",
  };
  return labels[phase] ?? phase;
}

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

function WaveStatusIcon({ status }: { status: WaveProgressState["status"] }) {
  if (status === "complete") return <Check className="h-3 w-3 text-green-500" />;
  if (status === "running") return <Loader2 className="h-3 w-3 animate-spin text-blue-500" />;
  return <Circle className="h-3 w-3 text-muted-foreground" />;
}

function ScopeRow({ scope, phase }: { scope: string; phase: ScopePhase }) {
  const idx = phaseIndex(phase);
  const isDone = phase === "complete";

  return (
    <div className="flex items-center gap-2 text-xs">
      <span className="truncate max-w-[140px] text-muted-foreground" title={scope}>
        {scope}
      </span>
      <div className="flex items-center gap-0.5 flex-1">
        {PHASE_ORDER.slice(0, -1).map((p, i) => {
          const isCurrent = i === idx && !isDone;
          const isPast = i < idx || isDone;
          return (
            <div
              key={p}
              className={`h-1.5 flex-1 rounded-full transition-colors ${
                isPast
                  ? "bg-green-500/60"
                  : isCurrent
                    ? "bg-blue-500 animate-pulse"
                    : "bg-muted"
              }`}
              title={phaseLabel(p)}
            />
          );
        })}
      </div>
      <Badge variant="outline" className="text-[10px] px-1 py-0 h-4 shrink-0">
        {phaseLabel(phase)}
      </Badge>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function ProgressPanel({ waves }: ProgressPanelProps) {
  const [expanded, setExpanded] = useState(true);

  if (waves.length === 0) return null;

  const currentWave = waves[waves.length - 1];
  const scopeEntries = Object.values(currentWave.scopes);

  // Compact summary for collapsed state
  const compactLabel = scopeEntries.length > 0
    ? `Wave ${currentWave.wave}/${currentWave.totalWaves}: ${scopeEntries.map((s) => `${s.scope.slice(0, 20)}${s.scope.length > 20 ? "..." : ""} (${phaseLabel(s.phase)})`).join(", ")}`
    : `Wave ${currentWave.wave}/${currentWave.totalWaves}: planning...`;

  return (
    <div className="rounded-md border bg-card text-card-foreground px-3 py-2 text-xs space-y-1">
      <button
        type="button"
        onClick={() => setExpanded((v) => !v)}
        className="flex items-center gap-1.5 w-full text-left"
      >
        {expanded ? (
          <ChevronUp className="h-3 w-3 shrink-0 text-muted-foreground" />
        ) : (
          <ChevronDown className="h-3 w-3 shrink-0 text-muted-foreground" />
        )}
        {!expanded && (
          <span className="truncate text-muted-foreground">{compactLabel}</span>
        )}
        {expanded && (
          <span className="font-medium">Wave Progress</span>
        )}
      </button>

      {expanded && (
        <div className="space-y-2 pt-1">
          {waves.map((wave) => (
            <div key={wave.wave} className="space-y-1">
              <div className="flex items-center gap-1.5">
                <WaveStatusIcon status={wave.status} />
                <span className="font-medium">
                  Wave {wave.wave}/{wave.totalWaves}
                </span>
                {wave.status === "complete" && (
                  <span className="text-green-600 dark:text-green-400">complete</span>
                )}
              </div>
              {Object.values(wave.scopes).length > 0 ? (
                <div className="pl-4 space-y-1">
                  {Object.values(wave.scopes).map((s) => (
                    <ScopeRow key={s.scope} scope={s.scope} phase={s.phase} />
                  ))}
                </div>
              ) : wave.status !== "complete" ? (
                <div className="pl-4 text-muted-foreground">Planning...</div>
              ) : null}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
