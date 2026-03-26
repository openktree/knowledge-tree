"use client";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import type { SynthesisNodeResponse } from "@/types";

interface SynthesisNodeListProps {
  nodes: SynthesisNodeResponse[];
}

export function SynthesisNodeList({ nodes }: SynthesisNodeListProps) {
  return (
    <Card className="mt-4">
      <CardHeader>
        <CardTitle className="text-sm">
          Referenced Nodes ({nodes.length})
        </CardTitle>
      </CardHeader>
      <CardContent>
        <div className="flex flex-wrap gap-2">
          {nodes.map((node) => (
            <a
              key={node.node_id}
              href={`/nodes/${node.node_id}`}
              className="inline-flex items-center gap-1.5 rounded-md border px-2.5 py-1 text-xs hover:bg-accent transition-colors"
            >
              <span className="font-medium">{node.concept}</span>
              <Badge variant="outline" className="text-[9px] px-1 py-0">
                {node.node_type}
              </Badge>
            </a>
          ))}
        </div>
      </CardContent>
    </Card>
  );
}
