"use client";

import { useState, useCallback, useMemo } from "react";
import type { PathsResponse, EdgeResponse } from "@/types";
import { api } from "@/lib/api";

export interface UseCompareModeResult {
  /** Whether compare mode is active (source is set). */
  isCompareActive: boolean;
  sourceNodeId: string | null;
  targetNodeId: string | null;
  pathsData: PathsResponse | null;
  isLoadingPaths: boolean;
  pathError: string | null;
  /** null = show all paths, number = specific path index */
  activePathIndex: number | null;
  /** All node IDs on the active (or all) path(s). */
  pathNodeIds: ReadonlySet<string>;
  /** All graph edge IDs that match path edges (matched by source+target node pair). */
  pathEdgeIds: ReadonlySet<string>;
  startCompare: (sourceId: string) => void;
  selectTarget: (targetId: string) => Promise<void>;
  setActivePathIndex: (index: number | null) => void;
  exitCompare: () => void;
}

/**
 * Manages compare/path-finding mode state.
 *
 * @param graphEdges - current edges in the graph view, used to match path
 *   edges (by source+target node pair) to Cytoscape edge IDs.
 */
export function useCompareMode(
  graphEdges: EdgeResponse[],
): UseCompareModeResult {
  const [sourceNodeId, setSourceNodeId] = useState<string | null>(null);
  const [targetNodeId, setTargetNodeId] = useState<string | null>(null);
  const [pathsData, setPathsData] = useState<PathsResponse | null>(null);
  const [isLoadingPaths, setIsLoadingPaths] = useState(false);
  const [pathError, setPathError] = useState<string | null>(null);
  const [activePathIndex, setActivePathIndex] = useState<number | null>(null);

  const isCompareActive = sourceNodeId !== null;

  // Build a lookup from "sourceNodeId|targetNodeId" → graph edge ID
  const edgePairIndex = useMemo(() => {
    const index = new Map<string, string>();
    for (const edge of graphEdges) {
      // Index both directions since the graph may have edges in either direction
      index.set(`${edge.source_node_id}|${edge.target_node_id}`, edge.id);
      index.set(`${edge.target_node_id}|${edge.source_node_id}`, edge.id);
    }
    return index;
  }, [graphEdges]);

  // Derive the set of node IDs and edge IDs on the active path(s)
  const { pathNodeIds, pathEdgeIds } = useMemo(() => {
    const nodeIds = new Set<string>();
    const edgeIds = new Set<string>();

    if (!pathsData || pathsData.paths.length === 0) {
      return { pathNodeIds: nodeIds, pathEdgeIds: edgeIds };
    }

    const paths =
      activePathIndex !== null
        ? [pathsData.paths[activePathIndex]].filter(Boolean)
        : pathsData.paths;

    for (const path of paths) {
      for (const step of path.steps) {
        nodeIds.add(step.node_id);
        if (step.edge) {
          // Match by source+target node pair to find the graph edge ID
          const key = `${step.edge.source_node_id}|${step.edge.target_node_id}`;
          const graphEdgeId = edgePairIndex.get(key);
          if (graphEdgeId) {
            edgeIds.add(graphEdgeId);
          }
        }
      }
    }

    return { pathNodeIds: nodeIds, pathEdgeIds: edgeIds };
  }, [pathsData, activePathIndex, edgePairIndex]);

  const startCompare = useCallback((sourceId: string) => {
    setSourceNodeId(sourceId);
    setTargetNodeId(null);
    setPathsData(null);
    setPathError(null);
    setActivePathIndex(null);
  }, []);

  const selectTarget = useCallback(
    async (targetId: string) => {
      if (!sourceNodeId) return;
      setTargetNodeId(targetId);
      setIsLoadingPaths(true);
      setPathError(null);
      setActivePathIndex(null);
      try {
        const result = await api.graph.getPaths(sourceNodeId, targetId);
        setPathsData(result);
      } catch (err) {
        setPathError(
          err instanceof Error ? err.message : "Failed to find paths",
        );
        setPathsData(null);
      } finally {
        setIsLoadingPaths(false);
      }
    },
    [sourceNodeId],
  );

  const exitCompare = useCallback(() => {
    setSourceNodeId(null);
    setTargetNodeId(null);
    setPathsData(null);
    setPathError(null);
    setActivePathIndex(null);
  }, []);

  return {
    isCompareActive,
    sourceNodeId,
    targetNodeId,
    pathsData,
    isLoadingPaths,
    pathError,
    activePathIndex,
    pathNodeIds,
    pathEdgeIds,
    startCompare,
    selectTarget,
    setActivePathIndex,
    exitCompare,
  };
}
