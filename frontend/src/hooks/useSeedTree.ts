"use client";

import { useState, useEffect } from "react";
import type { SeedTreeResponse } from "@/types";
import { api } from "@/lib/api";

export interface UseSeedTreeResult {
  tree: SeedTreeResponse | null;
  isLoading: boolean;
  error: string | null;
}

export function useSeedTree(seedKey: string | null): UseSeedTreeResult {
  const [tree, setTree] = useState<SeedTreeResponse | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!seedKey) {
      setTree(null);
      setIsLoading(false);
      setError(null);
      return;
    }

    let cancelled = false;

    async function fetchTree(key: string) {
      setIsLoading(true);
      setError(null);
      try {
        const data = await api.seeds.getTree(key);
        if (!cancelled) setTree(data);
      } catch (err) {
        if (!cancelled) {
          setError(
            err instanceof Error ? err.message : "Failed to fetch seed tree",
          );
        }
      } finally {
        if (!cancelled) setIsLoading(false);
      }
    }

    fetchTree(seedKey);
    return () => {
      cancelled = true;
    };
  }, [seedKey]);

  return { tree, isLoading, error };
}
