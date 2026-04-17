"use client";

import type { GraphResponse } from "@/types";

const REASON_COPY: Record<string, { title: string; body: string }> = {
  owner: {
    title: "Graph is read-only",
    body: "An owner has set this graph to read-only. Turn it off in graph settings to resume writes.",
  },
  migrating: {
    title: "Graph is migrating",
    body: "This graph is being upgraded to a new version of its type. Writes are paused until migration completes.",
  },
  error: {
    title: "Migration failed",
    body: "The last migration stopped with an error. A superadmin can re-dispatch the migration from graph settings.",
  },
};

interface GraphReadOnlyBannerProps {
  graph: Pick<GraphResponse, "read_only" | "read_only_reason" | "graph_type_info" | "graph_type_version">;
}

/**
 * Banner shown at the top of every graph-scoped page when the graph is
 * read-only. The copy is keyed on `read_only_reason`:
 *  - `owner`     — manual toggle, owner can flip it back in settings
 *  - `migrating` — system-set during `graph_migration_wf`
 *  - `error`     — migration halted; superadmin re-migrate required
 */
export function GraphReadOnlyBanner({ graph }: GraphReadOnlyBannerProps) {
  if (!graph.read_only) return null;
  const reason = graph.read_only_reason ?? "owner";
  const copy = REASON_COPY[reason] ?? REASON_COPY.owner;

  const versionBadge =
    reason === "migrating" && graph.graph_type_info
      ? ` (to v${graph.graph_type_info.current_version})`
      : "";

  return (
    <div
      role="alert"
      className="flex items-start gap-3 rounded-md border border-red-300 bg-red-50 px-4 py-3 text-sm text-red-900 dark:border-red-800 dark:bg-red-950/40 dark:text-red-100"
    >
      <div className="flex-1">
        <p className="font-semibold">
          {copy.title}
          {versionBadge}
        </p>
        <p className="mt-0.5 text-red-800/90 dark:text-red-200/90">{copy.body}</p>
      </div>
    </div>
  );
}
