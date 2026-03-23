"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import type { EdgeResponse } from "@/types";
import { api } from "@/lib/api";

export interface UseEdgeListResult {
  edges: EdgeResponse[];
  total: number;
  offset: number;
  search: string;
  relationshipType: string | null;
  isLoading: boolean;
  error: string | null;
  setSearch: (query: string) => void;
  setRelationshipType: (type: string | null) => void;
  setPage: (page: number) => void;
  refresh: () => void;
}

const PAGE_SIZE = 20;

export function useEdgeList(): UseEdgeListResult {
  const [edges, setEdges] = useState<EdgeResponse[]>([]);
  const [total, setTotal] = useState(0);
  const [offset, setOffset] = useState(0);
  const [search, setSearchRaw] = useState("");
  const [relationshipType, setRelationshipTypeRaw] = useState<string | null>(null);
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

  const setRelationshipType = useCallback((type: string | null) => {
    setRelationshipTypeRaw(type);
    setOffset(0);
  }, []);

  const setPage = useCallback((page: number) => {
    setOffset(page * PAGE_SIZE);
  }, []);

  const fetchData = useCallback(async () => {
    setIsLoading(true);
    setError(null);
    try {
      const result = await api.edges.list({
        offset,
        limit: PAGE_SIZE,
        search: debouncedSearch || undefined,
        relationship_type: relationshipType ?? undefined,
      });
      setEdges(result.items);
      setTotal(result.total);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load edges");
    } finally {
      setIsLoading(false);
    }
  }, [offset, debouncedSearch, relationshipType]);

  useEffect(() => {
    fetchData();
  }, [fetchData]);

  return {
    edges,
    total,
    offset,
    search,
    relationshipType,
    isLoading,
    error,
    setSearch,
    setRelationshipType,
    setPage,
    refresh: fetchData,
  };
}
