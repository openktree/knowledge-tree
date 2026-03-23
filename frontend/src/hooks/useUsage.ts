"use client";

import { useCallback, useEffect, useState } from "react";
import type {
  UsageSummaryResponse,
  ConversationUsageResponse,
  ConversationUsageSummary,
  TokenUsageByModel,
} from "@/types";
import { api } from "@/lib/api";

export interface UseUsageSummaryResult {
  data: UsageSummaryResponse | null;
  isLoading: boolean;
  error: string | null;
  refresh: () => void;
}

export function useUsageSummary(since?: string, until?: string): UseUsageSummaryResult {
  const [data, setData] = useState<UsageSummaryResponse | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const fetch = useCallback(async () => {
    setIsLoading(true);
    setError(null);
    try {
      const result = await api.usage.getSummary(since, until);
      setData(result);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load usage");
    } finally {
      setIsLoading(false);
    }
  }, [since, until]);

  useEffect(() => {
    void fetch();
  }, [fetch]);

  return { data, isLoading, error, refresh: fetch };
}

export interface UseUsageByModelResult {
  data: TokenUsageByModel[];
  isLoading: boolean;
  error: string | null;
  refresh: () => void;
}

export function useUsageByModel(since?: string, until?: string): UseUsageByModelResult {
  const [data, setData] = useState<TokenUsageByModel[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const fetch = useCallback(async () => {
    setIsLoading(true);
    setError(null);
    try {
      const result = await api.usage.getByModel(since, until);
      setData(result);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load usage");
    } finally {
      setIsLoading(false);
    }
  }, [since, until]);

  useEffect(() => {
    void fetch();
  }, [fetch]);

  return { data, isLoading, error, refresh: fetch };
}

export interface UseUsageByConversationResult {
  data: ConversationUsageSummary[];
  isLoading: boolean;
  error: string | null;
  refresh: () => void;
}

export function useUsageByConversation(since?: string, until?: string): UseUsageByConversationResult {
  const [data, setData] = useState<ConversationUsageSummary[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const fetch = useCallback(async () => {
    setIsLoading(true);
    setError(null);
    try {
      const result = await api.usage.getByConversation(since, until);
      setData(result);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load usage");
    } finally {
      setIsLoading(false);
    }
  }, [since, until]);

  useEffect(() => {
    void fetch();
  }, [fetch]);

  return { data, isLoading, error, refresh: fetch };
}

export interface UseConversationUsageResult {
  data: ConversationUsageResponse | null;
  isLoading: boolean;
  error: string | null;
  refresh: () => void;
}

export function useConversationUsage(
  conversationId: string | null,
): UseConversationUsageResult {
  const [data, setData] = useState<ConversationUsageResponse | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const fetch = useCallback(async () => {
    if (!conversationId) return;
    setIsLoading(true);
    setError(null);
    try {
      const result = await api.usage.getConversationUsage(conversationId);
      setData(result);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load usage");
    } finally {
      setIsLoading(false);
    }
  }, [conversationId]);

  useEffect(() => {
    void fetch();
  }, [fetch]);

  return { data, isLoading, error, refresh: fetch };
}
