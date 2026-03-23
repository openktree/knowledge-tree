"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type {
  ConversationMessageResponse,
  ConversationResponse,
  EdgeResponse,
  NodeResponse,
  SubgraphResponse,
} from "@/types";
import { api } from "@/lib/api";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export type ConversationPhase =
  | "pending"
  | "running"
  | "completed"
  | "failed";

export interface BudgetState {
  nav_remaining: number;
  nav_total: number;
  explore_remaining: number;
  explore_total: number;
}

export interface UseConversationResult {
  conversation: ConversationResponse | null;
  conversationMode: string;
  messages: ConversationMessageResponse[];
  activeTurnPhase: ConversationPhase;
  activeTurnAnswer: string;
  activeTurnBudgets: BudgetState;
  nodes: NodeResponse[];
  edges: EdgeResponse[];
  isLoading: boolean;
  isTurnActive: boolean;
  isStoppingTurn: boolean;
  error: string | null;
  sendMessage: (
    message: string,
    navBudget?: number,
    exploreBudget?: number,
    waveCount?: number,
  ) => Promise<void>;
  resynthesizeMessage: (messageId: string) => Promise<void>;
  stopTurn: () => Promise<void>;
  updateTitle: (title: string) => Promise<void>;
  hideNode: (nodeId: string) => void;
  refreshProgress: () => Promise<void>;
}

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const POLL_INTERVAL_MS = 30_000;

const DEFAULT_BUDGETS: BudgetState = {
  nav_remaining: 0,
  nav_total: 0,
  explore_remaining: 0,
  explore_total: 0,
};

// ---------------------------------------------------------------------------
// Hook
// ---------------------------------------------------------------------------

export function useConversation(
  conversationId: string,
): UseConversationResult {
  const [conversation, setConversation] =
    useState<ConversationResponse | null>(null);
  const [messages, setMessages] = useState<ConversationMessageResponse[]>([]);
  const [activeTurnPhase, setActiveTurnPhase] =
    useState<ConversationPhase>("completed");
  const [activeTurnAnswer, setActiveTurnAnswer] = useState("");
  const [activeTurnBudgets, setActiveTurnBudgets] =
    useState<BudgetState>(DEFAULT_BUDGETS);
  const [nodes, setNodes] = useState<NodeResponse[]>([]);
  const [edges, setEdges] = useState<EdgeResponse[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [isStoppingTurn, setIsStoppingTurn] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const isTurnActive =
    activeTurnPhase === "pending" || activeTurnPhase === "running";

  const mountedRef = useRef(true);
  const pollTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const isTurnActiveRef = useRef(isTurnActive);
  // Track created_nodes to detect graph changes between polls
  const lastCreatedNodesRef = useRef<string>("");

  useEffect(() => {
    isTurnActiveRef.current = isTurnActive;
  }, [isTurnActive]);

  // Derive the latest assistant message ID for progress polling
  const latestAssistantMessageId = useMemo(() => {
    for (let i = messages.length - 1; i >= 0; i--) {
      if (messages[i].role === "assistant") return messages[i].id;
    }
    return null;
  }, [messages]);

  // -------------------------------------------------------------------
  // Merge subgraph data into accumulated nodes/edges
  // -------------------------------------------------------------------

  const mergeSubgraph = useCallback((sg: SubgraphResponse | null) => {
    if (!sg) return;
    if (Array.isArray(sg.nodes) && sg.nodes.length > 0) {
      setNodes((prev) => {
        const existingIds = new Set(prev.map((n) => n.id));
        const newNodes = sg.nodes.filter((n) => !existingIds.has(n.id));
        return newNodes.length > 0 ? [...prev, ...newNodes] : prev;
      });
    }
    if (Array.isArray(sg.edges)) {
      setEdges((prev) => {
        const sgNodeIds = new Set(sg.nodes.map((n) => n.id));
        const sgEdgeIds = new Set(sg.edges.map((e) => e.id));
        const kept = prev.filter(
          (e) =>
            sgEdgeIds.has(e.id) ||
            !sgNodeIds.has(e.source_node_id) ||
            !sgNodeIds.has(e.target_node_id),
        );
        const keptIds = new Set(kept.map((e) => e.id));
        const added = sg.edges.filter((e) => !keptIds.has(e.id));
        return added.length > 0 || kept.length !== prev.length
          ? [...kept, ...added]
          : prev;
      });
    }
  }, []);

  // -------------------------------------------------------------------
  // Load graph from a completed message
  // -------------------------------------------------------------------

  const loadGraphFromMessage = useCallback(
    (msg: ConversationMessageResponse, cancelledRef?: { current: boolean }) => {
      const hasSavedSubgraph =
        msg.subgraph &&
        Array.isArray(msg.subgraph.nodes) &&
        msg.subgraph.nodes.length > 0;

      if (hasSavedSubgraph) {
        mergeSubgraph(msg.subgraph!);
      } else if (msg.created_nodes && msg.created_nodes.length > 0) {
        api.graph
          .getSubgraph(msg.created_nodes, 1)
          .then((sg) => {
            if (cancelledRef?.current === true) return;
            if (!mountedRef.current) return;
            mergeSubgraph(sg);
          })
          .catch(() => {});
      }
    },
    [mergeSubgraph],
  );

  // -------------------------------------------------------------------
  // Poll progress for active turn
  // -------------------------------------------------------------------

  const pollProgress = useCallback(async () => {
    if (!mountedRef.current) return;

    const msgId = latestAssistantMessageId;
    if (!msgId) return;

    try {
      const progress = await api.conversations.getProgress(
        conversationId,
        msgId,
      );
      if (!mountedRef.current) return;

      // Update budgets
      const navTotal = progress.nav_budget ?? 0;
      const navUsed = progress.nav_used ?? 0;
      const expTotal = progress.explore_budget ?? 0;
      const expUsed = progress.explore_used ?? 0;
      setActiveTurnBudgets({
        nav_total: navTotal,
        nav_remaining: navTotal - navUsed,
        explore_total: expTotal,
        explore_remaining: expTotal - expUsed,
      });

      // Detect graph changes — fetch subgraph when created_nodes change
      const createdKey = (progress.created_nodes ?? []).sort().join(",");
      if (
        createdKey &&
        createdKey !== lastCreatedNodesRef.current
      ) {
        lastCreatedNodesRef.current = createdKey;
        api.graph
          .getSubgraph(progress.created_nodes!, 1)
          .then((sg) => {
            if (mountedRef.current) mergeSubgraph(sg);
          })
          .catch(() => {});
      }

      // Handle completion
      if (progress.status === "completed") {
        setActiveTurnPhase("completed");
        if (progress.content) setActiveTurnAnswer(progress.content);
        if (progress.subgraph) mergeSubgraph(progress.subgraph);
        // Refresh full conversation to get final message state
        api.conversations
          .get(conversationId)
          .then((conv) => {
            if (mountedRef.current) {
              setConversation(conv);
              setMessages(conv.messages);
              for (const msg of conv.messages) {
                if (msg.role === "assistant" && msg.status === "completed") {
                  loadGraphFromMessage(msg);
                }
              }
            }
          })
          .catch(() => {});
      } else if (progress.status === "failed") {
        setActiveTurnPhase("failed");
        if (progress.error) setError(progress.error);
      } else if (progress.status === "running") {
        setActiveTurnPhase("running");
      }
    } catch {
      // Silently ignore poll errors — will retry on next interval
    }
  }, [conversationId, latestAssistantMessageId, mergeSubgraph, loadGraphFromMessage]);

  const refreshProgress = useCallback(async () => {
    await pollProgress();
  }, [pollProgress]);

  // -------------------------------------------------------------------
  // Initial load
  // -------------------------------------------------------------------

  useEffect(() => {
    mountedRef.current = true;
    let cancelled = false;

    async function load() {
      try {
        const conv = await api.conversations.get(conversationId);
        if (cancelled) return;
        setConversation(conv);
        setMessages(conv.messages);

        const cancelledRef = { current: false };
        for (const msg of conv.messages) {
          if (msg.role === "assistant" && msg.status === "completed") {
            loadGraphFromMessage(msg, cancelledRef);
          }
        }

        // Check if there's an active turn
        const lastMsg = conv.messages[conv.messages.length - 1];
        if (
          lastMsg &&
          lastMsg.role === "assistant" &&
          (lastMsg.status === "pending" || lastMsg.status === "running")
        ) {
          setActiveTurnPhase(
            lastMsg.status === "running" ? "running" : "pending",
          );
          const navTotal = lastMsg.nav_budget ?? 0;
          const expTotal = lastMsg.explore_budget ?? 0;
          const navUsed = lastMsg.nav_used ?? 0;
          const expUsed = lastMsg.explore_used ?? 0;
          setActiveTurnBudgets({
            nav_total: navTotal,
            nav_remaining: navTotal - navUsed,
            explore_total: expTotal,
            explore_remaining: expTotal - expUsed,
          });
        } else {
          setActiveTurnPhase("completed");
        }

        setIsLoading(false);
      } catch (err) {
        if (cancelled) return;
        setError(
          err instanceof Error ? err.message : "Failed to load conversation",
        );
        setIsLoading(false);
      }
    }

    load();

    return () => {
      cancelled = true;
      mountedRef.current = false;
    };
  }, [conversationId, loadGraphFromMessage]);

  // -------------------------------------------------------------------
  // Polling for active turn
  // -------------------------------------------------------------------

  useEffect(() => {
    if (!isTurnActive) return;

    // Do an immediate poll on turn start, then every POLL_INTERVAL_MS
    pollProgress();

    function schedulePoll() {
      pollTimerRef.current = setTimeout(() => {
        pollTimerRef.current = null;
        if (!mountedRef.current || !isTurnActiveRef.current) return;

        pollProgress().then(() => {
          if (mountedRef.current && isTurnActiveRef.current) {
            schedulePoll();
          }
        });
      }, POLL_INTERVAL_MS);
    }

    schedulePoll();

    return () => {
      if (pollTimerRef.current) {
        clearTimeout(pollTimerRef.current);
        pollTimerRef.current = null;
      }
    };
  }, [isTurnActive, pollProgress]);

  // -------------------------------------------------------------------
  // Send message
  // -------------------------------------------------------------------

  const sendMessage = useCallback(
    async (message: string, navBudget = 30, exploreBudget = 2, waveCount = 2) => {
      setError(null);
      setActiveTurnAnswer("");
      setActiveTurnBudgets(DEFAULT_BUDGETS);
      lastCreatedNodesRef.current = "";

      try {
        const msgResponse = await api.conversations.sendMessage(
          conversationId,
          {
            message,
            nav_budget: navBudget,
            explore_budget: exploreBudget,
            wave_count: waveCount,
          },
        );

        setMessages((prev) => {
          const userMsg: ConversationMessageResponse = {
            id: crypto.randomUUID(),
            turn_number: msgResponse.turn_number - 1,
            role: "user",
            content: message,
            nav_budget: null,
            explore_budget: null,
            nav_used: null,
            explore_used: null,
            visited_nodes: null,
            created_nodes: null,
            created_edges: null,
            subgraph: null,
            status: null,
            error: null,
            created_at: new Date().toISOString(),
          };
          return [...prev, userMsg, msgResponse];
        });

        setActiveTurnPhase("pending");
      } catch (err) {
        setError(
          err instanceof Error ? err.message : "Failed to send message",
        );
      }
    },
    [conversationId],
  );

  const resynthesizeMessage = useCallback(
    async (messageId: string) => {
      setError(null);
      setActiveTurnAnswer("");
      setActiveTurnBudgets(DEFAULT_BUDGETS);
      lastCreatedNodesRef.current = "";

      try {
        await api.conversations.resynthesize(conversationId, messageId);
        setActiveTurnPhase("pending");
      } catch (err) {
        setError(
          err instanceof Error ? err.message : "Failed to re-synthesize",
        );
      }
    },
    [conversationId],
  );

  const stopTurn = useCallback(async () => {
    const activeMsg = [...messages]
      .reverse()
      .find(
        (m) =>
          m.role === "assistant" &&
          (m.status === "pending" || m.status === "running"),
      );
    if (!activeMsg) return;

    setIsStoppingTurn(true);
    try {
      await api.conversations.stopTurn(conversationId, activeMsg.id);
      setActiveTurnPhase("failed");
      setError("Stopped by user");
    } catch (err) {
      setError(
        err instanceof Error ? err.message : "Failed to stop turn",
      );
    } finally {
      setIsStoppingTurn(false);
    }
  }, [conversationId, messages]);

  const hideNode = useCallback(
    (nodeId: string) => {
      setNodes((prev) => prev.filter((n) => n.id !== nodeId));
      setEdges((prev) =>
        prev.filter(
          (e) =>
            e.source_node_id !== nodeId && e.target_node_id !== nodeId,
        ),
      );
    },
    [],
  );

  const updateTitle = useCallback(
    async (title: string) => {
      try {
        const updated = await api.conversations.updateTitle(conversationId, {
          title,
        });
        if (mountedRef.current) {
          setConversation(updated);
        }
      } catch (err) {
        setError(
          err instanceof Error ? err.message : "Failed to update title",
        );
      }
    },
    [conversationId],
  );

  return {
    conversation,
    conversationMode: conversation?.mode ?? "query",
    messages,
    activeTurnPhase,
    activeTurnAnswer,
    activeTurnBudgets,
    nodes,
    edges,
    isLoading,
    isTurnActive,
    isStoppingTurn,
    error,
    sendMessage,
    resynthesizeMessage,
    stopTurn,
    updateTitle,
    hideNode,
    refreshProgress,
  };
}
