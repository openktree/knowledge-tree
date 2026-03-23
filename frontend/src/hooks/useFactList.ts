"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import type { FactResponse } from "@/types";
import { api } from "@/lib/api";

export interface UseFactListResult {
  facts: FactResponse[];
  total: number;
  offset: number;
  search: string;
  factType: string | null;
  isLoading: boolean;
  error: string | null;
  setSearch: (query: string) => void;
  setFactType: (type: string | null) => void;
  setPage: (page: number) => void;
  refresh: () => void;
}

const PAGE_SIZE = 20;

export function useFactList(): UseFactListResult {
  const [facts, setFacts] = useState<FactResponse[]>([]);
  const [total, setTotal] = useState(0);
  const [offset, setOffset] = useState(0);
  const [search, setSearchRaw] = useState("");
  const [factType, setFactTypeRaw] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const [debouncedSearch, setDebouncedSearch] = useState("");

  const setSearch = useCallback((query: string) => {
    setSearchRaw(query);
    setOffset(0);
    if (debounceRef.current) clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(() => {
      setDebouncedSearch(query);
    }, 300);
  }, []);

  const setFactType = useCallback((type: string | null) => {
    setFactTypeRaw(type);
    setOffset(0);
  }, []);

  const setPage = useCallback((page: number) => {
    setOffset(page * PAGE_SIZE);
  }, []);

  const fetchData = useCallback(async () => {
    setIsLoading(true);
    setError(null);
    try {
      const result = await api.facts.list({
        offset,
        limit: PAGE_SIZE,
        search: debouncedSearch || undefined,
        fact_type: factType ?? undefined,
      });
      setFacts(result.items);
      setTotal(result.total);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load facts");
    } finally {
      setIsLoading(false);
    }
  }, [offset, debouncedSearch, factType]);

  useEffect(() => {
    fetchData();
  }, [fetchData]);

  return {
    facts,
    total,
    offset,
    search,
    factType,
    isLoading,
    error,
    setSearch,
    setFactType,
    setPage,
    refresh: fetchData,
  };
}
