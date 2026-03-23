"use client";

import { useState, useCallback } from "react";
import type { NodeResponse, EdgeResponse } from "@/types";

export interface GraphState {
  nodes: NodeResponse[];
  edges: EdgeResponse[];
  selectedNodeId: string | null;
  addNode: (node: NodeResponse) => void;
  addEdge: (edge: EdgeResponse) => void;
  setNodes: (nodes: NodeResponse[]) => void;
  setEdges: (edges: EdgeResponse[]) => void;
  selectNode: (nodeId: string | null) => void;
  clearGraph: () => void;
}

export function useGraphState(): GraphState {
  const [nodes, setNodesState] = useState<NodeResponse[]>([]);
  const [edges, setEdgesState] = useState<EdgeResponse[]>([]);
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);

  const addNode = useCallback((node: NodeResponse) => {
    setNodesState((prev) => {
      // Avoid duplicates: replace if same id already exists
      const existing = prev.findIndex((n) => n.id === node.id);
      if (existing >= 0) {
        const updated = [...prev];
        updated[existing] = node;
        return updated;
      }
      return [...prev, node];
    });
  }, []);

  const addEdge = useCallback((edge: EdgeResponse) => {
    setEdgesState((prev) => {
      const existing = prev.findIndex((e) => e.id === edge.id);
      if (existing >= 0) {
        const updated = [...prev];
        updated[existing] = edge;
        return updated;
      }
      return [...prev, edge];
    });
  }, []);

  const setNodes = useCallback((newNodes: NodeResponse[]) => {
    setNodesState(newNodes);
  }, []);

  const setEdges = useCallback((newEdges: EdgeResponse[]) => {
    setEdgesState(newEdges);
  }, []);

  const selectNode = useCallback((nodeId: string | null) => {
    setSelectedNodeId(nodeId);
  }, []);

  const clearGraph = useCallback(() => {
    setNodesState([]);
    setEdgesState([]);
    setSelectedNodeId(null);
  }, []);

  return {
    nodes,
    edges,
    selectedNodeId,
    addNode,
    addEdge,
    setNodes,
    setEdges,
    selectNode,
    clearGraph,
  };
}
