"use client";

import { useState, useEffect, useCallback, useRef } from "react";
import type { SeedResponse } from "@/types";
import { api } from "@/lib/api";

export interface UseSeedListResult {
  seeds: SeedResponse[];
  total: number;
  offset: number;
  search: string;
  status: string | null;
  nodeType: string | null;
  promotionThreshold: number;
  isLoading: boolean;
  error: string | null;
  setSearch: (query: string) => void;
  setStatus: (s: string | null) => void;
  setNodeType: (t: string | null) => void;
  setPage: (page: number) => void;
  refresh: () => void;
}

const PAGE_SIZE = 20;

export function useSeedList(): UseSeedListResult {
  const [seeds, setSeeds] = useState<SeedResponse[]>([]);
  const [total, setTotal] = useState(0);
  const [promotionThreshold, setPromotionThreshold] = useState(10);
  const [offset, setOffset] = useState(0);
  const [search, setSearchRaw] = useState("");
  const [status, setStatusRaw] = useState<string | null>(null);
  const [nodeType, setNodeTypeRaw] = useState<string | null>(null);
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

  const setStatus = useCallback((s: string | null) => {
    setStatusRaw(s);
    setOffset(0);
  }, []);

  const setNodeType = useCallback((t: string | null) => {
    setNodeTypeRaw(t);
    setOffset(0);
  }, []);

  const setPage = useCallback((page: number) => {
    setOffset(page * PAGE_SIZE);
  }, []);

  const fetchData = useCallback(async () => {
    setIsLoading(true);
    setError(null);
    try {
      const result = await api.seeds.list({
        offset,
        limit: PAGE_SIZE,
        search: debouncedSearch || undefined,
        status: status ?? undefined,
        node_type: nodeType ?? undefined,
      });
      setSeeds(result.items);
      setTotal(result.total);
      setPromotionThreshold(result.promotion_threshold);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load seeds");
    } finally {
      setIsLoading(false);
    }
  }, [offset, debouncedSearch, status, nodeType]);

  useEffect(() => {
    fetchData();
  }, [fetchData]);

  return {
    seeds,
    total,
    offset,
    search,
    status,
    nodeType,
    promotionThreshold,
    isLoading,
    error,
    setSearch,
    setStatus,
    setNodeType,
    setPage,
    refresh: fetchData,
  };
}
