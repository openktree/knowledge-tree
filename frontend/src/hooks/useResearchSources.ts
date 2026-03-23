"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { IngestSourceResponse } from "@/types";

interface UseResearchSourcesResult {
  sources: IngestSourceResponse[];
  isLoading: boolean;
  error: string | null;
}

export function useResearchSources(
  conversationId: string | null,
  mode: string | null,
): UseResearchSourcesResult {
  const [sources, setSources] = useState<IngestSourceResponse[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const isActive = !!conversationId && mode === "ingest";

  useEffect(() => {
    if (!isActive) return;

    let cancelled = false;

    async function fetchSources() {
      setIsLoading(true);
      setError(null);

      try {
        const data = await api.research.getSources(conversationId!);
        if (!cancelled) {
          setSources(data);
        }
      } catch (err) {
        if (!cancelled) {
          setError(
            err instanceof Error ? err.message : "Failed to load sources",
          );
        }
      } finally {
        if (!cancelled) {
          setIsLoading(false);
        }
      }
    }

    fetchSources();

    return () => {
      cancelled = true;
    };
  }, [conversationId, mode, isActive]);

  // Return empty when not active — avoids synchronous setState in effect
  if (!isActive) {
    return { sources: [], isLoading: false, error: null };
  }

  return { sources, isLoading, error };
}
