"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { NodeResponse, EdgeResponse } from "@/types";
import { api } from "@/lib/api";

export interface UseGraphExplorerResult {
  nodes: NodeResponse[];
  edges: EdgeResponse[];
  seedIds: ReadonlySet<string>;
  selectedNodeId: string | null;
  neighborDepth: number;
  isLoading: boolean;
  error: string | null;
  searchAndAdd: (query: string) => Promise<void>;
  addSeedById: (nodeId: string) => Promise<void>;
  expandNode: (nodeId: string) => Promise<void>;
  /** Select a node, expand its neighbors, and signal the view to center on it. */
  navigateToNode: (nodeId: string) => Promise<void>;
  setNeighborDepth: (depth: number) => Promise<void> | void;
  selectNode: (nodeId: string | null) => void;
  removeFromView: (nodeId: string) => void;
  clearView: () => void;
  /** Fetch any missing node IDs and merge them (with their edges) into the view. */
  ensureNodesInView: (nodeIds: string[], extraEdges?: EdgeResponse[]) => Promise<void>;
}

export function useGraphExplorer(
  initialSeedIds?: string[],
): UseGraphExplorerResult {
  const [nodeMap, setNodeMap] = useState<Map<string, NodeResponse>>(new Map());
  const [edges, setEdges] = useState<EdgeResponse[]>([]);
  const [seedIds, setSeedIds] = useState<Set<string>>(new Set());
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);
  const [neighborDepth, setNeighborDepthRaw] = useState(1);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const nodes = useMemo(() => Array.from(nodeMap.values()), [nodeMap]);

  // Use a ref to track whether we've already loaded initial seeds,
  // so we only do it once even if the prop reference changes.
  const initialLoadedRef = useRef(false);

  useEffect(() => {
    if (initialLoadedRef.current) return;
    if (!initialSeedIds || initialSeedIds.length === 0) return;
    initialLoadedRef.current = true;

    const load = async () => {
      setIsLoading(true);
      setError(null);
      try {
        const subgraph = await api.graph.getSubgraph(initialSeedIds, neighborDepth);
        const newNodeMap = new Map<string, NodeResponse>();
        for (const node of subgraph.nodes) {
          newNodeMap.set(node.id, node);
        }
        setNodeMap(newNodeMap);
        setEdges(subgraph.edges);
        setSeedIds(new Set(initialSeedIds));
      } catch (err) {
        setError(err instanceof Error ? err.message : "Failed to load seeds");
      } finally {
        setIsLoading(false);
      }
    };
    load();
  }, [initialSeedIds, neighborDepth]);

  /** Fetch subgraph for the given seed IDs and replace the full view. */
  const fetchSubgraph = useCallback(
    async (seeds: Set<string>, depth: number) => {
      const ids = [...seeds];
      if (ids.length === 0) return;
      const subgraph = await api.graph.getSubgraph(ids, depth);
      const newNodeMap = new Map<string, NodeResponse>();
      for (const node of subgraph.nodes) {
        newNodeMap.set(node.id, node);
      }
      setNodeMap(newNodeMap);
      setEdges(subgraph.edges);
    },
    [],
  );

  const searchAndAdd = useCallback(
    async (query: string) => {
      setIsLoading(true);
      setError(null);
      try {
        const results = await api.nodes.search(query);
        if (results.length === 0) {
          setError("No nodes found");
          return;
        }
        const newSeedIds = new Set(seedIds);
        for (const node of results) {
          newSeedIds.add(node.id);
        }
        setSeedIds(newSeedIds);
        await fetchSubgraph(newSeedIds, neighborDepth);
      } catch (err) {
        setError(err instanceof Error ? err.message : "Search failed");
      } finally {
        setIsLoading(false);
      }
    },
    [seedIds, neighborDepth, fetchSubgraph],
  );

  const addSeedById = useCallback(
    async (nodeId: string) => {
      setIsLoading(true);
      setError(null);
      try {
        const newSeedIds = new Set(seedIds);
        newSeedIds.add(nodeId);
        setSeedIds(newSeedIds);
        await fetchSubgraph(newSeedIds, neighborDepth);
      } catch (err) {
        setError(err instanceof Error ? err.message : "Failed to add node");
      } finally {
        setIsLoading(false);
      }
    },
    [seedIds, neighborDepth, fetchSubgraph],
  );

  const expandNode = useCallback(
    async (nodeId: string) => {
      setIsLoading(true);
      setError(null);
      try {
        const subgraph = await api.graph.getSubgraph([nodeId], neighborDepth);
        setNodeMap((prev) => {
          const next = new Map(prev);
          for (const node of subgraph.nodes) {
            next.set(node.id, node);
          }
          return next;
        });
        setEdges((prev) => {
          const existingIds = new Set(prev.map((e) => e.id));
          const newEdges = subgraph.edges.filter(
            (e) => !existingIds.has(e.id),
          );
          return [...prev, ...newEdges];
        });
      } catch (err) {
        setError(err instanceof Error ? err.message : "Expand failed");
      } finally {
        setIsLoading(false);
      }
    },
    [neighborDepth],
  );

  const handleNeighborDepthChange = useCallback(
    async (depth: number) => {
      setNeighborDepthRaw(depth);
      if (seedIds.size === 0) return;
      setIsLoading(true);
      setError(null);
      try {
        await fetchSubgraph(seedIds, depth);
      } catch (err) {
        setError(err instanceof Error ? err.message : "Depth change failed");
      } finally {
        setIsLoading(false);
      }
    },
    [seedIds, fetchSubgraph],
  );

  const selectNode = useCallback((nodeId: string | null) => {
    setSelectedNodeId(nodeId);
  }, []);

  const navigateToNode = useCallback(
    async (nodeId: string) => {
      // Skip re-fetch if already viewing this node as the sole seed
      if (selectedNodeId === nodeId && seedIds.size === 1 && seedIds.has(nodeId)) {
        return;
      }
      setSelectedNodeId(nodeId);
      setIsLoading(true);
      setError(null);
      try {
        const newSeeds = new Set([nodeId]);
        setSeedIds(newSeeds);
        await fetchSubgraph(newSeeds, neighborDepth);
      } catch (err) {
        setError(err instanceof Error ? err.message : "Navigation failed");
      } finally {
        setIsLoading(false);
      }
    },
    [selectedNodeId, seedIds, neighborDepth, fetchSubgraph],
  );

  const removeFromView = useCallback(
    (nodeId: string) => {
      const newSeedIds = new Set(seedIds);
      newSeedIds.delete(nodeId);
      setSeedIds(newSeedIds);
      setNodeMap((prev) => {
        const next = new Map(prev);
        next.delete(nodeId);
        return next;
      });
      setEdges((prev) =>
        prev.filter(
          (e) => e.source_node_id !== nodeId && e.target_node_id !== nodeId,
        ),
      );
      setSelectedNodeId((prev) => (prev === nodeId ? null : prev));
    },
    [seedIds],
  );

  const clearView = useCallback(() => {
    setNodeMap(new Map());
    setEdges([]);
    setSeedIds(new Set());
    setSelectedNodeId(null);
    setError(null);
  }, []);

  const ensureNodesInView = useCallback(
    async (nodeIds: string[], extraEdges?: EdgeResponse[]) => {
      // Use functional updater approach: figure out which nodes are missing
      // inside the setState call to avoid stale-closure issues, but we still
      // need the list up-front to make the API call. Read current map via ref.
      const currentMap = nodeMap;
      const missing = nodeIds.filter((id) => !currentMap.has(id));

      // Merge any extra edges the caller provides (e.g. from path response)
      if (extraEdges && extraEdges.length > 0) {
        setEdges((prev) => {
          const existingIds = new Set(prev.map((e) => e.id));
          const newEdges = extraEdges.filter((e) => !existingIds.has(e.id));
          return newEdges.length > 0 ? [...prev, ...newEdges] : prev;
        });
      }

      if (missing.length === 0) return;
      try {
        const subgraph = await api.graph.getSubgraph(missing, 1);
        setNodeMap((prev) => {
          const next = new Map(prev);
          for (const node of subgraph.nodes) {
            next.set(node.id, node);
          }
          return next;
        });
        setEdges((prev) => {
          const existingIds = new Set(prev.map((e) => e.id));
          const newEdges = subgraph.edges.filter(
            (e) => !existingIds.has(e.id),
          );
          return newEdges.length > 0 ? [...prev, ...newEdges] : prev;
        });
      } catch {
        // Non-critical — path highlighting still works for nodes already in view
      }
    },
    [nodeMap],
  );

  return {
    nodes,
    edges,
    seedIds,
    selectedNodeId,
    neighborDepth,
    isLoading,
    error,
    searchAndAdd,
    addSeedById,
    expandNode,
    navigateToNode,
    setNeighborDepth: handleNeighborDepthChange,
    selectNode,
    removeFromView,
    clearView,
    ensureNodesInView,
  };
}
