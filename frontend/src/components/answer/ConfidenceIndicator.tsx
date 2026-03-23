"use client";

import { cn } from "@/lib/utils";

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

export interface ConfidenceIndicatorProps {
  /** Confidence / convergence score between 0 and 1. */
  score: number;
  /** Optional label displayed before the bar. */
  label?: string;
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function getColorClasses(score: number): string {
  if (score < 0.4) return "bg-red-500";
  if (score <= 0.7) return "bg-yellow-500";
  return "bg-green-500";
}

function getTrackClasses(score: number): string {
  if (score < 0.4) return "bg-red-500/20";
  if (score <= 0.7) return "bg-yellow-500/20";
  return "bg-green-500/20";
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function ConfidenceIndicator({
  score,
  label,
}: ConfidenceIndicatorProps) {
  const clampedScore = Math.max(0, Math.min(1, score));
  const percentage = Math.round(clampedScore * 100);

  return (
    <div className="flex items-center gap-3 w-full">
      {label && (
        <span className="text-sm font-medium text-muted-foreground shrink-0">
          {label}
        </span>
      )}
      <div
        className={cn(
          "relative h-2.5 flex-1 overflow-hidden rounded-full",
          getTrackClasses(clampedScore),
        )}
        role="meter"
        aria-valuenow={percentage}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-label={label ? `${label}: ${percentage}%` : `${percentage}%`}
      >
        <div
          className={cn(
            "h-full rounded-full transition-all duration-300",
            getColorClasses(clampedScore),
          )}
          style={{ width: `${percentage}%` }}
        />
      </div>
      <span className="text-sm font-semibold tabular-nums shrink-0">
        {percentage}%
      </span>
    </div>
  );
}
