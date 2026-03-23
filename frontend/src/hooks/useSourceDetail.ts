"use client";

import { useState, useEffect, useCallback } from "react";
import { api } from "@/lib/api";
import type { SourceDetailResponse } from "@/types";

export interface UseSourceDetailResult {
  source: SourceDetailResponse | null;
  isLoading: boolean;
  error: string | null;
  refetch: () => void;
}

export function useSourceDetail(sourceId: string | null): UseSourceDetailResult {
  const [source, setSource] = useState<SourceDetailResponse | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [fetchKey, setFetchKey] = useState(0);

  const refetch = useCallback(() => {
    setFetchKey((k) => k + 1);
  }, []);

  useEffect(() => {
    if (!sourceId) {
      setSource(null);
      setIsLoading(false);
      setError(null);
      return;
    }

    let cancelled = false;

    async function fetchSource(id: string) {
      setIsLoading(true);
      setError(null);
      try {
        const data = await api.sources.get(id);
        if (!cancelled) setSource(data);
      } catch (err) {
        if (!cancelled) {
          setError(
            err instanceof Error ? err.message : "Failed to fetch source details"
          );
        }
      } finally {
        if (!cancelled) setIsLoading(false);
      }
    }

    fetchSource(sourceId);

    return () => {
      cancelled = true;
    };
  }, [sourceId, fetchKey]);

  return { source, isLoading, error, refetch };
}
