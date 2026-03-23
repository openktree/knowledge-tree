import type { EdgeResponse, NodeResponse, TimelineEntry } from "@/types";

export interface TimelineState {
  nodes: NodeResponse[];
  edges: EdgeResponse[];
}

/**
 * Replay timeline entries 0..position into node/edge maps.
 *
 * Pure function — no side effects. Extracted for testability.
 * Position is clamped to [0, entries.length - 1].
 * Returns empty arrays when entries is empty.
 */
export function computeStateAtPosition(
  entries: TimelineEntry[],
  position: number,
): TimelineState {
  if (entries.length === 0) return { nodes: [], edges: [] };

  const clamped = Math.max(0, Math.min(position, entries.length - 1));

  const nodeMap = new Map<string, NodeResponse>();
  const edgeMap = new Map<string, EdgeResponse>();

  for (let i = 0; i <= clamped; i++) {
    const entry = entries[i];
    if (entry.node) {
      nodeMap.set(entry.node.id, entry.node);
    }
    if (entry.edge) {
      edgeMap.set(entry.edge.id, entry.edge);
    }
  }

  return {
    nodes: Array.from(nodeMap.values()),
    edges: Array.from(edgeMap.values()),
  };
}
