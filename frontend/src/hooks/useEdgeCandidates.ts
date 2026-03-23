"use client";

import { useState, useEffect, useCallback } from "react";
import { api } from "@/lib/api";
import type {
  EdgeCandidatePairSummary,
  EdgeCandidatePairDetail,
} from "@/types";

export function useEdgeCandidateList(params?: {
  offset?: number;
  limit?: number;
  status?: string;
  search?: string;
  min_facts?: number;
}) {
  const [items, setItems] = useState<EdgeCandidatePairSummary[]>([]);
  const [total, setTotal] = useState(0);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const offset = params?.offset ?? 0;
  const limit = params?.limit ?? 20;
  const status = params?.status;
  const search = params?.search;
  const minFacts = params?.min_facts;

  const fetchData = useCallback(async () => {
    setIsLoading(true);
    setError(null);
    try {
      const result = await api.edgeCandidates.list({
        offset,
        limit,
        status,
        search,
        min_facts: minFacts,
      });
      setItems(result.items);
      setTotal(result.total);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load edge candidates");
    } finally {
      setIsLoading(false);
    }
  }, [offset, limit, status, search, minFacts]);

  useEffect(() => {
    fetchData();
  }, [fetchData]);

  return { items, total, isLoading, error, refetch: fetchData };
}

export function useEdgeCandidateDetail(seedKeyA: string, seedKeyB: string) {
  const [detail, setDetail] = useState<EdgeCandidatePairDetail | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const fetchData = useCallback(async () => {
    setIsLoading(true);
    setError(null);
    try {
      const result = await api.edgeCandidates.get(seedKeyA, seedKeyB);
      setDetail(result);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load pair detail");
    } finally {
      setIsLoading(false);
    }
  }, [seedKeyA, seedKeyB]);

  useEffect(() => {
    fetchData();
  }, [fetchData]);

  return { detail, isLoading, error, refetch: fetchData };
}

export function useSeedEdgeCandidates(
  seedKey: string,
  params?: { offset?: number; limit?: number },
) {
  const [items, setItems] = useState<EdgeCandidatePairSummary[]>([]);
  const [total, setTotal] = useState(0);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const offset = params?.offset ?? 0;
  const limit = params?.limit ?? 20;

  const fetchData = useCallback(async () => {
    setIsLoading(true);
    setError(null);
    try {
      const result = await api.edgeCandidates.bySeed(seedKey, { offset, limit });
      setItems(result.items);
      setTotal(result.total);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load candidates");
    } finally {
      setIsLoading(false);
    }
  }, [seedKey, offset, limit]);

  useEffect(() => {
    fetchData();
  }, [fetchData]);

  return { items, total, isLoading, error, refetch: fetchData };
}
