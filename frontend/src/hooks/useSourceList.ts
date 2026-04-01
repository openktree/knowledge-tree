"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import type { SourceResponse } from "@/types";
import { api } from "@/lib/api";

export interface UseSourceListResult {
  sources: SourceResponse[];
  total: number;
  offset: number;
  search: string;
  providerId: string | null;
  sortBy: string | null;
  hasProhibited: boolean | null;
  isSuperSource: boolean | null;
  fetchStatus: string | null;
  isLoading: boolean;
  error: string | null;
  setSearch: (query: string) => void;
  setProviderId: (id: string | null) => void;
  setSortBy: (sortBy: string | null) => void;
  setHasProhibited: (val: boolean | null) => void;
  setIsSuperSource: (val: boolean | null) => void;
  setFetchStatus: (val: string | null) => void;
  setPage: (page: number) => void;
  refresh: () => void;
}

const PAGE_SIZE = 20;

export function useSourceList(): UseSourceListResult {
  const [sources, setSources] = useState<SourceResponse[]>([]);
  const [total, setTotal] = useState(0);
  const [offset, setOffset] = useState(0);
  const [search, setSearchRaw] = useState("");
  const [providerId, setProviderIdRaw] = useState<string | null>(null);
  const [sortBy, setSortByRaw] = useState<string | null>(null);
  const [hasProhibited, setHasProhibitedRaw] = useState<boolean | null>(null);
  const [isSuperSource, setIsSuperSourceRaw] = useState<boolean | null>(null);
  const [fetchStatus, setFetchStatusRaw] = useState<string | null>(null);
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

  const setProviderId = useCallback((id: string | null) => {
    setProviderIdRaw(id);
    setOffset(0);
  }, []);

  const setSortBy = useCallback((val: string | null) => {
    setSortByRaw(val);
    setOffset(0);
  }, []);

  const setHasProhibited = useCallback((val: boolean | null) => {
    setHasProhibitedRaw(val);
    setOffset(0);
  }, []);

  const setIsSuperSource = useCallback((val: boolean | null) => {
    setIsSuperSourceRaw(val);
    setOffset(0);
  }, []);

  const setFetchStatus = useCallback((val: string | null) => {
    setFetchStatusRaw(val);
    setOffset(0);
  }, []);

  const setPage = useCallback((page: number) => {
    setOffset(page * PAGE_SIZE);
  }, []);

  const fetchData = useCallback(async () => {
    setIsLoading(true);
    setError(null);
    try {
      const result = await api.sources.list({
        offset,
        limit: PAGE_SIZE,
        search: debouncedSearch || undefined,
        provider_id: providerId ?? undefined,
        sort_by: sortBy ?? undefined,
        has_prohibited: hasProhibited ?? undefined,
        is_super_source: isSuperSource ?? undefined,
        fetch_status: fetchStatus ?? undefined,
      });
      setSources(result.items);
      setTotal(result.total);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load sources");
    } finally {
      setIsLoading(false);
    }
  }, [offset, debouncedSearch, providerId, sortBy, hasProhibited, isSuperSource, fetchStatus]);

  useEffect(() => {
    fetchData();
  }, [fetchData]);

  return {
    sources,
    total,
    offset,
    search,
    providerId,
    sortBy,
    hasProhibited,
    isSuperSource,
    fetchStatus,
    isLoading,
    error,
    setSearch,
    setProviderId,
    setSortBy,
    setHasProhibited,
    setIsSuperSource,
    setFetchStatus,
    setPage,
    refresh: fetchData,
  };
}
