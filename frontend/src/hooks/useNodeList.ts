"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import type { NodeResponse } from "@/types";
import { api } from "@/lib/api";
import { useGraph } from "@/contexts/graph";

export interface UseNodeListResult {
  nodes: NodeResponse[];
  total: number;
  offset: number;
  search: string;
  nodeType: string;
  sort: string;
  isLoading: boolean;
  error: string | null;
  setSearch: (query: string) => void;
  setNodeType: (type: string) => void;
  setSort: (sort: string) => void;
  setPage: (page: number) => void;
  refresh: () => void;
}

const PAGE_SIZE = 20;

export function useNodeList(): UseNodeListResult {
  const { switchGeneration } = useGraph();
  const [nodes, setNodes] = useState<NodeResponse[]>([]);
  const [total, setTotal] = useState(0);
  const [offset, setOffset] = useState(0);
  const [search, setSearchRaw] = useState("");
  const [nodeType, setNodeTypeRaw] = useState("");
  const [sort, setSortRaw] = useState("edge_count");
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

  const setNodeType = useCallback((type: string) => {
    setNodeTypeRaw(type);
    setOffset(0);
  }, []);

  const setSort = useCallback((s: string) => {
    setSortRaw(s);
    setOffset(0);
  }, []);

  const setPage = useCallback((page: number) => {
    setOffset(page * PAGE_SIZE);
  }, []);

  const fetchData = useCallback(async () => {
    setIsLoading(true);
    setError(null);
    try {
      const result = await api.nodes.list({
        offset,
        limit: PAGE_SIZE,
        search: debouncedSearch || undefined,
        node_type: nodeType && nodeType !== "all" ? nodeType : undefined,
        sort: sort || undefined,
      });
      setNodes(result.items);
      setTotal(result.total);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load nodes");
    } finally {
      setIsLoading(false);
    }
  }, [offset, debouncedSearch, nodeType, sort]);

  useEffect(() => {
    fetchData();
  }, [fetchData]);

  // Refetch when the active graph changes
  useEffect(() => {
    setOffset(0);
    fetchData();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [switchGeneration]);

  return {
    nodes,
    total,
    offset,
    search,
    nodeType,
    sort,
    isLoading,
    error,
    setSearch,
    setNodeType,
    setSort,
    setPage,
    refresh: fetchData,
  };
}
