"use client";

import { useCallback, useEffect, useState } from "react";
import type { SourceInsightsResponse } from "@/types";
import { api } from "@/lib/api";

export interface UseSourceInsightsResult {
  data: SourceInsightsResponse | null;
  isLoading: boolean;
  error: string | null;
  refresh: () => void;
}

export function useSourceInsights(since?: string): UseSourceInsightsResult {
  const [data, setData] = useState<SourceInsightsResponse | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const fetch = useCallback(async () => {
    setIsLoading(true);
    setError(null);
    try {
      const result = await api.sources.getInsights(since);
      setData(result);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load source insights");
    } finally {
      setIsLoading(false);
    }
  }, [since]);

  useEffect(() => {
    void fetch();
  }, [fetch]);

  return { data, isLoading, error, refresh: fetch };
}
