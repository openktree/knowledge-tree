"use client";

import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

export interface PhaseIndicatorProps {
  /** Current query execution phase. */
  phase: string;
}

// ---------------------------------------------------------------------------
// Phase-to-style mapping
// ---------------------------------------------------------------------------

/**
 * Returns Tailwind classes for the badge background, text, and border based
 * on the current phase.
 */
function phaseStyles(phase: string): string {
  switch (phase) {
    case "pending":
    case "running":
      return "bg-secondary text-secondary-foreground border-secondary";
    case "navigating":
      return "bg-blue-100 text-blue-800 border-blue-200 dark:bg-blue-900/40 dark:text-blue-300 dark:border-blue-800";
    case "exploring":
      return "bg-purple-100 text-purple-800 border-purple-200 dark:bg-purple-900/40 dark:text-purple-300 dark:border-purple-800";
    case "decomposing":
      return "bg-orange-100 text-orange-800 border-orange-200 dark:bg-orange-900/40 dark:text-orange-300 dark:border-orange-800";
    case "building":
      return "bg-purple-100 text-purple-800 border-purple-200 dark:bg-purple-900/40 dark:text-purple-300 dark:border-purple-800";
    case "synthesizing":
      return "bg-amber-100 text-amber-800 border-amber-200 dark:bg-amber-900/40 dark:text-amber-300 dark:border-amber-800";
    case "completed":
      return "bg-green-100 text-green-800 border-green-200 dark:bg-green-900/40 dark:text-green-300 dark:border-green-800";
    case "failed":
      return "bg-red-100 text-red-800 border-red-200 dark:bg-red-900/40 dark:text-red-300 dark:border-red-800";
    default:
      return "bg-secondary text-secondary-foreground border-secondary";
  }
}

/**
 * Returns a human-readable label for the phase.
 */
function phaseLabel(phase: string): string {
  switch (phase) {
    case "pending":
      return "Pending";
    case "running":
      return "Running";
    case "navigating":
      return "Navigating";
    case "exploring":
      return "Exploring";
    case "decomposing":
      return "Decomposing";
    case "building":
      return "Building";
    case "synthesizing":
      return "Synthesizing";
    case "completed":
      return "Completed";
    case "failed":
      return "Failed";
    default:
      // Capitalize first letter for unknown phases.
      return phase.charAt(0).toUpperCase() + phase.slice(1);
  }
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function PhaseIndicator({ phase }: PhaseIndicatorProps) {
  return (
    <Badge
      variant="outline"
      className={cn("text-xs border", phaseStyles(phase))}
    >
      {phaseLabel(phase)}
    </Badge>
  );
}
