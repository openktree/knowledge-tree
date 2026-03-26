"use client";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import type { SynthesisNodeResponse } from "@/types";

interface SubSynthesisListProps {
  subSyntheses: SynthesisNodeResponse[];
}

export function SubSynthesisList({ subSyntheses }: SubSynthesisListProps) {
  return (
    <Card className="mt-4">
      <CardHeader>
        <CardTitle className="text-sm">
          Sub-Syntheses ({subSyntheses.length})
        </CardTitle>
      </CardHeader>
      <CardContent>
        <div className="space-y-2">
          {subSyntheses.map((sub) => (
            <a
              key={sub.node_id}
              href={`/syntheses/${sub.node_id}`}
              className="flex items-center gap-2 rounded-md border p-3 hover:bg-accent transition-colors"
            >
              <span className="font-medium text-sm">{sub.concept}</span>
              <Badge variant="outline" className="text-[10px]">
                Synthesis
              </Badge>
            </a>
          ))}
        </div>
      </CardContent>
    </Card>
  );
}
