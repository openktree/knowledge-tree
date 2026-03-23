"use client";

import { Progress } from "@/components/ui/progress";
import {
  Tooltip,
  TooltipTrigger,
  TooltipContent,
} from "@/components/ui/tooltip";
// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

interface ScopeBudgetInfo {
  scope: string;
  explore_remaining: number;
  explore_total: number;
  nav_remaining: number;
  nav_total: number;
}

export interface BudgetDisplayProps {
  navRemaining: number;
  navTotal: number;
  exploreRemaining: number;
  exploreTotal: number;
  scopeBudgets?: Record<string, ScopeBudgetInfo>;
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function usedPercentage(remaining: number, total: number): number {
  if (total <= 0) return 0;
  const used = total - remaining;
  return Math.round(Math.max(0, Math.min(100, (used / total) * 100)));
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function BudgetDisplay({
  navRemaining,
  navTotal,
  exploreRemaining,
  exploreTotal,
  scopeBudgets,
}: BudgetDisplayProps) {
  const navUsed = navTotal - navRemaining;
  const exploreUsed = exploreTotal - exploreRemaining;

  const scopeEntries = scopeBudgets ? Object.values(scopeBudgets) : [];
  const hasActiveScopes = scopeEntries.length > 0;

  const bars = (
    <div className="flex flex-col gap-3 w-full">
      {/* Navigation budget */}
      <div className="flex flex-col gap-1.5">
        <div className="flex items-center justify-between text-sm">
          <span className="font-medium text-muted-foreground">Nav</span>
          <span className="tabular-nums font-semibold">
            {navUsed}/{navTotal}
          </span>
        </div>
        <Progress value={usedPercentage(navRemaining, navTotal)} />
      </div>

      {/* Explore budget (hidden when not applicable, e.g. ingest mode) */}
      {exploreTotal > 0 && (
        <div className="flex flex-col gap-1.5">
          <div className="flex items-center justify-between text-sm">
            <span className="font-medium text-muted-foreground">Explore</span>
            <span className="tabular-nums font-semibold">
              {exploreUsed}/{exploreTotal}
            </span>
          </div>
          <Progress value={usedPercentage(exploreRemaining, exploreTotal)} />
        </div>
      )}
    </div>
  );

  if (!hasActiveScopes) {
    return bars;
  }

  return (
    <Tooltip>
      <TooltipTrigger asChild>{bars}</TooltipTrigger>
      <TooltipContent
        side="bottom"
        className="w-72 p-3 flex flex-col gap-3"
      >
        <span className="text-xs font-semibold">
          Active Scopes ({scopeEntries.length})
        </span>
        {scopeEntries.map((scope) => {
          const scopeExploreUsed =
            scope.explore_total - scope.explore_remaining;
          const scopeNavUsed = scope.nav_total - scope.nav_remaining;
          return (
            <div key={scope.scope} className="flex flex-col gap-1.5">
              <span
                className="text-xs truncate"
                title={scope.scope}
              >
                {scope.scope}
              </span>
              <div className="flex items-center gap-2 text-xs">
                <span className="w-12 shrink-0 text-background/70">
                  Explore
                </span>
                <div className="flex-1">
                  <Progress
                    value={usedPercentage(
                      scope.explore_remaining,
                      scope.explore_total,
                    )}
                    className="h-1.5"
                  />
                </div>
                <span className="tabular-nums w-10 text-right">
                  {scopeExploreUsed}/{scope.explore_total}
                </span>
              </div>
              <div className="flex items-center gap-2 text-xs">
                <span className="w-12 shrink-0 text-background/70">Nav</span>
                <div className="flex-1">
                  <Progress
                    value={usedPercentage(
                      scope.nav_remaining,
                      scope.nav_total,
                    )}
                    className="h-1.5"
                  />
                </div>
                <span className="tabular-nums w-10 text-right">
                  {scopeNavUsed}/{scope.nav_total}
                </span>
              </div>
            </div>
          );
        })}
      </TooltipContent>
    </Tooltip>
  );
}
