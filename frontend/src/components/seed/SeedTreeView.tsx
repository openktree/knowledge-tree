"use client";

import { useRef, useEffect, useMemo, useCallback } from "react";
import { useRouter } from "next/navigation";
import dynamic from "next/dynamic";
import cytoscape from "cytoscape";
import dagre from "cytoscape-dagre";
import { Loader2 } from "lucide-react";
import { useSeedTree } from "@/hooks/useSeedTree";
import type { SeedTreeNode } from "@/types";

// Register dagre layout (client-only)
cytoscape.use(dagre);

const CytoscapeComponent = dynamic(() => import("react-cytoscapejs"), {
  ssr: false,
  loading: () => (
    <div className="flex h-full w-full items-center justify-center text-muted-foreground">
      Loading tree...
    </div>
  ),
});

const STATUS_COLORS: Record<string, string> = {
  active: "#22c55e",
  promoted: "#3b82f6",
  merged: "#eab308",
  ambiguous: "#a855f7",
};

const TYPE_SHAPES: Record<string, string> = {
  entity: "diamond",
  concept: "roundrectangle",
  event: "barrel",
  perspective: "hexagon",
};

function flattenTree(
  node: SeedTreeNode,
  focusKey: string,
): { nodes: cytoscape.ElementDefinition[]; edges: cytoscape.ElementDefinition[] } {
  const nodes: cytoscape.ElementDefinition[] = [];
  const edges: cytoscape.ElementDefinition[] = [];

  function walk(n: SeedTreeNode, parentKey?: string) {
    const size = Math.max(30, Math.min(80, 30 + n.fact_count * 2));
    nodes.push({
      data: {
        id: n.key,
        label: `${n.name}\n(${n.fact_count})`,
        status: n.status,
        nodeType: n.node_type,
        factCount: n.fact_count,
        promotedNodeKey: n.promoted_node_key,
        isFocus: n.key === focusKey,
        size,
      },
    });
    if (parentKey) {
      edges.push({
        data: {
          id: `${parentKey}->${n.key}`,
          source: parentKey,
          target: n.key,
          ambiguityType: n.ambiguity_type ?? "text",
        },
      });
    }
    for (const child of n.children) {
      walk(child, n.key);
    }
  }

  walk(node);
  return { nodes, edges };
}

const stylesheet: cytoscape.StylesheetJsonBlock[] = [
  {
    selector: "node",
    style: {
      label: "data(label)",
      "text-wrap": "wrap",
      "text-valign": "center",
      "text-halign": "center",
      "font-size": "11px",
      width: "data(size)",
      height: "data(size)",
      "background-color": "#6b7280",
      "border-width": 2,
      "border-color": "#374151",
      color: "#e5e7eb",
    },
  },
  // Status colors
  ...Object.entries(STATUS_COLORS).map(([status, color]) => ({
    selector: `node[status="${status}"]`,
    style: { "background-color": color },
  })),
  // Shapes by type
  ...Object.entries(TYPE_SHAPES).map(([type, shape]) => ({
    selector: `node[nodeType="${type}"]`,
    style: { shape: shape as cytoscape.Css.NodeShape },
  })),
  // Focus node highlight
  {
    selector: "node[?isFocus]",
    style: {
      "border-width": 4,
      "border-color": "#f59e0b",
    },
  },
  {
    selector: "edge",
    style: {
      "curve-style": "bezier",
      "target-arrow-shape": "triangle",
      "target-arrow-color": "#6b7280",
      "line-color": "#6b7280",
      width: 2,
    },
  },
  {
    selector: 'edge[ambiguityType="embedding"]',
    style: {
      "line-style": "dashed",
    },
  },
];

interface SeedTreeViewProps {
  seedKey: string;
}

export function SeedTreeView({ seedKey }: SeedTreeViewProps) {
  const { tree, isLoading, error } = useSeedTree(seedKey);
  const cyRef = useRef<cytoscape.Core | null>(null);
  const router = useRouter();

  const elements = useMemo(() => {
    if (!tree) return [];
    const { nodes, edges } = flattenTree(tree.root, tree.focus_key);
    return [...nodes, ...edges];
  }, [tree]);

  const handleCyReady = useCallback(
    (cy: cytoscape.Core) => {
      cyRef.current = cy;
      cy.on("tap", "node", (evt) => {
        const key = evt.target.id();
        if (key && key !== seedKey) {
          router.push(`/seeds/${encodeURIComponent(key)}`);
        }
      });
    },
    [seedKey, router],
  );

  // Re-layout when elements change
  useEffect(() => {
    if (cyRef.current && elements.length > 0) {
      cyRef.current.layout({
        name: "dagre",
        rankDir: "TB",
        nodeSep: 50,
        rankSep: 80,
        animate: true,
        animationDuration: 300,
      } as cytoscape.LayoutOptions).run();
    }
  }, [elements]);

  if (isLoading) {
    return (
      <div className="flex items-center justify-center py-12">
        <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
      </div>
    );
  }

  if (error) {
    return <p className="text-sm text-destructive py-4">{error}</p>;
  }

  if (!tree) {
    return <p className="text-sm text-muted-foreground py-4">No tree data.</p>;
  }

  return (
    <div className="w-full h-[500px] border rounded-lg overflow-hidden bg-background">
      <CytoscapeComponent
        elements={elements}
        stylesheet={stylesheet}
        layout={{
          name: "dagre",
          rankDir: "TB",
          nodeSep: 50,
          rankSep: 80,
        } as cytoscape.LayoutOptions}
        style={{ width: "100%", height: "100%" }}
        cy={handleCyReady}
      />
    </div>
  );
}
