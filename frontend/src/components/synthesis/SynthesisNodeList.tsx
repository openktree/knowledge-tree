"use client";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import type { SynthesisNodeResponse } from "@/types";

interface SynthesisNodeListProps {
  nodes: SynthesisNodeResponse[];
}

const NODE_TYPE_STYLES: Record<string, string> = {
  concept: "border-ocean/20 text-ocean dark:text-ocean-mid bg-ocean-dim/30 dark:bg-ocean-dark/20",
  entity: "border-forest/20 text-forest dark:text-forest-mid bg-forest-dim/30 dark:bg-forest-dark/20",
  event: "border-earth/20 text-earth dark:text-earth-mid bg-earth-dim/30 dark:bg-earth-dark/20",
  perspective: "border-border text-muted-foreground bg-muted",
};

export function SynthesisNodeList({ nodes }: SynthesisNodeListProps) {
  return (
    <Card className="mt-6">
      <CardHeader className="pb-3">
        <CardTitle className="text-[0.68rem] uppercase tracking-[0.1em] font-bold text-muted-foreground">
          Referenced Nodes ({nodes.length})
        </CardTitle>
      </CardHeader>
      <CardContent>
        <div className="flex flex-wrap gap-2">
          {nodes.map((node) => {
            const typeStyle = NODE_TYPE_STYLES[node.node_type] ?? NODE_TYPE_STYLES.concept;
            return (
              <a
                key={node.node_id}
                href={`/nodes/${node.node_id}`}
                className="inline-flex items-center gap-1.5 rounded-full border px-3 py-1 text-xs hover:shadow-sm transition-all hover:scale-[1.02]"
              >
                <span className="font-medium text-foreground/80">{node.concept}</span>
                <span className={`text-[9px] uppercase tracking-wider font-semibold px-1.5 py-0 rounded-full ${typeStyle}`}>
                  {node.node_type}
                </span>
              </a>
            );
          })}
        </div>
      </CardContent>
    </Card>
  );
}
