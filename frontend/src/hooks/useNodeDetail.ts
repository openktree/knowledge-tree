"use client";

import { useState, useEffect, useCallback } from "react";
import { api } from "@/lib/api";
import type {
  NodeResponse,
  DimensionResponse,
  FactResponse,
  EdgeResponse,
  NodeVersionResponse,
  ConvergenceResponse,
} from "@/types";

export interface PerspectivePair {
  thesis: NodeResponse;
  antithesis: NodeResponse;
}

export interface UseNodeDetailResult {
  node: NodeResponse | null;
  dimensions: DimensionResponse[];
  facts: FactResponse[];
  edges: EdgeResponse[];
  history: NodeVersionResponse[];
  convergence: ConvergenceResponse | null;
  perspectives: PerspectivePair[];
  isLoading: boolean;
  error: string | null;
  rebuildNode: (mode?: "full" | "incremental", scope?: "all" | "dimensions" | "edges") => Promise<void>;
  isRebuilding: boolean;
  refreshPerspectives: () => Promise<void>;
}

function groupIntoPairs(perspectives: NodeResponse[]): PerspectivePair[] {
  const pairs: PerspectivePair[] = [];
  const seen = new Set<string>();

  for (const p of perspectives) {
    if (seen.has(p.id)) continue;
    const role = p.metadata?.dialectic_role as string | undefined;
    const pairId = p.metadata?.dialectic_pair_id as string | undefined;

    if (role === "thesis" && pairId) {
      const antithesis = perspectives.find((q) => q.id === pairId);
      if (antithesis) {
        pairs.push({ thesis: p, antithesis });
        seen.add(p.id);
        seen.add(antithesis.id);
      }
    } else if (role === "antithesis" && pairId) {
      const thesis = perspectives.find((q) => q.id === pairId);
      if (thesis) {
        pairs.push({ thesis, antithesis: p });
        seen.add(p.id);
        seen.add(thesis.id);
      }
    }
  }

  return pairs;
}

export function useNodeDetail(nodeId: string | null): UseNodeDetailResult {
  const [node, setNode] = useState<NodeResponse | null>(null);
  const [dimensions, setDimensions] = useState<DimensionResponse[]>([]);
  const [facts, setFacts] = useState<FactResponse[]>([]);
  const [edges, setEdges] = useState<EdgeResponse[]>([]);
  const [history, setHistory] = useState<NodeVersionResponse[]>([]);
  const [convergence, setConvergence] = useState<ConvergenceResponse | null>(
    null
  );
  const [perspectives, setPerspectives] = useState<PerspectivePair[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [rebuildingNodeId, setRebuildingNodeId] = useState<string | null>(null);
  const isRebuilding = rebuildingNodeId === nodeId;
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!nodeId) {
      setNode(null);
      setDimensions([]);
      setFacts([]);
      setEdges([]);
      setHistory([]);
      setConvergence(null);
      setPerspectives([]);
      setIsLoading(false);
      setError(null);
      return;
    }

    let cancelled = false;

    async function fetchAll(id: string) {
      setIsLoading(true);
      setError(null);

      try {
        const [
          nodeData,
          dimensionsData,
          factsData,
          edgesData,
          historyData,
          convergenceData,
          perspectivesData,
        ] = await Promise.all([
          api.nodes.get(id),
          api.nodes.getDimensions(id),
          api.nodes.getFacts(id),
          api.nodes.getEdges(id),
          api.nodes.getHistory(id),
          api.nodes.getConvergence(id).catch(() => null),
          api.nodes.getPerspectives(id).catch(() => [] as NodeResponse[]),
        ]);

        if (cancelled) return;

        setNode(nodeData);
        setDimensions(dimensionsData);
        setFacts(factsData);
        setEdges(edgesData);
        setHistory(historyData);
        setConvergence(convergenceData);
        setPerspectives(groupIntoPairs(perspectivesData));
      } catch (err) {
        if (cancelled) return;
        setError(
          err instanceof Error ? err.message : "Failed to fetch node details"
        );
      } finally {
        if (!cancelled) {
          setIsLoading(false);
        }
      }
    }

    fetchAll(nodeId);

    return () => {
      cancelled = true;
    };
  }, [nodeId]);

  const refreshPerspectives = useCallback(async () => {
    if (!nodeId) return;
    try {
      const data = await api.nodes.getPerspectives(nodeId);
      setPerspectives(groupIntoPairs(data));
    } catch {
      // ignore — perspectives may not be available for this node type
    }
  }, [nodeId]);

  const rebuildNode = useCallback(async (
    mode: "full" | "incremental" = "full",
    scope: "all" | "dimensions" | "edges" = "all",
  ) => {
    if (!nodeId) return;
    setRebuildingNodeId(nodeId);
    try {
      await api.nodes.rebuildNode(nodeId, mode, scope);
      // Poll until updated_at changes or enrichment_status flips
      const baselineUpdatedAt = node?.updated_at;
      const POLL_INTERVAL = 4000;
      const MAX_POLLS = 30; // ~2 minutes max
      let polls = 0;
      const targetId = nodeId;

      const poll = async () => {
        polls++;
        try {
          const [freshNode, edgesData, dimsData, convergenceData] =
            await Promise.all([
              api.nodes.get(targetId),
              api.nodes.getEdges(targetId),
              api.nodes.getDimensions(targetId),
              api.nodes.getConvergence(targetId).catch(() => null),
            ]);
          const changed =
            !baselineUpdatedAt ||
            freshNode.updated_at !== baselineUpdatedAt;
          if (changed || polls >= MAX_POLLS) {
            setNode(freshNode);
            setEdges(edgesData);
            setDimensions(dimsData);
            setConvergence(convergenceData);
            setRebuildingNodeId(null);
            return;
          }
        } catch {
          if (polls >= MAX_POLLS) {
            setRebuildingNodeId(null);
            return;
          }
        }
        setTimeout(poll, POLL_INTERVAL);
      };

      setTimeout(poll, POLL_INTERVAL);
    } catch (err) {
      setError(
        err instanceof Error ? err.message : "Failed to rebuild node"
      );
      setRebuildingNodeId(null);
    }
  }, [nodeId, node]);

  return {
    node,
    dimensions,
    facts,
    edges,
    history,
    convergence,
    perspectives,
    isLoading,
    error,
    rebuildNode,
    isRebuilding,
    refreshPerspectives,
  };
}
