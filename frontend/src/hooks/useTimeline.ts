"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import type {
  EdgeResponse,
  NodeResponse,
  TimelineEntry,
  TimelineSpeed,
} from "@/types";
import { computeStateAtPosition } from "@/lib/timeline-utils";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface UseTimelineResult {
  /** Current position. -1 means live (showing latest stream state). */
  position: number;
  /** Whether auto-play is running. */
  isPlaying: boolean;
  /** Current playback speed multiplier. */
  speed: TimelineSpeed;
  /** Whether the user is scrubbing (position !== -1). */
  isScrubbing: boolean;
  /** Nodes at the current timeline position (empty when live). */
  timelineNodes: NodeResponse[];
  /** Edges at the current timeline position (empty when live). */
  timelineEdges: EdgeResponse[];
  /** Total number of timeline entries. */
  total: number;
  /** The entry at the current position, if scrubbing. */
  currentEntry: TimelineEntry | null;

  seek: (position: number) => void;
  stepForward: () => void;
  stepBackward: () => void;
  togglePlay: () => void;
  setSpeed: (speed: TimelineSpeed) => void;
  goToLive: () => void;
}

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const BASE_INTERVAL_MS = 500;

// ---------------------------------------------------------------------------
// Hook
// ---------------------------------------------------------------------------

export function useTimeline(entries: TimelineEntry[]): UseTimelineResult {
  // position === -1 means "live" (not scrubbing)
  const [position, setPosition] = useState(-1);
  const [isPlaying, setIsPlaying] = useState(false);
  const [speed, setSpeedState] = useState<TimelineSpeed>(1);

  const total = entries.length;

  // Reset to live when entries drop to 0 (stream reset).
  // Using the "adjusting state when a prop changes" pattern:
  // https://react.dev/learn/you-might-not-need-an-effect#adjusting-some-state-when-a-prop-changes
  const [prevTotal, setPrevTotal] = useState(total);
  if (total !== prevTotal) {
    setPrevTotal(total);
    if (total === 0) {
      setPosition(-1);
      setIsPlaying(false);
    }
  }

  const isScrubbing = position !== -1;

  // Compute graph state at position
  const { nodes: timelineNodes, edges: timelineEdges } = useMemo(() => {
    if (!isScrubbing || entries.length === 0) {
      return { nodes: [] as NodeResponse[], edges: [] as EdgeResponse[] };
    }
    return computeStateAtPosition(entries, position);
  }, [entries, position, isScrubbing]);

  const currentEntry = isScrubbing ? (entries[position] ?? null) : null;

  // -------------------------------------------------------------------
  // Controls
  // -------------------------------------------------------------------

  const seek = useCallback(
    (pos: number) => {
      if (total === 0) return;
      const clamped = Math.max(0, Math.min(pos, total - 1));
      setPosition(clamped);
    },
    [total],
  );

  const stepForward = useCallback(() => {
    if (total === 0) return;
    setPosition((prev) => {
      const current = prev === -1 ? total - 1 : prev;
      if (current >= total - 1) return prev;
      return current + 1;
    });
  }, [total]);

  const stepBackward = useCallback(() => {
    if (total === 0) return;
    setPosition((prev) => {
      const current = prev === -1 ? total - 1 : prev;
      if (current <= 0) return 0;
      return current - 1;
    });
  }, [total]);

  const togglePlay = useCallback(() => {
    if (total === 0) return;
    setIsPlaying((prev) => {
      if (!prev) {
        // Starting play: if live or at end, start from beginning
        setPosition((pos) => {
          if (pos === -1 || pos >= total - 1) return 0;
          return pos;
        });
      }
      return !prev;
    });
  }, [total]);

  const setSpeed = useCallback((s: TimelineSpeed) => {
    setSpeedState(s);
  }, []);

  const goToLive = useCallback(() => {
    setPosition(-1);
    setIsPlaying(false);
  }, []);

  // -------------------------------------------------------------------
  // Auto-play interval
  // -------------------------------------------------------------------

  useEffect(() => {
    if (!isPlaying || total === 0) return;

    const intervalMs = BASE_INTERVAL_MS / speed;
    const timer = setInterval(() => {
      setPosition((prev) => {
        const current = prev === -1 ? 0 : prev;
        if (current >= total - 1) {
          // Reached end — snap to live and stop
          setIsPlaying(false);
          return -1;
        }
        return current + 1;
      });
    }, intervalMs);

    return () => clearInterval(timer);
  }, [isPlaying, speed, total]);

  return {
    position,
    isPlaying,
    speed,
    isScrubbing,
    timelineNodes,
    timelineEdges,
    total,
    currentEntry,
    seek,
    stepForward,
    stepBackward,
    togglePlay,
    setSpeed,
    goToLive,
  };
}
