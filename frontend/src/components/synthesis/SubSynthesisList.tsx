"use client";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { BookOpen } from "lucide-react";
import type { SynthesisNodeResponse } from "@/types";

interface SubSynthesisListProps {
  subSyntheses: SynthesisNodeResponse[];
}

export function SubSynthesisList({ subSyntheses }: SubSynthesisListProps) {
  return (
    <Card className="mt-6">
      <CardHeader className="pb-3">
        <CardTitle className="text-[0.68rem] uppercase tracking-[0.1em] font-bold text-muted-foreground">
          Sub-Syntheses ({subSyntheses.length})
        </CardTitle>
      </CardHeader>
      <CardContent>
        <div className="space-y-2">
          {subSyntheses.map((sub) => (
            <a
              key={sub.node_id}
              href={`/investigate/${sub.node_id}`}
              className="flex items-center gap-3 rounded-lg border p-3.5 hover:bg-accent hover:border-ocean/30 transition-all group"
            >
              <BookOpen className="size-4 text-ocean/60 group-hover:text-ocean shrink-0" />
              <span className="font-medium text-sm text-foreground/80 group-hover:text-foreground">
                {sub.concept}
              </span>
              <span className="text-[0.6rem] uppercase tracking-wider font-semibold text-ocean/50 border border-ocean/20 rounded-full px-2 py-0.5 ml-auto shrink-0">
                Synthesis
              </span>
            </a>
          ))}
        </div>
      </CardContent>
    </Card>
  );
}
