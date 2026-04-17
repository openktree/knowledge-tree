"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { ArrowLeft, Loader2 } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { GraphReadOnlyBanner } from "@/components/graphs/GraphReadOnlyBanner";
import { getGraph, setGraphReadOnly } from "@/lib/api";
import type { GraphResponse } from "@/types";

interface SettingsPageProps {
  params: Promise<{ slug: string }>;
}

/**
 * Phase 1 scaffold: banner + read-only toggle + type/version info card.
 * Composition display (Phase 2) + migration history (Phase 7) fill in later.
 */
export default function GraphSettingsPage({ params }: SettingsPageProps) {
  const [slug, setSlug] = useState<string | null>(null);
  const [graph, setGraph] = useState<GraphResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [toggling, setToggling] = useState(false);

  useEffect(() => {
    void params.then((p) => setSlug(p.slug));
  }, [params]);

  const refresh = useCallback(async () => {
    if (!slug) return;
    try {
      const data = await getGraph(slug);
      setGraph(data);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load graph");
    } finally {
      setLoading(false);
    }
  }, [slug]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const handleToggleReadOnly = async (nextValue: boolean) => {
    if (!slug) return;
    setToggling(true);
    try {
      const updated = await setGraphReadOnly(slug, { read_only: nextValue });
      setGraph(updated);
    } catch (err) {
      alert(err instanceof Error ? err.message : "Failed to toggle read-only");
    } finally {
      setToggling(false);
    }
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center py-12">
        <Loader2 className="size-5 animate-spin text-muted-foreground" />
      </div>
    );
  }
  if (error || !graph) {
    return <p className="text-sm text-destructive">Error: {error ?? "not found"}</p>;
  }

  const systemLocked =
    graph.read_only && graph.read_only_reason !== "owner";
  const currentVersion = graph.graph_type_info?.current_version ?? graph.graph_type_version;
  const versionBehind = graph.graph_type_version < currentVersion;

  return (
    <div className="mx-auto max-w-3xl space-y-6 py-6">
      <div className="flex items-center gap-3">
        <Link href={`/graphs/${graph.slug}`} className="text-muted-foreground hover:text-foreground">
          <ArrowLeft className="size-4" />
        </Link>
        <h1 className="text-lg font-semibold">{graph.name} — Settings</h1>
      </div>

      <GraphReadOnlyBanner graph={graph} />

      {/* Type & version card */}
      <section className="rounded-xl border border-border bg-card p-4">
        <h2 className="text-sm font-semibold mb-2">Type &amp; version</h2>
        <div className="flex items-center gap-2 text-sm">
          <Badge variant="outline">
            {graph.graph_type_info?.display_name ?? graph.graph_type_id}
          </Badge>
          <span className="text-muted-foreground">
            v{graph.graph_type_version}
            {versionBehind && ` of ${currentVersion}`}
          </span>
        </div>
        {versionBehind && (
          <p className="mt-2 text-xs text-muted-foreground">
            A newer version of this type is available. The migration runs automatically
            on the next startup — you&apos;ll see the read-only banner while it runs.
          </p>
        )}
      </section>

      {/* Read-only toggle card */}
      <section className="rounded-xl border border-border bg-card p-4">
        <h2 className="text-sm font-semibold mb-1">Read-only mode</h2>
        <p className="text-xs text-muted-foreground mb-3">
          Freezes writes to this graph. The banner above shows the current state for all
          graph-scoped pages.
        </p>
        <div className="flex items-center gap-3">
          <Button
            size="sm"
            variant={graph.read_only ? "secondary" : "default"}
            disabled={systemLocked || toggling}
            onClick={() => handleToggleReadOnly(!graph.read_only)}
          >
            {graph.read_only ? "Turn off read-only" : "Set to read-only"}
          </Button>
          {systemLocked && (
            <span className="text-xs text-muted-foreground">
              System-locked ({graph.read_only_reason}) — wait for the process to finish
              or ask a superadmin to re-migrate.
            </span>
          )}
        </div>
      </section>

      {/* Phase 2 placeholder: composition display */}
      <section className="rounded-xl border border-dashed border-border bg-card/50 p-4">
        <h2 className="text-sm font-semibold mb-1 text-muted-foreground">
          Composition (Phase 2)
        </h2>
        <p className="text-xs text-muted-foreground">
          Per-phase provider picks + resolved config will render here once the
          resolver ships. Config edits land via <code>config.yaml</code> under{" "}
          <code>graphs.{graph.slug}</code>.
        </p>
      </section>

      {/* Phase 7 placeholder: migration history */}
      <section className="rounded-xl border border-dashed border-border bg-card/50 p-4">
        <h2 className="text-sm font-semibold mb-1 text-muted-foreground">
          Migration history (Phase 7)
        </h2>
        <p className="text-xs text-muted-foreground">
          Version-upgrade audit rows will list here once the migration framework lands.
        </p>
      </section>
    </div>
  );
}
