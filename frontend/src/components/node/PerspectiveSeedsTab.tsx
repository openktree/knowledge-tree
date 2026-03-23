"use client";

import { useState, useEffect, useCallback } from "react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Loader2, Sparkles, X } from "lucide-react";
import { api } from "@/lib/api";
import type { PerspectiveSeedPairResponse } from "@/types";

interface PerspectiveSeedsTabProps {
  sourceNodeId: string;
  onSynthesized?: () => void;
}

export function PerspectiveSeedsTab({
  sourceNodeId,
  onSynthesized,
}: PerspectiveSeedsTabProps) {
  const [seeds, setSeeds] = useState<PerspectiveSeedPairResponse[]>([]);
  const [total, setTotal] = useState(0);
  const [isLoading, setIsLoading] = useState(true);
  const [synthesizingKey, setSynthesizingKey] = useState<string | null>(null);
  const [dismissingKey, setDismissingKey] = useState<string | null>(null);

  const fetchSeeds = useCallback(async () => {
    setIsLoading(true);
    try {
      const resp = await api.seeds.listPerspectives({
        source_node_id: sourceNodeId,
        limit: 50,
      });
      setSeeds(resp.items);
      setTotal(resp.total);
    } catch {
      // silently fail
    } finally {
      setIsLoading(false);
    }
  }, [sourceNodeId]);

  useEffect(() => {
    fetchSeeds();
  }, [fetchSeeds]);

  const handleSynthesize = async (seedKey: string) => {
    setSynthesizingKey(seedKey);
    try {
      await api.seeds.synthesizePerspective(seedKey);
      // Remove from list
      setSeeds((prev) => prev.filter((s) => s.thesis_key !== seedKey));
      setTotal((prev) => prev - 1);
      onSynthesized?.();
    } catch {
      // silently fail
    } finally {
      setSynthesizingKey(null);
    }
  };

  const handleDismiss = async (seedKey: string) => {
    setDismissingKey(seedKey);
    try {
      await api.seeds.dismissPerspective(seedKey);
      setSeeds((prev) => prev.filter((s) => s.thesis_key !== seedKey));
      setTotal((prev) => prev - 1);
    } catch {
      // silently fail
    } finally {
      setDismissingKey(null);
    }
  };

  if (isLoading) {
    return (
      <div className="flex items-center justify-center py-8">
        <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
      </div>
    );
  }

  if (seeds.length === 0) {
    return (
      <p className="text-sm text-muted-foreground py-4">
        No perspective seeds proposed yet.
      </p>
    );
  }

  return (
    <div className="space-y-3">
      <p className="text-xs text-muted-foreground">
        {total} perspective seed{total !== 1 ? "s" : ""} proposed.
        Synthesize to create full perspective nodes.
      </p>
      {seeds.map((seed) => (
        <div
          key={seed.thesis_key}
          className="rounded-md border p-3 space-y-2"
        >
          <div className="space-y-1">
            <div className="flex items-start gap-2">
              <Badge className="bg-blue-100 text-blue-800 dark:bg-blue-900 dark:text-blue-200 shrink-0 text-[10px]">
                Thesis
              </Badge>
              <p className="text-sm leading-snug">{seed.thesis_claim}</p>
            </div>
            {seed.antithesis_claim && (
              <div className="flex items-start gap-2">
                <Badge className="bg-red-100 text-red-800 dark:bg-red-900 dark:text-red-200 shrink-0 text-[10px]">
                  Antithesis
                </Badge>
                <p className="text-sm leading-snug">{seed.antithesis_claim}</p>
              </div>
            )}
          </div>

          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <Badge variant="outline" className="text-[10px]">
                {seed.fact_count} fact{seed.fact_count !== 1 ? "s" : ""}
              </Badge>
              {seed.scope_description && (
                <span className="text-[10px] text-muted-foreground truncate max-w-48">
                  {seed.scope_description}
                </span>
              )}
            </div>
            <div className="flex items-center gap-1">
              <Button
                variant="ghost"
                size="sm"
                className="h-6 text-xs gap-1 text-muted-foreground hover:text-destructive"
                onClick={() => handleDismiss(seed.thesis_key)}
                disabled={dismissingKey === seed.thesis_key}
              >
                {dismissingKey === seed.thesis_key ? (
                  <Loader2 className="h-3 w-3 animate-spin" />
                ) : (
                  <X className="h-3 w-3" />
                )}
                Dismiss
              </Button>
              <Button
                variant="default"
                size="sm"
                className="h-6 text-xs gap-1"
                onClick={() => handleSynthesize(seed.thesis_key)}
                disabled={synthesizingKey === seed.thesis_key}
              >
                {synthesizingKey === seed.thesis_key ? (
                  <Loader2 className="h-3 w-3 animate-spin" />
                ) : (
                  <Sparkles className="h-3 w-3" />
                )}
                Synthesize
              </Button>
            </div>
          </div>
        </div>
      ))}
    </div>
  );
}
