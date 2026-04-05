"use client";

import { createContext, useCallback, useContext, useEffect, useState } from "react";
import { listGraphs, setActiveGraphSlug } from "@/lib/api";
import { useAuth } from "@/contexts/auth";
import type { GraphResponse } from "@/types";

interface GraphState {
  /** Currently active graph slug */
  activeGraph: string;
  /** All graphs the user can access */
  graphs: GraphResponse[];
  /** Whether graphs are still loading */
  loading: boolean;
  /** Switch to a different graph */
  setActiveGraph: (slug: string) => void;
  /** Refresh the graph list */
  refreshGraphs: () => Promise<void>;
  /** The active graph object (null during loading or if not found) */
  activeGraphInfo: GraphResponse | null;
}

const GraphContext = createContext<GraphState | null>(null);

export function GraphProvider({ children }: { children: React.ReactNode }) {
  const { user, loading: authLoading } = useAuth();
  const [activeGraph, setActiveGraphState] = useState<string>("default");
  const [graphs, setGraphs] = useState<GraphResponse[]>([]);
  const [loading, setLoading] = useState(true);

  // Read persisted graph from localStorage on mount and sync to api module
  useEffect(() => {
    const stored = localStorage.getItem("active_graph");
    if (stored) {
      setActiveGraphState(stored);
      setActiveGraphSlug(stored);
    }
  }, []);

  const refreshGraphs = useCallback(async () => {
    try {
      const data = await listGraphs();
      setGraphs(data);

      // Validate stored graph slug still exists; reset to default if deleted
      const stored = localStorage.getItem("active_graph");
      if (stored && stored !== "default" && !data.some((g) => g.slug === stored)) {
        setActiveGraphState("default");
        setActiveGraphSlug("default");
        localStorage.setItem("active_graph", "default");
      }
    } catch (err) {
      console.error("Failed to load graphs:", err);
    } finally {
      setLoading(false);
    }
  }, []);

  // Wait for auth to complete before fetching graphs
  useEffect(() => {
    if (!authLoading && user) {
      refreshGraphs();
    } else if (!authLoading && !user) {
      // Not logged in — reset to defaults
      setGraphs([]);
      setLoading(false);
    }
  }, [authLoading, user, refreshGraphs]);

  const setActiveGraph = useCallback(
    (slug: string) => {
      setActiveGraphState(slug);
      setActiveGraphSlug(slug);
      localStorage.setItem("active_graph", slug);
    },
    [],
  );

  const activeGraphInfo = graphs.find((g) => g.slug === activeGraph) ?? null;

  return (
    <GraphContext.Provider
      value={{
        activeGraph,
        graphs,
        loading,
        setActiveGraph,
        refreshGraphs,
        activeGraphInfo,
      }}
    >
      {children}
    </GraphContext.Provider>
  );
}

export function useGraph(): GraphState {
  const ctx = useContext(GraphContext);
  if (!ctx) throw new Error("useGraph must be used within GraphProvider");
  return ctx;
}
