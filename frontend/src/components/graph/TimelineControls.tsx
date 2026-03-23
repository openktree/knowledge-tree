"use client";

import {
  Clock,
  ChevronLeft,
  ChevronRight,
  Play,
  Pause,
  Radio,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Slider } from "@/components/ui/slider";
import { Badge } from "@/components/ui/badge";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import type { TimelineEntry, TimelineSpeed } from "@/types";

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

export interface TimelineControlsProps {
  /** Current position (-1 = live). */
  position: number;
  /** Total number of timeline entries. */
  total: number;
  /** Whether auto-play is running. */
  isPlaying: boolean;
  /** Current playback speed. */
  speed: TimelineSpeed;
  /** Whether the user is scrubbing. */
  isScrubbing: boolean;
  /** The entry at the current position. */
  currentEntry: TimelineEntry | null;

  onSeek: (position: number) => void;
  onStepForward: () => void;
  onStepBackward: () => void;
  onTogglePlay: () => void;
  onSetSpeed: (speed: TimelineSpeed) => void;
  onGoToLive: () => void;
}

// ---------------------------------------------------------------------------
// Speed cycle helper
// ---------------------------------------------------------------------------

const SPEED_ORDER: TimelineSpeed[] = [0.5, 1, 2, 4];

function nextSpeed(current: TimelineSpeed): TimelineSpeed {
  const idx = SPEED_ORDER.indexOf(current);
  return SPEED_ORDER[(idx + 1) % SPEED_ORDER.length];
}

function speedLabel(speed: TimelineSpeed): string {
  return `${speed}x`;
}

// ---------------------------------------------------------------------------
// Event kind labels
// ---------------------------------------------------------------------------

const KIND_LABELS: Record<string, string> = {
  node_created: "Created",
  node_visited: "Visited",
  node_expanded: "Expanded",
  edge_created: "Edge",
};

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function TimelineControls({
  position,
  total,
  isPlaying,
  speed,
  isScrubbing,
  currentEntry,
  onSeek,
  onStepForward,
  onStepBackward,
  onTogglePlay,
  onSetSpeed,
  onGoToLive,
}: TimelineControlsProps) {
  if (total === 0) return null;

  const displayPos = isScrubbing ? position + 1 : total;

  return (
    <TooltipProvider>
      <div className="flex items-center gap-1 rounded-lg border bg-card/80 p-1 backdrop-blur-sm">
        {/* Clock icon */}
        <Clock className="ml-1 size-3.5 text-muted-foreground shrink-0" />

        {/* Step backward */}
        <Tooltip>
          <TooltipTrigger asChild>
            <Button
              variant="ghost"
              size="icon-xs"
              onClick={onStepBackward}
              disabled={!isScrubbing || position <= 0}
              aria-label="Step backward"
            >
              <ChevronLeft className="size-3" />
            </Button>
          </TooltipTrigger>
          <TooltipContent side="top">Step backward</TooltipContent>
        </Tooltip>

        {/* Play / Pause */}
        <Tooltip>
          <TooltipTrigger asChild>
            <Button
              variant="ghost"
              size="icon-xs"
              onClick={onTogglePlay}
              aria-label={isPlaying ? "Pause" : "Play"}
            >
              {isPlaying ? (
                <Pause className="size-3" />
              ) : (
                <Play className="size-3" />
              )}
            </Button>
          </TooltipTrigger>
          <TooltipContent side="top">
            {isPlaying ? "Pause" : "Play"}
          </TooltipContent>
        </Tooltip>

        {/* Step forward */}
        <Tooltip>
          <TooltipTrigger asChild>
            <Button
              variant="ghost"
              size="icon-xs"
              onClick={onStepForward}
              disabled={!isScrubbing || position >= total - 1}
              aria-label="Step forward"
            >
              <ChevronRight className="size-3" />
            </Button>
          </TooltipTrigger>
          <TooltipContent side="top">Step forward</TooltipContent>
        </Tooltip>

        {/* Separator */}
        <div className="mx-0.5 h-5 w-px bg-border" />

        {/* Slider */}
        <Slider
          min={0}
          max={Math.max(0, total - 1)}
          step={1}
          value={[isScrubbing ? position : total - 1]}
          onValueChange={([v]) => onSeek(v)}
          className="w-32"
          aria-label="Timeline position"
        />

        {/* Position counter */}
        <span className="text-xs text-muted-foreground tabular-nums whitespace-nowrap px-1">
          {displayPos}/{total}
        </span>

        {/* Current event badge */}
        {currentEntry && (
          <Badge variant="secondary" className="text-[10px] px-1.5 py-0">
            {KIND_LABELS[currentEntry.kind] ?? currentEntry.kind}
            {currentEntry.node && (
              <span className="ml-1 max-w-[8ch] truncate">
                {currentEntry.node.concept}
              </span>
            )}
          </Badge>
        )}

        {/* Separator */}
        <div className="mx-0.5 h-5 w-px bg-border" />

        {/* Speed cycle */}
        <Tooltip>
          <TooltipTrigger asChild>
            <Button
              variant="ghost"
              size="icon-xs"
              onClick={() => onSetSpeed(nextSpeed(speed))}
              aria-label="Change speed"
              className="text-[10px] font-mono w-8"
            >
              {speedLabel(speed)}
            </Button>
          </TooltipTrigger>
          <TooltipContent side="top">Playback speed</TooltipContent>
        </Tooltip>

        {/* LIVE button (only when scrubbing) */}
        {isScrubbing && (
          <Tooltip>
            <TooltipTrigger asChild>
              <Button
                variant="default"
                size="icon-xs"
                onClick={onGoToLive}
                aria-label="Go to live"
                className="gap-1 px-2 w-auto"
              >
                <Radio className="size-3" />
                <span className="text-[10px] font-semibold">LIVE</span>
              </Button>
            </TooltipTrigger>
            <TooltipContent side="top">Return to live view</TooltipContent>
          </Tooltip>
        )}
      </div>
    </TooltipProvider>
  );
}
