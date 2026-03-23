"use client";

import { useState, useEffect, useCallback } from "react";
import type { SeedDetailResponse } from "@/types";
import { api } from "@/lib/api";

export interface UseSeedDetailResult {
  seed: SeedDetailResponse | null;
  isLoading: boolean;
  error: string | null;
  refetch: () => Promise<void>;
}

export function useSeedDetail(seedKey: string | null): UseSeedDetailResult {
  const [seed, setSeed] = useState<SeedDetailResponse | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const fetchSeed = useCallback(async () => {
    if (!seedKey) {
      setSeed(null);
      setIsLoading(false);
      setError(null);
      return;
    }

    setIsLoading(true);
    setError(null);
    try {
      const data = await api.seeds.get(seedKey);
      setSeed(data);
    } catch (err) {
      setError(
        err instanceof Error ? err.message : "Failed to fetch seed details",
      );
    } finally {
      setIsLoading(false);
    }
  }, [seedKey]);

  useEffect(() => {
    let cancelled = false;

    async function load() {
      if (!seedKey) {
        setSeed(null);
        setIsLoading(false);
        setError(null);
        return;
      }

      setIsLoading(true);
      setError(null);
      try {
        const data = await api.seeds.get(seedKey);
        if (!cancelled) setSeed(data);
      } catch (err) {
        if (!cancelled) {
          setError(
            err instanceof Error ? err.message : "Failed to fetch seed details",
          );
        }
      } finally {
        if (!cancelled) setIsLoading(false);
      }
    }

    load();
    return () => {
      cancelled = true;
    };
  }, [seedKey]);

  return { seed, isLoading, error, refetch: fetchSeed };
}
